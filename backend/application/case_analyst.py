"""Application: Case Analyst — ACCEPTED EvidenceItem(version) → Fact CANDIDATE.

AI never confirms facts. Issues/LegalTheories stay proposal-layer only.
Does not create ClaimDirection, DocumentDraft, or advance past N6.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.domain.errors import NotFoundError, ValidationError
from backend.domain.services import DomainService
from backend.models import (
    CaseMaterial,
    CaseParty,
    EvidenceItem,
    ExtractedContent,
    Fact,
    Issue,
    LegalTheory,
    NodeRun,
    SkillExecution,
    SourceSpan,
    TimelineEvent,
    WorkflowInstance,
)
from backend.schemas.case_analyst import (
    AnalystEngineResult,
    AnalystInput,
    EvidenceRef,
    FactProposal,
)
from backend.skills.case_analyst import (
    CaseAnalystEngine,
    DeterministicCaseAnalystStub,
    EvidenceView,
    SpanSnippet,
    looks_like_legal_conclusion,
)
from backend.workflow.runtime import WorkflowRuntime


def _now() -> datetime:
    return datetime.now(UTC)


def _normalize_statement(statement: str) -> str:
    text = statement.strip().lower()
    text = re.sub(r"\s+", "", text)
    text = text.replace("，", ",").replace("。", ".").replace("：", ":")
    return text


def _ref_key(ref: EvidenceRef) -> tuple[UUID, int]:
    return (ref.evidence_item_id, ref.evidence_item_version)


def _ref_set(refs: list[EvidenceRef]) -> frozenset[tuple[UUID, int]]:
    return frozenset(_ref_key(r) for r in refs)


@dataclass
class AnalystApplyResult:
    created_facts: list[Fact] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    rejected_proposals: list[dict[str, Any]] = field(default_factory=list)
    confirmed_amendment_proposals: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    missing_evidence: list[dict[str, Any]] = field(default_factory=list)
    issue_ids: list[UUID] = field(default_factory=list)
    legal_theory_ids: list[UUID] = field(default_factory=list)
    timeline_ids: list[UUID] = field(default_factory=list)
    fact_keys: list[UUID] = field(default_factory=list)
    skill_execution_id: UUID | None = None


class CaseAnalystService:
    def __init__(
        self,
        session: Session,
        *,
        engine: CaseAnalystEngine | None = None,
    ) -> None:
        self.session = session
        self.domain = DomainService(session)
        self.engine: CaseAnalystEngine = engine or DeterministicCaseAnalystStub()

    def analyze(
        self,
        *,
        case_id: UUID,
        accepted_evidence_refs: list[dict[str, Any] | EvidenceRef],
        actor_id: UUID,
        node_run_id: UUID | None = None,
        raw_result: dict[str, Any] | AnalystEngineResult | None = None,
    ) -> AnalystApplyResult:
        self.domain._require_case(case_id)  # noqa: SLF001
        refs = self._parse_input_refs(accepted_evidence_refs)
        # Hard fail: any illegal input ref aborts the whole call.
        views = self._validate_and_build_views(case_id, refs)
        scope = _ref_set(refs)

        started = _now()
        skill_exec = None
        if node_run_id is not None:
            skill_exec = self._start_skill_execution(
                node_run_id=node_run_id,
                case_id=case_id,
                refs=refs,
                started_at=started,
            )

        if raw_result is None:
            inp = AnalystInput(case_id=case_id, accepted_evidence_refs=refs)
            engine_result = self.engine.analyze(inp, views)
        elif isinstance(raw_result, AnalystEngineResult):
            engine_result = raw_result
        else:
            engine_result = AnalystEngineResult.model_validate(raw_result)

        result = AnalystApplyResult()
        result.conflicts = [c.model_dump(mode="json") for c in engine_result.conflicts]
        result.missing_evidence = [
            m.model_dump(mode="json") for m in engine_result.missing_evidence
        ]

        for proposal in engine_result.facts:
            try:
                self._apply_fact_proposal(
                    case_id=case_id,
                    proposal=proposal,
                    scope=scope,
                    actor_id=actor_id,
                    analyst_run_id=skill_exec.id if skill_exec else None,
                    result=result,
                )
            except ValidationError as exc:
                result.rejected_proposals.append(
                    {
                        "proposal_id": str(proposal.proposal_id),
                        "channel": "facts",
                        "error": exc.message,
                        "code": exc.code,
                    }
                )

        # Issues / LegalTheories: proposal layer only — never propose_fact.
        for issue in engine_result.issues:
            row = Issue(
                case_id=case_id,
                statement=issue.statement,
                order_index=len(result.issue_ids),
                layer="CANDIDATE",
                related_fact_ids=None,
                analyst_run_id=skill_exec.id if skill_exec else None,
            )
            self.session.add(row)
            self.session.flush()
            result.issue_ids.append(row.id)

        for theory in engine_result.legal_theories:
            row = LegalTheory(
                case_id=case_id,
                theory_summary=theory.theory_summary,
                norms_suggested_json=None,
                supporting_fact_ids=None,
                layer="CANDIDATE",
                analyst_run_id=skill_exec.id if skill_exec else None,
            )
            self.session.add(row)
            self.session.flush()
            result.legal_theory_ids.append(row.id)

        if skill_exec is not None:
            self._finish_skill_execution(skill_exec, result=result, finished_at=_now())
            result.skill_execution_id = skill_exec.id

        return result

    def is_party_gate_complete(self, case_id: UUID) -> bool:
        """N5: every current CaseParty must be CONFIRMED (and at least one exists)."""
        parties = list(
            self.session.scalars(
                select(CaseParty).where(
                    CaseParty.case_id == case_id,
                    CaseParty.is_current.is_(True),
                )
            )
        )
        if not parties:
            return False
        return all(p.layer == "CONFIRMED" for p in parties)

    def is_fact_gate_complete(self, *, fact_keys: list[UUID]) -> bool:
        """N6: listed Facts must be CONFIRMED or REJECTED (no CANDIDATE)."""
        if not fact_keys:
            return True
        for key in fact_keys:
            fact = self.domain.repo.get_current_fact(key)
            if fact is None:
                raise NotFoundError(f"fact not found: {key}")
            if fact.status == "CANDIDATE":
                return False
            if fact.status not in {"CONFIRMED", "REJECTED"}:
                return False
        return True

    def run_n4_analyze(
        self,
        *,
        instance_id: UUID,
        node_run_id: UUID,
        accepted_evidence_refs: list[dict[str, Any] | EvidenceRef],
        actor_id: UUID,
        auto_complete: bool = True,
    ) -> AnalystApplyResult:
        runtime = WorkflowRuntime(self.session)
        instance = runtime.get_instance(instance_id)
        node_run = runtime.get_node_run(node_run_id)
        if node_run.instance_id != instance.id:
            raise ValidationError("node_run does not belong to instance")

        result = self.analyze(
            case_id=instance.case_id,
            accepted_evidence_refs=accepted_evidence_refs,
            actor_id=actor_id,
            node_run_id=node_run_id,
        )

        ctx = dict(instance.context_json or {})
        ctx["analyst"] = {
            "fact_keys": [str(k) for k in result.fact_keys],
            "created_fact_ids": [str(f.id) for f in result.created_facts],
            "skipped": result.skipped,
            "rejected_proposals": result.rejected_proposals,
            "confirmed_amendment_proposals": result.confirmed_amendment_proposals,
            "conflicts": result.conflicts,
            "missing_evidence": result.missing_evidence,
            "issue_ids": [str(x) for x in result.issue_ids],
            "legal_theory_ids": [str(x) for x in result.legal_theory_ids],
            "timeline_ids": [str(x) for x in result.timeline_ids],
            "skill_execution_id": str(result.skill_execution_id)
            if result.skill_execution_id
            else None,
            "accepted_evidence_refs": [
                {
                    "evidence_item_id": str(r.evidence_item_id),
                    "evidence_item_version": r.evidence_item_version,
                }
                for r in self._parse_input_refs(accepted_evidence_refs)
            ],
        }
        instance.context_json = ctx
        self.session.flush()

        if auto_complete:
            # N4 complete → Runtime advances to N5 human gate → WAITING_USER
            runtime.complete_node(
                node_run_id,
                output_ref=f"analyst:{result.skill_execution_id or 'no-skill'}",
            )
        return result

    def assert_n5_ready_to_complete(self, instance_id: UUID) -> None:
        instance = self.session.get(WorkflowInstance, instance_id)
        if instance is None:
            raise NotFoundError("workflow instance not found")
        if not self.is_party_gate_complete(instance.case_id):
            raise ValidationError(
                "N5 party gate incomplete: parties missing or not CONFIRMED"
            )

    def complete_n5_confirm_parties(
        self,
        *,
        instance_id: UUID,
        node_run_id: UUID,
        auto_advance: bool = True,
    ):
        """Complete N5 after parties are CONFIRMED. Default advances to N6 gate."""
        self.assert_n5_ready_to_complete(instance_id)
        runtime = WorkflowRuntime(self.session)
        return runtime.complete_node(node_run_id, auto_advance=auto_advance)

    def assert_n6_ready_to_complete(self, instance_id: UUID) -> None:
        instance = self.session.get(WorkflowInstance, instance_id)
        if instance is None:
            raise NotFoundError("workflow instance not found")
        analyst = (instance.context_json or {}).get("analyst") or {}
        keys = [UUID(str(x)) for x in analyst.get("fact_keys") or []]
        if not self.is_fact_gate_complete(fact_keys=keys):
            raise ValidationError(
                "N6 fact gate incomplete: CANDIDATE facts remain"
            )

    def complete_n6_confirm_facts(
        self,
        *,
        instance_id: UUID,
        node_run_id: UUID,
        auto_advance: bool = False,
    ):
        """Complete N6 only when analyst Fact Candidates are CONFIRMED/REJECTED.

        Phase 6 default: auto_advance=False so N7_CONFIRM_CLAIMS is not started.
        """
        self.assert_n6_ready_to_complete(instance_id)
        runtime = WorkflowRuntime(self.session)
        return runtime.complete_node(node_run_id, auto_advance=auto_advance)

    # ----- internals -----

    def _parse_input_refs(
        self, accepted_evidence_refs: list[dict[str, Any] | EvidenceRef]
    ) -> list[EvidenceRef]:
        if not accepted_evidence_refs:
            raise ValidationError(
                "accepted_evidence_refs required; no implicit evidence selection"
            )
        refs: list[EvidenceRef] = []
        for raw in accepted_evidence_refs:
            if isinstance(raw, EvidenceRef):
                refs.append(raw)
                continue
            if not isinstance(raw, dict):
                raise ValidationError("evidence ref must be an object")
            if "evidence_item_version" not in raw or raw.get("evidence_item_version") is None:
                raise ValidationError(
                    "evidence_item_version required; "
                    "implicit current/latest/max(version) is forbidden"
                )
            if "evidence_item_id" not in raw:
                raise ValidationError("evidence_item_id required")
            try:
                refs.append(EvidenceRef.model_validate(raw))
            except Exception as exc:  # noqa: BLE001
                raise ValidationError(f"invalid evidence ref: {exc}") from exc
        return refs

    def _validate_and_build_views(
        self, case_id: UUID, refs: list[EvidenceRef]
    ) -> list[EvidenceView]:
        views: list[EvidenceView] = []
        for ref in refs:
            item = self.domain.repo.get_evidence_version(
                ref.evidence_item_id, ref.evidence_item_version
            )
            if item is None:
                # Distinguish missing id vs missing version
                any_ver = self.session.scalars(
                    select(EvidenceItem).where(EvidenceItem.id == ref.evidence_item_id)
                ).first()
                if any_ver is None:
                    raise ValidationError(
                        f"evidence item not found: {ref.evidence_item_id}"
                    )
                raise ValidationError(
                    f"evidence version not found: "
                    f"{ref.evidence_item_id}@v{ref.evidence_item_version}"
                )
            if item.case_id != case_id:
                raise ValidationError(
                    f"cross-case evidence rejected: {ref.evidence_item_id}"
                )
            if item.acceptance == "PENDING":
                raise ValidationError(
                    f"PENDING evidence cannot be analyzed: {ref.evidence_item_id}"
                )
            if item.acceptance == "EXCLUDED":
                raise ValidationError(
                    f"EXCLUDED evidence cannot be analyzed: {ref.evidence_item_id}"
                )
            if item.acceptance != "ACCEPTED":
                raise ValidationError(
                    f"evidence must be ACCEPTED: {ref.evidence_item_id} "
                    f"(acceptance={item.acceptance})"
                )

            spans = self._load_provenance_spans(case_id, item)
            if not spans:
                raise ValidationError(
                    f"evidence provenance incomplete or untrusted: "
                    f"{ref.evidence_item_id}@v{ref.evidence_item_version}"
                )
            views.append(
                EvidenceView(
                    evidence_item_id=item.id,
                    evidence_item_version=item.version,
                    title=item.title,
                    summary=item.summary,
                    category=item.category,
                    source_spans=spans,
                )
            )
        return views

    def _load_provenance_spans(
        self, case_id: UUID, item: EvidenceItem
    ) -> list[SpanSnippet]:
        links = self.domain.repo.list_evidence_spans(item.id, item.version)
        if not links:
            return []
        snippets: list[SpanSnippet] = []
        for link in links:
            span = self.session.get(SourceSpan, link.source_span_id)
            if span is None:
                raise ValidationError(
                    f"broken provenance: source_span missing for evidence "
                    f"{item.id}@v{item.version}"
                )
            material = self.session.get(CaseMaterial, span.material_id)
            if material is None or material.case_id != case_id:
                raise ValidationError(
                    f"broken provenance: material/case mismatch for span {span.id}"
                )
            ec = self.session.get(ExtractedContent, span.extracted_content_id)
            if ec is None or ec.status != "SUCCEEDED":
                raise ValidationError(
                    f"broken provenance: ExtractedContent not SUCCEEDED for span {span.id}"
                )
            snippets.append(
                SpanSnippet(
                    source_span_id=span.id,
                    quote=span.quote,
                    page=span.page,
                    paragraph=span.paragraph,
                    material_id=span.material_id,
                )
            )
        return snippets

    def _apply_fact_proposal(
        self,
        *,
        case_id: UUID,
        proposal: FactProposal,
        scope: frozenset[tuple[UUID, int]],
        actor_id: UUID,
        analyst_run_id: UUID | None,
        result: AnalystApplyResult,
    ) -> None:
        if not proposal.supporting_evidence_refs:
            raise ValidationError("fact proposal missing supporting_evidence_refs")
        if looks_like_legal_conclusion(proposal.statement):
            raise ValidationError(
                "legal conclusion cannot enter Fact channel: "
                f"{proposal.statement[:80]}"
            )
        for ref in proposal.supporting_evidence_refs:
            if _ref_key(ref) not in scope:
                raise ValidationError(
                    f"fact evidence ref outside analyst input scope: "
                    f"{ref.evidence_item_id}@v{ref.evidence_item_version}"
                )
            # Existence already validated for scope; re-check version row.
            if (
                self.domain.repo.get_evidence_version(
                    ref.evidence_item_id, ref.evidence_item_version
                )
                is None
            ):
                raise ValidationError(
                    f"fact references nonexistent evidence: "
                    f"{ref.evidence_item_id}@v{ref.evidence_item_version}"
                )

        fingerprint = (
            _normalize_statement(proposal.statement),
            proposal.fact_type,
            _ref_set(proposal.supporting_evidence_refs),
        )
        existing = self._find_matching_fact(case_id, fingerprint)
        if existing is not None:
            result.skipped.append(
                {
                    "reason": "duplicate",
                    "fact_key": str(existing.fact_key),
                    "version": existing.version,
                    "status": existing.status,
                    "proposal_id": str(proposal.proposal_id),
                }
            )
            result.fact_keys.append(existing.fact_key)
            return

        # Same evidence set + type as CONFIRMED with different statement:
        # never amend CONFIRMED via AI — emit amendment proposal candidate only.
        confirmed_peer = self._find_confirmed_peer(
            case_id,
            fact_type=proposal.fact_type,
            refs=_ref_set(proposal.supporting_evidence_refs),
        )
        if confirmed_peer is not None and _normalize_statement(
            confirmed_peer.statement
        ) != fingerprint[0]:
            # Create a separate CANDIDATE for lawyer review (new fact_key).
            fact = self._create_candidate(case_id, proposal, actor_id, analyst_run_id)
            result.created_facts.append(fact)
            result.fact_keys.append(fact.fact_key)
            result.confirmed_amendment_proposals.append(
                {
                    "proposal_id": str(proposal.proposal_id),
                    "new_fact_key": str(fact.fact_key),
                    "targets_confirmed_fact_key": str(confirmed_peer.fact_key),
                    "targets_version": confirmed_peer.version,
                    "note": (
                        "AI must not amend CONFIRMED facts; "
                        "lawyer may amend via Domain Service"
                    ),
                }
            )
            self._maybe_timeline(case_id, fact, proposal, analyst_run_id, result)
            return

        # Same evidence+type as another CANDIDATE with different statement:
        # uncertain identity → do not merge; create new CANDIDATE.
        fact = self._create_candidate(case_id, proposal, actor_id, analyst_run_id)
        if fact.status != "CANDIDATE":
            raise ValidationError("analyst must create Fact as CANDIDATE only")
        result.created_facts.append(fact)
        result.fact_keys.append(fact.fact_key)
        self._maybe_timeline(case_id, fact, proposal, analyst_run_id, result)

    def _create_candidate(
        self,
        case_id: UUID,
        proposal: FactProposal,
        actor_id: UUID,
        analyst_run_id: UUID | None,
    ) -> Fact:
        links = [
            {
                "evidence_item_id": r.evidence_item_id,
                "evidence_item_version": r.evidence_item_version,
            }
            for r in proposal.supporting_evidence_refs
        ]
        return self.domain.propose_fact(
            case_id=case_id,
            statement=proposal.statement,
            evidence_links=links,
            actor_id=actor_id,
            analyst_run_id=analyst_run_id,
        )

    def _maybe_timeline(
        self,
        case_id: UUID,
        fact: Fact,
        proposal: FactProposal,
        analyst_run_id: UUID | None,
        result: AnalystApplyResult,
    ) -> None:
        if proposal.occurred_at is None:
            return
        event_time: datetime | None
        if isinstance(proposal.occurred_at, datetime):
            event_time = proposal.occurred_at
        elif isinstance(proposal.occurred_at, date):
            event_time = datetime(
                proposal.occurred_at.year,
                proposal.occurred_at.month,
                proposal.occurred_at.day,
                tzinfo=UTC,
            )
        else:
            event_time = None
        precision = proposal.precision or ("DAY" if event_time else "UNKNOWN")
        row = TimelineEvent(
            case_id=case_id,
            event_time=event_time,
            time_precision=precision,
            description=proposal.statement,
            layer="CANDIDATE",
            fact_key=fact.fact_key,
            evidence_item_id=proposal.supporting_evidence_refs[0].evidence_item_id,
            related_fact_ids=[str(fact.fact_key)],
            analyst_run_id=analyst_run_id,
        )
        self.session.add(row)
        self.session.flush()
        result.timeline_ids.append(row.id)

    def _find_matching_fact(
        self,
        case_id: UUID,
        fingerprint: tuple[str, str, frozenset[tuple[UUID, int]]],
    ) -> Fact | None:
        norm, fact_type, refs = fingerprint
        _ = fact_type  # fact_type is DTO-only; match via statement + evidence set
        currents = self.session.scalars(
            select(Fact).where(
                Fact.case_id == case_id,
                Fact.is_current.is_(True),
                Fact.status.in_(["CANDIDATE", "CONFIRMED"]),
            )
        ).all()
        for fact in currents:
            if _normalize_statement(fact.statement) != norm:
                continue
            links = self.domain.repo.list_fact_links(fact.id)
            active = {
                (lnk.evidence_item_id, lnk.evidence_item_version)
                for lnk in links
                if lnk.status == "ACTIVE"
            }
            if active == refs:
                return fact
        return None

    def _find_confirmed_peer(
        self,
        case_id: UUID,
        *,
        fact_type: str,
        refs: frozenset[tuple[UUID, int]],
    ) -> Fact | None:
        _ = fact_type
        currents = self.session.scalars(
            select(Fact).where(
                Fact.case_id == case_id,
                Fact.is_current.is_(True),
                Fact.status == "CONFIRMED",
            )
        ).all()
        for fact in currents:
            links = self.domain.repo.list_fact_links(fact.id)
            active = {
                (lnk.evidence_item_id, lnk.evidence_item_version)
                for lnk in links
                if lnk.status == "ACTIVE"
            }
            if active == refs:
                return fact
        return None

    def _start_skill_execution(
        self,
        *,
        node_run_id: UUID,
        case_id: UUID,
        refs: list[EvidenceRef],
        started_at: datetime,
    ) -> SkillExecution:
        node_run = self.session.get(NodeRun, node_run_id)
        if node_run is None:
            raise NotFoundError("node_run not found")
        existing = self.session.scalars(
            select(SkillExecution).where(SkillExecution.node_run_id == node_run_id)
        ).first()
        if existing is not None:
            raise ValidationError("SkillExecution already exists for this NodeRun")
        exec_row = SkillExecution(
            node_run_id=node_run_id,
            skill_code="CaseAnalystSkill",
            status="RUNNING",
            metrics_json={
                "input": {
                    "case_id": str(case_id),
                    "workflow_instance_id": str(node_run.instance_id),
                    "accepted_evidence_refs": [
                        {
                            "evidence_item_id": str(r.evidence_item_id),
                            "evidence_item_version": r.evidence_item_version,
                        }
                        for r in refs
                    ],
                },
                "started_at": started_at.isoformat(),
            },
        )
        self.session.add(exec_row)
        self.session.flush()
        return exec_row

    def _finish_skill_execution(
        self,
        exec_row: SkillExecution,
        *,
        result: AnalystApplyResult,
        finished_at: datetime,
    ) -> None:
        metrics = dict(exec_row.metrics_json or {})
        metrics["finished_at"] = finished_at.isoformat()
        metrics["output"] = {
            "created_facts": [
                {
                    "fact_key": str(f.fact_key),
                    "id": str(f.id),
                    "version": f.version,
                    "status": f.status,
                }
                for f in result.created_facts
            ],
            "skipped": result.skipped,
            "rejected_proposals": result.rejected_proposals,
            "confirmed_amendment_proposals": result.confirmed_amendment_proposals,
            "conflicts": result.conflicts,
            "missing_evidence": result.missing_evidence,
            "issue_ids": [str(x) for x in result.issue_ids],
            "legal_theory_ids": [str(x) for x in result.legal_theory_ids],
            "timeline_ids": [str(x) for x in result.timeline_ids],
        }
        exec_row.metrics_json = metrics
        if result.rejected_proposals and not result.created_facts and not result.skipped:
            exec_row.status = "FAILED"
            exec_row.error_code = "ALL_FACT_PROPOSALS_REJECTED"
            exec_row.error_detail = "all fact proposals failed validation"
        else:
            exec_row.status = "SUCCEEDED"
        self.session.flush()
