"""Application: Pleading Writer — CONFIRMED inputs → DocumentDraft(DRAFT).

Writer never creates facts, claims, amounts, interest schemes, or legal conclusions.
Does not auto-approve. Does not implement Agent / N9 product review UI.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.application.claim_direction import ClaimDirectionService
from backend.application.pleading_draft_validator import PleadingDraftValidator
from backend.application.pleading_readiness import PleadingReadinessService
from backend.application.pleading_structured_input import PleadingStructuredInputBuilder
from backend.domain.errors import NotFoundError, ValidationError
from backend.domain.services import DomainService
from backend.models import (
    CaseMaterial,
    DocumentDraft,
    EvidenceItem,
    ExtractedContent,
    Fact,
    NodeRun,
    SkillExecution,
    SourceSpan,
    WorkflowInstance,
)
from backend.schemas.case_analyst import EvidenceRef
from backend.schemas.claim_direction_proposal import FactRef
from backend.schemas.pleading_quality import PleadingDraftValidationError
from backend.schemas.pleading_writer import (
    ClaimDirectionRef,
    PleadingWriterEngineResult,
    PleadingWriterInput,
)
from backend.skills.civil_complaint_renderer import build_body_structured, render_civil_complaint
from backend.skills.pleading_writer import (
    AcceptedEvidenceView,
    ClaimDirectionView,
    ConfirmedFactView,
    DeterministicPleadingWriterStub,
    PartyView,
    PleadingWriterEngine,
    looks_like_fabricated_law_citation,
    looks_like_invented_interest,
)
from backend.workflow.runtime import WorkflowRuntime


def _now() -> datetime:
    return datetime.now(UTC)


def _stable_hash(parts: list[str]) -> str:
    blob = "|".join(parts)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass
class PleadingWriterApplyResult:
    draft: DocumentDraft | None = None
    warnings: list[dict[str, Any]] = field(default_factory=list)
    citation_count: int = 0
    skill_execution_id: UUID | None = None
    used_fact_refs: list[dict[str, Any]] = field(default_factory=list)
    used_evidence_refs: list[dict[str, Any]] = field(default_factory=list)
    claim_direction_ref: dict[str, Any] | None = None


class PleadingWriterService:
    DOC_TYPE = "CIVIL_COMPLAINT"

    def __init__(
        self,
        session: Session,
        *,
        engine: PleadingWriterEngine | None = None,
    ) -> None:
        self.session = session
        self.domain = DomainService(session)
        self.claim_svc = ClaimDirectionService(session)
        self.engine: PleadingWriterEngine = engine or DeterministicPleadingWriterStub()

    def write(
        self,
        *,
        case_id: UUID,
        claim_direction_ref: dict[str, Any] | ClaimDirectionRef | None,
        confirmed_fact_refs: list[dict[str, Any] | FactRef],
        accepted_evidence_refs: list[dict[str, Any] | EvidenceRef],
        confirmed_party_keys: list[UUID],
        actor_id: UUID,
        node_run_id: UUID | None = None,
        raw_result: PleadingWriterEngineResult | None = None,
    ) -> PleadingWriterApplyResult:
        self.domain._require_case(case_id)  # noqa: SLF001

        # Hard gate: No readiness → No complaint draft
        PleadingReadinessService(self.session).assert_ready(case_id)

        claim = self._require_writer_claim(case_id, claim_direction_ref)
        parties = self._validate_parties(case_id, confirmed_party_keys)
        fact_refs = self._parse_fact_refs(confirmed_fact_refs)
        evidence_refs = self._parse_evidence_refs(accepted_evidence_refs)
        facts = self._validate_facts(case_id, fact_refs)
        evidence = self._validate_evidence(case_id, evidence_refs)
        self._assert_fact_evidence_in_scope(facts, evidence_refs)

        started = _now()
        skill_exec = None
        if node_run_id is not None:
            skill_exec = self._start_skill_execution(
                node_run_id=node_run_id,
                case_id=case_id,
                parties=parties,
                fact_refs=fact_refs,
                evidence_refs=evidence_refs,
                claim=claim,
                started_at=started,
            )

        inp = PleadingWriterInput(
            case_id=case_id,
            claim_direction_ref=ClaimDirectionRef(
                claim_direction_key=claim.claim_direction_key,
                claim_direction_version=claim.claim_direction_version,
            ),
            confirmed_fact_refs=fact_refs,
            accepted_evidence_refs=evidence_refs,
            confirmed_party_keys=list(confirmed_party_keys),
        )
        structured = PleadingStructuredInputBuilder(self.session).build(
            parties=parties,
            facts=facts,
            claim=claim,
            evidence=evidence,
        )
        validator = PleadingDraftValidator()
        repair_count = 0
        try:
            engine_result = raw_result
            if engine_result is None:
                engine_result = self.engine.write(
                    inp,
                    parties=parties,
                    facts=facts,
                    claim=claim,
                    evidence=evidence,
                    structured=structured,
                )
                self._validate_engine_result(
                    engine_result,
                    claim=claim,
                    facts=facts,
                    evidence_refs=evidence_refs,
                )
                full_text = render_civil_complaint(engine_result)
                vresult = validator.validate(
                    engine_result, structured, full_text=full_text
                )
                if not vresult.passed:
                    repair_fn = getattr(self.engine, "repair", None)
                    if callable(repair_fn) and repair_count < 1:
                        repair_count = 1
                        engine_result = repair_fn(
                            inp,
                            parties=parties,
                            facts=facts,
                            claim=claim,
                            evidence=evidence,
                            structured=structured,
                            errors=vresult.errors,
                            prior=engine_result,
                        )
                        self._validate_engine_result(
                            engine_result,
                            claim=claim,
                            facts=facts,
                            evidence_refs=evidence_refs,
                        )
                        full_text = render_civil_complaint(engine_result)
                        vresult = validator.validate(
                            engine_result, structured, full_text=full_text
                        )
                    if not vresult.passed:
                        raise PleadingDraftValidationError(vresult)
        except PleadingDraftValidationError:
            raise
        except Exception as exc:  # noqa: BLE001 — engine/LLM/validation failures
            if skill_exec is not None:
                skill_exec.status = "FAILED"
                skill_exec.error_code = getattr(exc, "code", "ENGINE_FAILED")[:64]
                skill_exec.error_detail = str(exc)[:500]
                metrics = dict(skill_exec.metrics_json or {})
                meta = getattr(self.engine, "last_meta", None)
                if isinstance(meta, dict):
                    metrics["llm"] = meta
                skill_exec.metrics_json = metrics
                self.session.flush()
            raise

        full_text = render_civil_complaint(engine_result)
        body = build_body_structured(engine_result, full_text=full_text)
        body["structured_input_summary"] = {
            "liability_bridge_required": structured.liability_bridge_required,
            "evidence_material_count": len(structured.evidence_directory),
        }
        body["validation"] = {
            "passed": True,
            "repair_count": repair_count,
            "issue_codes": [],
        }
        citations = self._build_citations(engine_result, facts)
        conf_hash = self._confirmation_hash(
            parties=parties, facts=facts, claim=claim, evidence=evidence
        )

        draft = self.domain.create_document_draft(
            case_id=case_id,
            body_structured_json=body,
            citations=citations,
            based_on_confirmation_set_hash=conf_hash,
            doc_type=self.DOC_TYPE,
            status="DRAFT",
            writer_run_id=node_run_id,
            actor_id=actor_id,
        )
        if draft.status != "DRAFT":
            raise ValidationError("Writer must create DocumentDraft as DRAFT only")

        result = PleadingWriterApplyResult(
            draft=draft,
            warnings=[w.model_dump(mode="json") for w in engine_result.warnings],
            citation_count=len(citations),
            used_fact_refs=[r.model_dump(mode="json") for r in engine_result.used_fact_refs],
            used_evidence_refs=[
                r.model_dump(mode="json") for r in engine_result.used_evidence_refs
            ],
            claim_direction_ref={
                "claim_direction_key": str(claim.claim_direction_key),
                "claim_direction_version": claim.claim_direction_version,
            },
        )
        # Ensure Phase 7 gap warning is always present in audit output.
        if not any(
            w.get("code") == "CLAIM_FACT_VERSION_PROVENANCE_GAP" for w in result.warnings
        ):
            result.warnings.append(
                {
                    "code": "CLAIM_FACT_VERSION_PROVENANCE_GAP",
                    "message": (
                        "ClaimDirection Domain stores supporting_fact_ids as fact_key "
                        "only; Writer audited explicit fact_key+fact_version for this run."
                    ),
                }
            )

        if skill_exec is not None:
            self._finish_skill_execution(skill_exec, result=result, finished_at=_now())
            result.skill_execution_id = skill_exec.id
        return result

    def run_n8_write(
        self,
        *,
        instance_id: UUID,
        node_run_id: UUID,
        claim_direction_ref: dict[str, Any] | ClaimDirectionRef | None,
        confirmed_fact_refs: list[dict[str, Any] | FactRef],
        accepted_evidence_refs: list[dict[str, Any] | EvidenceRef],
        confirmed_party_keys: list[UUID],
        actor_id: UUID,
        auto_complete: bool = True,
    ) -> PleadingWriterApplyResult:
        runtime = WorkflowRuntime(self.session)
        instance = runtime.get_instance(instance_id)
        node_run = runtime.get_node_run(node_run_id)
        if node_run.instance_id != instance.id:
            raise ValidationError("node_run does not belong to instance")
        self._assert_n8_prerequisites(instance)

        result = self.write(
            case_id=instance.case_id,
            claim_direction_ref=claim_direction_ref,
            confirmed_fact_refs=confirmed_fact_refs,
            accepted_evidence_refs=accepted_evidence_refs,
            confirmed_party_keys=confirmed_party_keys,
            actor_id=actor_id,
            node_run_id=node_run_id,
        )

        ctx = dict(instance.context_json or {})
        ctx["pleading_writer"] = {
            "document_draft_id": str(result.draft.id) if result.draft else None,
            "draft_version": result.draft.version if result.draft else None,
            "citation_count": result.citation_count,
            "warnings": result.warnings,
            "used_fact_refs": result.used_fact_refs,
            "used_evidence_refs": result.used_evidence_refs,
            "claim_direction_ref": result.claim_direction_ref,
            "skill_execution_id": str(result.skill_execution_id)
            if result.skill_execution_id
            else None,
        }
        instance.context_json = ctx
        self.session.flush()

        if auto_complete:
            # N8 complete → N9 human gate WAITING_USER (do not approve draft)
            runtime.complete_node(
                node_run_id,
                output_ref=f"draft:{result.draft.id if result.draft else 'none'}",
            )
        return result

    # ----- gates / validation -----

    def _assert_n8_prerequisites(self, instance: WorkflowInstance) -> None:
        ctx = instance.context_json or {}
        if not (ctx.get("claim_direction") or {}).get("claim_direction_keys"):
            # Soft signal from Phase 7; still require Writer-usable ClaimDirection.
            pass
        try:
            self.claim_svc.get_confirmed_claim_direction_for_writer(instance.case_id)
        except ValidationError as exc:
            raise ValidationError(
                f"N8 requires CONFIRMED non-stale ClaimDirection (N7 incomplete): {exc.message}"
            ) from exc

    def _require_writer_claim(
        self,
        case_id: UUID,
        claim_direction_ref: dict[str, Any] | ClaimDirectionRef | None,
    ) -> ClaimDirectionView:
        usable = self.claim_svc.get_confirmed_claim_direction_for_writer(case_id)
        if claim_direction_ref is not None:
            if isinstance(claim_direction_ref, dict):
                if claim_direction_ref.get("claim_direction_version") is None:
                    raise ValidationError(
                        "claim_direction_version required; implicit latest forbidden"
                    )
                ref = ClaimDirectionRef.model_validate(claim_direction_ref)
            else:
                ref = claim_direction_ref
            if ref.claim_direction_key != usable.claim_direction_key:
                raise ValidationError("claim_direction_ref does not match Writer-usable claim")
            if ref.claim_direction_version != usable.version:
                raise ValidationError(
                    "claim_direction_version mismatch with current CONFIRMED claim"
                )
        return ClaimDirectionView(
            claim_direction_key=usable.claim_direction_key,
            claim_direction_version=usable.version,
            payload=dict(usable.payload or {}),
            status=usable.status,
            stale=usable.stale,
        )

    def _parse_fact_refs(
        self, refs: list[dict[str, Any] | FactRef]
    ) -> list[FactRef]:
        if not refs:
            raise ValidationError("confirmed_fact_refs required")
        out: list[FactRef] = []
        for raw in refs:
            if isinstance(raw, FactRef):
                out.append(raw)
                continue
            if "fact_version" not in raw or raw.get("fact_version") is None:
                raise ValidationError(
                    "fact_version required; implicit current/latest forbidden"
                )
            out.append(FactRef.model_validate(raw))
        return out

    def _parse_evidence_refs(
        self, refs: list[dict[str, Any] | EvidenceRef]
    ) -> list[EvidenceRef]:
        if not refs:
            raise ValidationError("accepted_evidence_refs required")
        out: list[EvidenceRef] = []
        for raw in refs:
            if isinstance(raw, EvidenceRef):
                out.append(raw)
                continue
            if "evidence_item_version" not in raw or raw.get("evidence_item_version") is None:
                raise ValidationError(
                    "evidence_item_version required; implicit latest forbidden"
                )
            if "evidence_item_id" not in raw and "filename" in raw:
                raise ValidationError("filename-only evidence citation is forbidden")
            out.append(EvidenceRef.model_validate(raw))
        return out

    def _validate_parties(
        self, case_id: UUID, party_keys: list[UUID]
    ) -> list[PartyView]:
        if not party_keys:
            raise ValidationError("confirmed_party_keys required")
        views: list[PartyView] = []
        roles: set[str] = set()
        for key in party_keys:
            party = self.domain.repo.get_current_party(key)
            if party is None:
                raise ValidationError(f"party not found: {key}")
            if party.case_id != case_id:
                raise ValidationError(f"cross-case party rejected: {key}")
            if party.layer != "CONFIRMED":
                raise ValidationError(f"party not CONFIRMED: {key}")
            roles.add(party.role)
            views.append(
                PartyView(
                    party_key=party.party_key,
                    role=party.role,
                    name=party.name,
                    party_type=party.party_type,
                    version=party.version,
                    identifiers_json=party.identifiers_json,
                )
            )
        if "PLAINTIFF" not in roles or "DEFENDANT" not in roles:
            raise ValidationError(
                "blocking: CONFIRMED PLAINTIFF and DEFENDANT required for complaint"
            )
        return views

    def _validate_facts(
        self, case_id: UUID, refs: list[FactRef]
    ) -> list[ConfirmedFactView]:
        views: list[ConfirmedFactView] = []
        for ref in refs:
            row = self.session.scalars(
                select(Fact).where(
                    Fact.fact_key == ref.fact_key, Fact.version == ref.fact_version
                )
            ).first()
            if row is None:
                any_row = self.session.scalars(
                    select(Fact).where(Fact.fact_key == ref.fact_key)
                ).first()
                if any_row is None:
                    raise ValidationError(f"fact not found: {ref.fact_key}")
                raise ValidationError(
                    f"fact version not found: {ref.fact_key}@v{ref.fact_version}"
                )
            if row.case_id != case_id:
                raise ValidationError(f"cross-case fact rejected: {ref.fact_key}")
            if row.status == "CANDIDATE":
                raise ValidationError(f"CANDIDATE fact rejected: {ref.fact_key}")
            if row.status != "CONFIRMED":
                raise ValidationError(
                    f"fact must be CONFIRMED: {ref.fact_key} (status={row.status})"
                )
            if row.stale:
                raise ValidationError(f"stale fact rejected: {ref.fact_key}")
            current = self.domain.repo.get_current_fact(ref.fact_key)
            if current is None or current.version != ref.fact_version:
                raise ValidationError(
                    f"fact_version {ref.fact_version} is not current CONFIRMED "
                    f"for {ref.fact_key}; implicit latest forbidden"
                )
            links = [
                lnk
                for lnk in self.domain.repo.list_fact_links(row.id)
                if lnk.status == "ACTIVE"
            ]
            if not links:
                raise ValidationError(
                    f"fact {ref.fact_key} has no ACTIVE FactEvidenceLink"
                )
            erefs: list[EvidenceRef] = []
            for lnk in links:
                erefs.append(
                    EvidenceRef(
                        evidence_item_id=lnk.evidence_item_id,
                        evidence_item_version=lnk.evidence_item_version,
                    )
                )
            views.append(
                ConfirmedFactView(
                    fact_key=row.fact_key,
                    fact_version=row.version,
                    statement=row.statement,
                    evidence_refs=erefs,
                )
            )
        return views

    def _validate_evidence(
        self, case_id: UUID, refs: list[EvidenceRef]
    ) -> list[AcceptedEvidenceView]:
        views: list[AcceptedEvidenceView] = []
        for ref in refs:
            item = self.domain.repo.get_evidence_version(
                ref.evidence_item_id, ref.evidence_item_version
            )
            if item is None:
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
                    f"PENDING evidence rejected: {ref.evidence_item_id}"
                )
            if item.acceptance == "EXCLUDED":
                raise ValidationError(
                    f"EXCLUDED evidence rejected: {ref.evidence_item_id}"
                )
            if item.acceptance != "ACCEPTED":
                raise ValidationError(
                    f"evidence must be ACCEPTED: {ref.evidence_item_id}"
                )
            self._assert_evidence_provenance(case_id, item)
            views.append(
                AcceptedEvidenceView(
                    evidence_item_id=item.id,
                    evidence_item_version=item.version,
                    number=item.number,
                    title=item.title,
                    summary=item.summary,
                    category=item.category,
                )
            )
        return views

    def _assert_evidence_provenance(self, case_id: UUID, item: EvidenceItem) -> None:
        spans = self.domain.repo.list_evidence_spans(item.id, item.version)
        if not spans:
            raise ValidationError(
                f"broken provenance: no EvidenceItemSpan for "
                f"{item.id}@v{item.version}"
            )
        for link in spans:
            span = self.session.get(SourceSpan, link.source_span_id)
            if span is None:
                raise ValidationError(
                    f"broken provenance: SourceSpan missing for evidence {item.id}"
                )
            material = self.session.get(CaseMaterial, span.material_id)
            if material is None or material.case_id != case_id:
                raise ValidationError(
                    f"broken provenance: material/case mismatch for span {span.id}"
                )
            if material.life_status == "VOID":
                raise ValidationError(
                    f"broken provenance: material is VOID for span {span.id}"
                )
            ec = self.session.get(ExtractedContent, span.extracted_content_id)
            if ec is None or ec.status != "SUCCEEDED":
                raise ValidationError(
                    f"broken provenance: ExtractedContent not SUCCEEDED for span {span.id}"
                )

    def _assert_fact_evidence_in_scope(
        self,
        facts: list[ConfirmedFactView],
        evidence_refs: list[EvidenceRef],
    ) -> None:
        scope = {(r.evidence_item_id, r.evidence_item_version) for r in evidence_refs}
        for fact in facts:
            for eref in fact.evidence_refs:
                if (eref.evidence_item_id, eref.evidence_item_version) not in scope:
                    raise ValidationError(
                        f"fact {fact.fact_key} links evidence outside Writer input scope: "
                        f"{eref.evidence_item_id}@v{eref.evidence_item_version}"
                    )

    def _validate_engine_result(
        self,
        result: PleadingWriterEngineResult,
        *,
        claim: ClaimDirectionView,
        facts: list[ConfirmedFactView],
        evidence_refs: list[EvidenceRef],
    ) -> None:
        payload_claims = list(claim.payload.get("claims") or [])
        substantive = [c for c in result.claims if c.claim_type != "PROCEDURAL"]
        if len(substantive) != len(payload_claims):
            raise ValidationError(
                "Writer substantive claims count must match CONFIRMED ClaimDirection"
            )
        for out, src in zip(substantive, payload_claims, strict=True):
            if out.claim_type != str(src.get("claim_type")):
                raise ValidationError("Writer must not change claim_type")
            src_amount = src.get("amount")
            if src_amount is None and out.amount is not None:
                raise ValidationError("Writer invented amount not present in ClaimDirection")
            if src_amount is not None:
                if out.amount is None or abs(float(out.amount) - float(src_amount)) > 1e-9:
                    raise ValidationError(
                        f"Writer amount {out.amount} != ClaimDirection amount {src_amount}"
                    )
                if str(out.currency or "") != str(src.get("currency") or ""):
                    raise ValidationError("Writer must not change currency")
                # Exact amount digits must appear in rendered claim text
                amount_token = (
                    str(int(src_amount))
                    if float(src_amount).is_integer()
                    else str(src_amount)
                )
                if amount_token not in out.text.replace(",", ""):
                    raise ValidationError(
                        f"rendered claim text missing exact amount {amount_token}"
                    )

            src_has_interest = bool(
                src.get("interest_start_date") or src.get("interest_rate")
            )
            if not src_has_interest and looks_like_invented_interest(out.text):
                raise ValidationError(
                    "Writer invented interest scheme not present in ClaimDirection"
                )

        allowed_fact_keys = {f.fact_key for f in facts}
        for block in result.fact_blocks:
            for fr in block.fact_refs:
                if fr.fact_key not in allowed_fact_keys:
                    raise ValidationError(
                        f"Writer fact block references fact outside input: {fr.fact_key}"
                    )
            if not block.evidence_refs:
                # Must still be able to resolve evidence via ConfirmedFactView
                matched = next(
                    (f for f in facts if f.fact_key == block.fact_refs[0].fact_key),
                    None,
                )
                if matched is None or not matched.evidence_refs:
                    raise ValidationError(
                        f"fact block {block.block_id} missing evidence provenance"
                    )

        combined = (
            result.facts_and_reasons_section
            + "\n"
            + result.claims_section
            + "\n"
            + result.parties_section
        )
        if looks_like_fabricated_law_citation(combined):
            raise ValidationError(
                "fabricated statute citation is forbidden without confirmed legal basis"
            )

        # used_fact_refs must include versions for every used fact
        for fr in result.used_fact_refs:
            if fr.fact_version < 1:
                raise ValidationError("used_fact_refs must include fact_version")

    def _build_citations(
        self,
        result: PleadingWriterEngineResult,
        facts: list[ConfirmedFactView],
    ) -> list[dict[str, Any]]:
        by_key = {f.fact_key: f for f in facts}
        citations: list[dict[str, Any]] = []
        for block in result.fact_blocks:
            for fr in block.fact_refs:
                fact = by_key.get(fr.fact_key)
                if fact is None:
                    raise ValidationError(f"citation fact missing: {fr.fact_key}")
                erefs = block.evidence_refs or fact.evidence_refs
                if not erefs:
                    raise ValidationError(
                        f"block {block.block_id} has no evidence for citation"
                    )
                for eref in erefs:
                    citations.append(
                        {
                            "block_id": block.block_id,
                            "citation_kind": "FACT",
                            "fact_key": str(fr.fact_key),
                            "fact_version": fr.fact_version,
                            "evidence_item_id": str(eref.evidence_item_id),
                            "evidence_item_version": eref.evidence_item_version,
                        }
                    )
        if not citations:
            raise ValidationError("Draft must include at least one DraftCitation")
        return citations

    def _confirmation_hash(
        self,
        *,
        parties: list[PartyView],
        facts: list[ConfirmedFactView],
        claim: ClaimDirectionView,
        evidence: list[AcceptedEvidenceView],
    ) -> str:
        parts = [
            f"party:{p.party_key}@v{p.version}" for p in parties
        ]
        parts += [f"fact:{f.fact_key}@v{f.fact_version}" for f in facts]
        parts.append(
            f"claim:{claim.claim_direction_key}@v{claim.claim_direction_version}"
        )
        parts += [
            f"ev:{e.evidence_item_id}@v{e.evidence_item_version}" for e in evidence
        ]
        return _stable_hash(sorted(parts))

    def _start_skill_execution(
        self,
        *,
        node_run_id: UUID,
        case_id: UUID,
        parties: list[PartyView],
        fact_refs: list[FactRef],
        evidence_refs: list[EvidenceRef],
        claim: ClaimDirectionView,
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
            skill_code="PleadingWriterSkill",
            status="RUNNING",
            metrics_json={
                "input": {
                    "case_id": str(case_id),
                    "workflow_instance_id": str(node_run.instance_id),
                    "party_keys": [str(p.party_key) for p in parties],
                    "confirmed_fact_refs": [
                        {
                            "fact_key": str(r.fact_key),
                            "fact_version": r.fact_version,
                        }
                        for r in fact_refs
                    ],
                    "accepted_evidence_refs": [
                        {
                            "evidence_item_id": str(r.evidence_item_id),
                            "evidence_item_version": r.evidence_item_version,
                        }
                        for r in evidence_refs
                    ],
                    "claim_direction_ref": {
                        "claim_direction_key": str(claim.claim_direction_key),
                        "claim_direction_version": claim.claim_direction_version,
                    },
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
        result: PleadingWriterApplyResult,
        finished_at: datetime,
    ) -> None:
        metrics = dict(exec_row.metrics_json or {})
        metrics["finished_at"] = finished_at.isoformat()
        metrics["output"] = {
            "document_draft_id": str(result.draft.id) if result.draft else None,
            "draft_version": result.draft.version if result.draft else None,
            "draft_status": result.draft.status if result.draft else None,
            "citation_count": result.citation_count,
            "warnings": result.warnings,
            "used_fact_refs": result.used_fact_refs,
            "used_evidence_refs": result.used_evidence_refs,
            "claim_direction_ref": result.claim_direction_ref,
        }
        exec_row.metrics_json = metrics
        exec_row.status = "SUCCEEDED"
        meta = getattr(self.engine, "last_meta", None)
        if isinstance(meta, dict):
            metrics["llm"] = meta
            exec_row.metrics_json = metrics
        self.session.flush()
