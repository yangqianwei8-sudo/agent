"""Application: Evidence Organizer — SourceSpan → PENDING EvidenceItem.

Does not create Facts, LegalTheory, ClaimDirection, or Drafts.
Does not auto-ACCEPT evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.domain.errors import NotFoundError, ValidationError
from backend.domain.services import DomainService
from backend.models import (
    CaseMaterial,
    EvidenceItem,
    EvidenceItemSpan,
    ExtractedContent,
    NodeRun,
    SkillExecution,
    SourceSpan,
    WorkflowInstance,
)
from backend.schemas.evidence_proposal import EvidenceItemProposal, OrganizerInput
from backend.skills.evidence_organizer import (
    DeterministicOrganizerStub,
    OrganizerEngine,
    SpanView,
)
from backend.workflow.runtime import WorkflowRuntime


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class OrganizerApplyResult:
    created: list[EvidenceItem] = field(default_factory=list)
    amended: list[EvidenceItem] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    failed_proposals: list[dict[str, Any]] = field(default_factory=list)
    skill_execution_id: UUID | None = None
    evidence_item_ids: list[UUID] = field(default_factory=list)


class EvidenceOrganizerService:
    def __init__(
        self,
        session: Session,
        *,
        engine: OrganizerEngine | None = None,
    ) -> None:
        self.session = session
        self.domain = DomainService(session)
        self.engine: OrganizerEngine = engine or DeterministicOrganizerStub()

    def resolve_extracted_content_ids(
        self,
        *,
        case_id: UUID,
        extracted_content_ids: list[UUID] | None = None,
        material_ids: list[UUID] | None = None,
    ) -> list[UUID]:
        """Resolve Organizer scope. Never auto-picks latest EC when a material has multiple."""
        if extracted_content_ids:
            return list(extracted_content_ids)
        if not material_ids:
            raise ValidationError(
                "extracted_content_ids or material_ids required; no implicit EC scan"
            )
        resolved: list[UUID] = []
        for mid in material_ids:
            material = self.session.get(CaseMaterial, mid)
            if material is None or material.case_id != case_id:
                raise ValidationError(f"material {mid} does not belong to case {case_id}")
            ecs = self.session.scalars(
                select(ExtractedContent).where(ExtractedContent.material_id == mid)
            ).all()
            if len(ecs) == 0:
                raise ValidationError(f"material {mid} has no ExtractedContent")
            if len(ecs) > 1:
                raise ValidationError(
                    f"material {mid} has multiple ExtractedContent; "
                    "pass extracted_content_ids explicitly (no auto latest)"
                )
            resolved.append(ecs[0].id)
        return resolved

    def organize(
        self,
        *,
        case_id: UUID,
        extracted_content_ids: list[UUID] | None = None,
        material_ids: list[UUID] | None = None,
        actor_id: UUID,
        node_run_id: UUID | None = None,
        raw_proposals: list[dict[str, Any]] | None = None,
    ) -> OrganizerApplyResult:
        ec_ids = self.resolve_extracted_content_ids(
            case_id=case_id,
            extracted_content_ids=extracted_content_ids,
            material_ids=material_ids,
        )
        if not ec_ids:
            raise ValidationError("extracted_content_ids required; no implicit EC selection")

        self.domain._require_case(case_id)  # noqa: SLF001 — shared case gate
        ecs = self._load_and_validate_ecs(case_id, ec_ids)
        spans = self._load_trusted_spans(case_id, ecs)
        span_by_id = {s.span_id: s for s in spans}

        started = _now()
        skill_exec = None
        if node_run_id is not None:
            skill_exec = self._start_skill_execution(
                node_run_id=node_run_id,
                case_id=case_id,
                extracted_content_ids=ec_ids,
                started_at=started,
            )

        result = OrganizerApplyResult()
        next_number = self._next_evidence_number(case_id)

        if raw_proposals is not None:
            proposals = self._parse_raw_proposals(raw_proposals, result)
        else:
            inp = OrganizerInput(case_id=case_id, extracted_content_ids=ec_ids)
            proposals = list(self.engine.organize(inp, spans).proposals)

        for proposal in proposals:
            try:
                self._validate_proposal_spans(proposal, case_id, span_by_id, ec_ids)
            except ValidationError as exc:
                result.failed_proposals.append(
                    {
                        "proposal_id": str(proposal.proposal_id),
                        "error": exc.message,
                        "code": exc.code,
                    }
                )
                continue

            span_set = frozenset(proposal.source_span_ids)
            existing = self._find_current_by_span_set(case_id, span_set)
            if existing is not None:
                if self._content_equal(existing, proposal):
                    result.skipped.append(
                        {
                            "reason": "duplicate",
                            "evidence_item_id": str(existing.id),
                            "version": existing.version,
                            "proposal_id": str(proposal.proposal_id),
                        }
                    )
                    result.evidence_item_ids.append(existing.id)
                    continue
                amended = self.domain.amend_evidence_item(
                    existing.id,
                    actor_id=actor_id,
                    title=proposal.title,
                    summary=proposal.summary,
                    category=proposal.category,
                    source_span_ids=list(proposal.source_span_ids),
                )
                if skill_exec is not None:
                    amended.organizer_run_id = skill_exec.id
                result.amended.append(amended)
                result.evidence_item_ids.append(amended.id)
                continue

            created = self.domain.create_evidence_item(
                case_id=case_id,
                number=str(next_number),
                title=proposal.title,
                category=proposal.category,
                summary=proposal.summary,
                source_span_ids=list(proposal.source_span_ids),
                actor_id=actor_id,
                organizer_run_id=skill_exec.id if skill_exec else None,
            )
            if created.acceptance != "PENDING":
                raise ValidationError("organizer must create EvidenceItem as PENDING only")
            next_number += 1
            result.created.append(created)
            result.evidence_item_ids.append(created.id)

        if skill_exec is not None:
            self._finish_skill_execution(skill_exec, result=result, finished_at=_now())
            result.skill_execution_id = skill_exec.id

        return result

    def _parse_raw_proposals(
        self,
        raw_proposals: list[dict[str, Any]],
        result: OrganizerApplyResult,
    ) -> list[EvidenceItemProposal]:
        """Parse raw proposal JSON; reject filename/quote-only without span ids."""
        parsed: list[EvidenceItemProposal] = []
        for raw in raw_proposals:
            try:
                if "source_span_ids" not in raw or not raw.get("source_span_ids"):
                    raise ValidationError(
                        "proposal missing source_span_ids "
                        "(filename/quote alone is not accepted)"
                    )
                parsed.append(EvidenceItemProposal.model_validate(raw))
            except ValidationError as exc:
                result.failed_proposals.append(
                    {
                        "proposal_id": str(raw.get("proposal_id", "")),
                        "error": exc.message,
                        "code": exc.code,
                    }
                )
            except Exception as exc:  # noqa: BLE001 — schema errors from pydantic
                result.failed_proposals.append(
                    {
                        "proposal_id": str(raw.get("proposal_id", "")),
                        "error": str(exc),
                        "code": "PROPOSAL_SCHEMA_INVALID",
                    }
                )
        return parsed

    def is_confirmation_gate_complete(
        self,
        *,
        evidence_item_ids: list[UUID],
    ) -> bool:
        """N3: all listed current EvidenceItems must be ACCEPTED or EXCLUDED."""
        if not evidence_item_ids:
            return True
        for eid in evidence_item_ids:
            item = self.domain.repo.get_current_evidence(eid)
            if item is None:
                raise NotFoundError(f"evidence item not found: {eid}")
            if item.acceptance == "PENDING":
                return False
            if item.acceptance not in {"ACCEPTED", "EXCLUDED"}:
                return False
        return True

    def run_n2_organize(
        self,
        *,
        instance_id: UUID,
        node_run_id: UUID,
        extracted_content_ids: list[UUID],
        actor_id: UUID,
        auto_complete: bool = True,
    ) -> OrganizerApplyResult:
        """Execute Evidence Organizer for N2_ORGANIZE and optionally complete the NodeRun."""
        runtime = WorkflowRuntime(self.session)
        instance = runtime.get_instance(instance_id)
        node_run = runtime.get_node_run(node_run_id)
        if node_run.instance_id != instance.id:
            raise ValidationError("node_run does not belong to instance")

        result = self.organize(
            case_id=instance.case_id,
            extracted_content_ids=extracted_content_ids,
            actor_id=actor_id,
            node_run_id=node_run_id,
        )

        ctx = dict(instance.context_json or {})
        ctx["organizer"] = {
            "evidence_item_ids": [str(x) for x in result.evidence_item_ids],
            "created": [str(x.id) for x in result.created],
            "amended": [str(x.id) for x in result.amended],
            "skipped": result.skipped,
            "failed_proposals": result.failed_proposals,
            "skill_execution_id": str(result.skill_execution_id)
            if result.skill_execution_id
            else None,
        }
        instance.context_json = ctx
        self.session.flush()

        if auto_complete:
            # Complete N2 → Runtime advances to N3 gate → WAITING_USER
            runtime.complete_node(
                node_run_id,
                output_ref=f"organizer:{result.skill_execution_id or 'no-skill'}",
            )
        return result

    def assert_n3_ready_to_complete(self, instance_id: UUID) -> None:
        instance = self.session.get(WorkflowInstance, instance_id)
        if instance is None:
            raise NotFoundError("workflow instance not found")
        organizer = (instance.context_json or {}).get("organizer") or {}
        ids = [UUID(str(x)) for x in organizer.get("evidence_item_ids") or []]
        if not self.is_confirmation_gate_complete(evidence_item_ids=ids):
            raise ValidationError(
                "N3 confirmation gate incomplete: PENDING evidence remains"
            )

    def complete_n3_confirm_evidence(
        self,
        *,
        instance_id: UUID,
        node_run_id: UUID,
        auto_advance: bool = False,
    ):
        """Complete N3 only when all Organizer evidence items are ACCEPTED/EXCLUDED.

        Phase 5 default: auto_advance=False so N4_ANALYZE is not started.
        """
        self.assert_n3_ready_to_complete(instance_id)
        runtime = WorkflowRuntime(self.session)
        return runtime.complete_node(node_run_id, auto_advance=auto_advance)

    # ----- internals -----

    def _load_and_validate_ecs(
        self, case_id: UUID, extracted_content_ids: list[UUID]
    ) -> list[ExtractedContent]:
        ecs: list[ExtractedContent] = []
        for ec_id in extracted_content_ids:
            ec = self.session.get(ExtractedContent, ec_id)
            if ec is None:
                raise NotFoundError(f"extracted_content not found: {ec_id}")
            material = self.session.get(CaseMaterial, ec.material_id)
            if material is None or material.case_id != case_id:
                raise ValidationError(
                    f"extracted_content {ec_id} does not belong to case {case_id}"
                )
            if ec.status != "SUCCEEDED":
                raise ValidationError(
                    f"extracted_content {ec_id} is not SUCCEEDED (status={ec.status})"
                )
            ecs.append(ec)
        return ecs

    def _load_trusted_spans(
        self, case_id: UUID, ecs: list[ExtractedContent]
    ) -> list[SpanView]:
        ec_ids = [ec.id for ec in ecs]
        rows = self.session.scalars(
            select(SourceSpan).where(SourceSpan.extracted_content_id.in_(ec_ids))
        ).all()
        views: list[SpanView] = []
        for span in rows:
            material = self.session.get(CaseMaterial, span.material_id)
            if material is None or material.case_id != case_id:
                continue
            ec = self.session.get(ExtractedContent, span.extracted_content_id)
            if ec is None or ec.status != "SUCCEEDED":
                continue
            views.append(
                SpanView(
                    span_id=span.id,
                    quote=span.quote,
                    page=span.page,
                    paragraph=span.paragraph,
                    material_id=span.material_id,
                    ec_id=span.extracted_content_id,
                )
            )
        return views

    def _validate_proposal_spans(
        self,
        proposal: EvidenceItemProposal,
        case_id: UUID,
        span_by_id: dict[UUID, SpanView],
        allowed_ec_ids: list[UUID],
    ) -> None:
        if not proposal.source_span_ids:
            raise ValidationError("proposal missing source_span_ids")
        allowed = set(allowed_ec_ids)
        for sid in proposal.source_span_ids:
            if sid not in span_by_id:
                # Distinguish missing vs out-of-scope / failed / cross-case
                span = self.session.get(SourceSpan, sid)
                if span is None:
                    raise ValidationError(f"source_span not found: {sid}")
                material = self.session.get(CaseMaterial, span.material_id)
                if material is None or material.case_id != case_id:
                    raise ValidationError(f"cross-case source_span rejected: {sid}")
                ec = self.session.get(ExtractedContent, span.extracted_content_id)
                if ec is None or ec.status != "SUCCEEDED":
                    raise ValidationError(
                        f"source_span from FAILED/untrusted ExtractedContent: {sid}"
                    )
                if span.extracted_content_id not in allowed:
                    raise ValidationError(
                        f"source_span outside organizer scope: {sid}"
                    )
                raise ValidationError(f"source_span not available to organizer: {sid}")
            view = span_by_id[sid]
            if view.ec_id not in allowed:
                raise ValidationError(f"source_span outside organizer scope: {sid}")

    def _find_current_by_span_set(
        self, case_id: UUID, span_set: frozenset[UUID]
    ) -> EvidenceItem | None:
        currents = self.session.scalars(
            select(EvidenceItem).where(
                EvidenceItem.case_id == case_id,
                EvidenceItem.is_current.is_(True),
            )
        ).all()
        for item in currents:
            links = self.session.scalars(
                select(EvidenceItemSpan).where(
                    EvidenceItemSpan.evidence_item_id == item.id,
                    EvidenceItemSpan.evidence_item_version == item.version,
                )
            ).all()
            existing_set = frozenset(x.source_span_id for x in links)
            if existing_set == span_set:
                return item
        return None

    def _content_equal(self, item: EvidenceItem, proposal: EvidenceItemProposal) -> bool:
        return (
            item.title == proposal.title
            and (item.summary or "") == (proposal.summary or "")
            and item.category == proposal.category
        )

    def _next_evidence_number(self, case_id: UUID) -> int:
        currents = self.session.scalars(
            select(EvidenceItem).where(
                EvidenceItem.case_id == case_id,
                EvidenceItem.is_current.is_(True),
            )
        ).all()
        return len(currents) + 1

    def _start_skill_execution(
        self,
        *,
        node_run_id: UUID,
        case_id: UUID,
        extracted_content_ids: list[UUID],
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
            skill_code="EvidenceOrganizerSkill",
            status="RUNNING",
            metrics_json={
                "input": {
                    "case_id": str(case_id),
                    "extracted_content_ids": [str(x) for x in extracted_content_ids],
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
        result: OrganizerApplyResult,
        finished_at: datetime,
    ) -> None:
        metrics = dict(exec_row.metrics_json or {})
        metrics["finished_at"] = finished_at.isoformat()
        metrics["output"] = {
            "created": [
                {"id": str(x.id), "version": x.version, "number": x.number}
                for x in result.created
            ],
            "amended": [
                {"id": str(x.id), "version": x.version, "number": x.number}
                for x in result.amended
            ],
            "skipped": result.skipped,
            "failed_proposals": result.failed_proposals,
        }
        exec_row.metrics_json = metrics
        if result.failed_proposals and not result.created and not result.amended:
            exec_row.status = "FAILED"
            exec_row.error_code = "ALL_PROPOSALS_REJECTED"
            exec_row.error_detail = "all organizer proposals failed validation"
        else:
            exec_row.status = "SUCCEEDED"
        self.session.flush()
