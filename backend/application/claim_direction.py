"""Application: Claim Direction — CONFIRMED Party/Fact → ClaimDirection CANDIDATE.

AI never confirms claim directions or invents unsupported amounts.
Does not implement PleadingWriter / N8 / DocumentDraft.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.domain.errors import NotFoundError, ValidationError
from backend.domain.services import DomainService
from backend.models import ClaimDirection, Fact, NodeRun, SkillExecution, WorkflowInstance
from backend.schemas.claim_direction import validate_claim_direction_payload
from backend.schemas.claim_direction_proposal import (
    ClaimDirectionEngineResult,
    ClaimDirectionInput,
    ClaimDirectionProposal,
    FactRef,
)
from backend.skills.claim_direction import (
    ClaimDirectionEngine,
    DeterministicClaimDirectionStub,
    FactView,
    PartyView,
    extract_amounts_from_text,
)
from backend.workflow.runtime import WorkflowRuntime


def _now() -> datetime:
    return datetime.now(UTC)


def _payload_fingerprint(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass
class ClaimDirectionApplyResult:
    created: list[ClaimDirection] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    rejected_proposals: list[dict[str, Any]] = field(default_factory=list)
    amendment_proposals: list[dict[str, Any]] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    missing_confirmations: list[str] = field(default_factory=list)
    claim_direction_keys: list[UUID] = field(default_factory=list)
    skill_execution_id: UUID | None = None


class ClaimDirectionService:
    def __init__(
        self,
        session: Session,
        *,
        engine: ClaimDirectionEngine | None = None,
    ) -> None:
        self.session = session
        self.domain = DomainService(session)
        self.engine: ClaimDirectionEngine = engine or DeterministicClaimDirectionStub()

    def propose(
        self,
        *,
        case_id: UUID,
        confirmed_fact_refs: list[dict[str, Any] | FactRef],
        confirmed_party_keys: list[UUID],
        actor_id: UUID,
        node_run_id: UUID | None = None,
        raw_result: dict[str, Any] | ClaimDirectionEngineResult | None = None,
    ) -> ClaimDirectionApplyResult:
        self.domain._require_case(case_id)  # noqa: SLF001
        fact_refs = self._parse_fact_refs(confirmed_fact_refs)
        if not confirmed_party_keys:
            raise ValidationError("confirmed_party_keys required")
        parties = self._validate_parties(case_id, confirmed_party_keys)
        facts = self._validate_facts(case_id, fact_refs)
        scope = {(f.fact_key, f.fact_version) for f in facts}

        started = _now()
        skill_exec = None
        if node_run_id is not None:
            skill_exec = self._start_skill_execution(
                node_run_id=node_run_id,
                case_id=case_id,
                fact_refs=fact_refs,
                party_keys=confirmed_party_keys,
                started_at=started,
            )

        if raw_result is None:
            inp = ClaimDirectionInput(
                case_id=case_id,
                confirmed_fact_refs=fact_refs,
                confirmed_party_keys=list(confirmed_party_keys),
            )
            try:
                engine_result = self.engine.propose(inp, facts, parties)
            except Exception as exc:  # noqa: BLE001 — engine/LLM failures
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
        elif isinstance(raw_result, ClaimDirectionEngineResult):
            engine_result = raw_result
        else:
            engine_result = ClaimDirectionEngineResult.model_validate(raw_result)

        result = ClaimDirectionApplyResult()
        for proposal in engine_result.proposals:
            result.risks.extend(proposal.risks)
            result.missing_confirmations.extend(proposal.missing_confirmations)
            try:
                self._apply_proposal(
                    case_id=case_id,
                    proposal=proposal,
                    facts=facts,
                    scope=scope,
                    actor_id=actor_id,
                    result=result,
                )
            except ValidationError as exc:
                result.rejected_proposals.append(
                    {
                        "proposal_id": str(proposal.proposal_id),
                        "error": exc.message,
                        "code": exc.code,
                    }
                )

        if skill_exec is not None:
            self._finish_skill_execution(skill_exec, result=result, finished_at=_now())
            result.skill_execution_id = skill_exec.id
        return result

    def get_confirmed_claim_direction_for_writer(self, case_id: UUID) -> ClaimDirection:
        """Writer-facing query: only CONFIRMED + stale=false current ClaimDirection.

        If multiple current CONFIRMED rows exist (different keys), prefer the single
        non-stale one; ambiguity raises ValidationError (no fuzzy latest).
        """
        self.domain._require_case(case_id)  # noqa: SLF001
        rows = list(
            self.session.scalars(
                select(ClaimDirection).where(
                    ClaimDirection.case_id == case_id,
                    ClaimDirection.is_current.is_(True),
                    ClaimDirection.status == "CONFIRMED",
                    ClaimDirection.stale.is_(False),
                )
            )
        )
        if not rows:
            raise ValidationError(
                "no CONFIRMED non-stale ClaimDirection available for Writer"
            )
        if len(rows) > 1:
            raise ValidationError(
                "multiple CONFIRMED non-stale ClaimDirections; ambiguous for Writer"
            )
        return rows[0]

    def is_n7_gate_complete(self, instance_id: UUID) -> bool:
        instance = self.session.get(WorkflowInstance, instance_id)
        if instance is None:
            raise NotFoundError("workflow instance not found")
        claim_ctx = (instance.context_json or {}).get("claim_direction") or {}
        keys = [UUID(str(x)) for x in claim_ctx.get("claim_direction_keys") or []]
        if not keys:
            return False
        for key in keys:
            claim = self.domain.repo.get_current_claim(key)
            if claim is None:
                raise NotFoundError(f"claim direction not found: {key}")
            if claim.status == "CANDIDATE":
                return False
            if claim.status not in {"CONFIRMED", "REJECTED"}:
                return False
        # At least one usable CONFIRMED non-stale claim for Writer path
        try:
            self.get_confirmed_claim_direction_for_writer(instance.case_id)
        except ValidationError:
            return False
        return True

    def run_n7_propose(
        self,
        *,
        instance_id: UUID,
        node_run_id: UUID,
        confirmed_fact_refs: list[dict[str, Any] | FactRef],
        confirmed_party_keys: list[UUID],
        actor_id: UUID,
        auto_wait: bool = True,
    ) -> ClaimDirectionApplyResult:
        """Run ClaimDirectionSkill on N7 node, then leave instance WAITING_USER."""
        runtime = WorkflowRuntime(self.session)
        instance = runtime.get_instance(instance_id)
        node_run = runtime.get_node_run(node_run_id)
        if node_run.instance_id != instance.id:
            raise ValidationError("node_run does not belong to instance")

        result = self.propose(
            case_id=instance.case_id,
            confirmed_fact_refs=confirmed_fact_refs,
            confirmed_party_keys=confirmed_party_keys,
            actor_id=actor_id,
            node_run_id=node_run_id,
        )

        ctx = dict(instance.context_json or {})
        ctx["claim_direction"] = {
            "claim_direction_keys": [str(k) for k in result.claim_direction_keys],
            "created": [str(c.claim_direction_key) for c in result.created],
            "skipped": result.skipped,
            "rejected_proposals": result.rejected_proposals,
            "amendment_proposals": result.amendment_proposals,
            "risks": result.risks,
            "missing_confirmations": result.missing_confirmations,
            "skill_execution_id": str(result.skill_execution_id)
            if result.skill_execution_id
            else None,
        }
        instance.context_json = ctx
        self.session.flush()

        if auto_wait:
            # N7 is a human gate: keep / set WAITING_USER for lawyer confirm.
            if node_run.status == "RUNNING":
                node_run.status = "WAITING_USER"
            runtime.wait_for_user(
                instance.id,
                reason="CLAIM",
                context={"node_run_id": str(node_run_id), "gate": "N7_CONFIRM_CLAIMS"},
            )
        return result

    def assert_n7_ready_to_complete(self, instance_id: UUID) -> None:
        if not self.is_n7_gate_complete(instance_id):
            raise ValidationError(
                "N7 claim gate incomplete: pending proposals remain "
                "or no CONFIRMED non-stale ClaimDirection"
            )

    def complete_n7_confirm_claims(
        self,
        *,
        instance_id: UUID,
        node_run_id: UUID,
        auto_advance: bool = False,
    ):
        """Complete N7 only when gate is satisfied. Phase 7 default: no N8."""
        self.assert_n7_ready_to_complete(instance_id)
        runtime = WorkflowRuntime(self.session)
        return runtime.complete_node(node_run_id, auto_advance=auto_advance)

    # ----- internals -----

    def _parse_fact_refs(
        self, confirmed_fact_refs: list[dict[str, Any] | FactRef]
    ) -> list[FactRef]:
        if not confirmed_fact_refs:
            raise ValidationError(
                "confirmed_fact_refs required; no implicit fact selection"
            )
        refs: list[FactRef] = []
        for raw in confirmed_fact_refs:
            if isinstance(raw, FactRef):
                refs.append(raw)
                continue
            if not isinstance(raw, dict):
                raise ValidationError("fact ref must be an object")
            if "fact_version" not in raw or raw.get("fact_version") is None:
                raise ValidationError(
                    "fact_version required; implicit current/latest is forbidden"
                )
            if "fact_key" not in raw:
                raise ValidationError("fact_key required")
            try:
                refs.append(FactRef.model_validate(raw))
            except Exception as exc:  # noqa: BLE001
                raise ValidationError(f"invalid fact ref: {exc}") from exc
        return refs

    def _validate_parties(
        self, case_id: UUID, party_keys: list[UUID]
    ) -> list[PartyView]:
        views: list[PartyView] = []
        for key in party_keys:
            party = self.domain.repo.get_current_party(key)
            if party is None:
                raise ValidationError(f"party not found: {key}")
            if party.case_id != case_id:
                raise ValidationError(f"cross-case party rejected: {key}")
            if party.layer != "CONFIRMED":
                raise ValidationError(f"party not CONFIRMED: {key}")
            views.append(
                PartyView(
                    party_key=party.party_key,
                    role=party.role,
                    name=party.name,
                    party_type=party.party_type,
                    version=party.version,
                )
            )
        return views

    def _validate_facts(self, case_id: UUID, refs: list[FactRef]) -> list[FactView]:
        views: list[FactView] = []
        for ref in refs:
            row = self.session.scalars(
                select(Fact).where(
                    Fact.fact_key == ref.fact_key,
                    Fact.version == ref.fact_version,
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
            if row.status == "REJECTED":
                raise ValidationError(f"REJECTED fact rejected: {ref.fact_key}")
            if row.status == "SUPERSEDED":
                raise ValidationError(f"SUPERSEDED fact rejected: {ref.fact_key}")
            if row.status != "CONFIRMED":
                raise ValidationError(
                    f"fact must be CONFIRMED: {ref.fact_key} (status={row.status})"
                )
            if row.stale:
                raise ValidationError(f"stale fact rejected: {ref.fact_key}")
            # Must also be the current identity row for Domain supporting_fact_ids.
            current = self.domain.repo.get_current_fact(ref.fact_key)
            if current is None or current.version != ref.fact_version:
                raise ValidationError(
                    f"fact_version {ref.fact_version} is not current for "
                    f"{ref.fact_key}; implicit latest forbidden — pass current "
                    f"CONFIRMED version explicitly"
                )
            views.append(
                FactView(
                    fact_key=row.fact_key,
                    fact_version=row.version,
                    statement=row.statement,
                    status=row.status,
                    stale=row.stale,
                    amounts_mentioned=extract_amounts_from_text(row.statement),
                )
            )
        return views

    def _apply_proposal(
        self,
        *,
        case_id: UUID,
        proposal: ClaimDirectionProposal,
        facts: list[FactView],
        scope: set[tuple[UUID, int]],
        actor_id: UUID,
        result: ClaimDirectionApplyResult,
    ) -> None:
        domain_payload = self._to_domain_payload(proposal, scope=scope)
        # Re-validate frozen ClaimDirection schema
        validated = validate_claim_direction_payload(domain_payload)
        self._assert_amount_provenance(validated, facts)
        self._assert_interest_safety(validated, facts)

        fingerprint = _payload_fingerprint(validated)
        existing = self._find_duplicate(case_id, fingerprint)
        if existing is not None:
            result.skipped.append(
                {
                    "reason": "duplicate",
                    "claim_direction_key": str(existing.claim_direction_key),
                    "version": existing.version,
                    "status": existing.status,
                    "proposal_id": str(proposal.proposal_id),
                }
            )
            result.claim_direction_keys.append(existing.claim_direction_key)
            return

        confirmed = self._find_confirmed_peer(case_id)
        created = self.domain.create_claim_direction(
            case_id=case_id,
            payload=validated,
            actor_id=actor_id,
        )
        if created.status != "CANDIDATE":
            raise ValidationError("claim direction proposal must be CANDIDATE")
        result.created.append(created)
        result.claim_direction_keys.append(created.claim_direction_key)
        if confirmed is not None:
            result.amendment_proposals.append(
                {
                    "proposal_id": str(proposal.proposal_id),
                    "new_claim_direction_key": str(created.claim_direction_key),
                    "targets_confirmed_key": str(confirmed.claim_direction_key),
                    "targets_version": confirmed.version,
                    "note": (
                        "AI must not amend CONFIRMED ClaimDirection; "
                        "lawyer may amend via Domain Service"
                    ),
                }
            )

    def _to_domain_payload(
        self,
        proposal: ClaimDirectionProposal,
        *,
        scope: set[tuple[UUID, int]],
    ) -> dict[str, Any]:
        claims: list[dict[str, Any]] = []
        for item in proposal.claims:
            if not item.supporting_fact_refs:
                raise ValidationError("claim missing supporting_fact_refs")
            fact_keys: list[str] = []
            for ref in item.supporting_fact_refs:
                if (ref.fact_key, ref.fact_version) not in scope:
                    raise ValidationError(
                        f"claim fact ref outside input scope: "
                        f"{ref.fact_key}@v{ref.fact_version}"
                    )
                fact_keys.append(str(ref.fact_key))
            claims.append(
                {
                    "claim_type": item.claim_type.value
                    if hasattr(item.claim_type, "value")
                    else str(item.claim_type),
                    "description": item.description,
                    "amount": item.amount,
                    "currency": item.currency,
                    "calculation_basis": item.calculation_basis,
                    "interest_start_date": item.interest_start_date.isoformat()
                    if item.interest_start_date
                    else None,
                    "interest_rate": item.interest_rate,
                    "supporting_fact_ids": fact_keys,
                }
            )
        return {
            "overall_strategy": proposal.overall_strategy,
            "claims": claims,
        }

    def _assert_amount_provenance(
        self, payload: dict[str, Any], facts: list[FactView]
    ) -> None:
        """Reject money amounts not grounded in CONFIRMED Fact numeric content."""
        raw = {round(a, 6) for f in facts for a in f.amounts_mentioned}
        # Drop date fragments (year/month/day) so pairwise diffs stay monetary.
        money = {a for a in raw if a >= 100.0}
        derived = set(money)
        for a in money:
            for b in money:
                if a > b:
                    derived.add(round(a - b, 6))

        for item in payload.get("claims") or []:
            amount = item.get("amount")
            if amount is None:
                continue
            rounded = round(float(amount), 6)
            if rounded not in derived:
                raise ValidationError(
                    f"amount {amount} is not grounded in CONFIRMED Fact amounts "
                    f"(cannot invent money); known={sorted(money)}"
                )

    def _assert_interest_safety(
        self, payload: dict[str, Any], facts: list[FactView]
    ) -> None:
        """Reject interest fields unless CONFIRMED facts mention interest terms."""
        import re

        supported = any(
            re.search(r"利率|利息|LPR|起算|年息|日息", f.statement or "") for f in facts
        )
        for item in payload.get("claims") or []:
            if item.get("interest_rate") or item.get("interest_start_date"):
                if not supported:
                    raise ValidationError(
                        "interest fields require CONFIRMED Fact support; "
                        "cannot invent LPR/rate/start date"
                    )

    def _find_duplicate(
        self, case_id: UUID, fingerprint: str
    ) -> ClaimDirection | None:
        currents = self.session.scalars(
            select(ClaimDirection).where(
                ClaimDirection.case_id == case_id,
                ClaimDirection.is_current.is_(True),
                ClaimDirection.status.in_(["CANDIDATE", "CONFIRMED"]),
            )
        ).all()
        for claim in currents:
            if _payload_fingerprint(dict(claim.payload or {})) == fingerprint:
                return claim
        return None

    def _find_confirmed_peer(self, case_id: UUID) -> ClaimDirection | None:
        return self.session.scalars(
            select(ClaimDirection).where(
                ClaimDirection.case_id == case_id,
                ClaimDirection.is_current.is_(True),
                ClaimDirection.status == "CONFIRMED",
                ClaimDirection.stale.is_(False),
            )
        ).first()

    def _start_skill_execution(
        self,
        *,
        node_run_id: UUID,
        case_id: UUID,
        fact_refs: list[FactRef],
        party_keys: list[UUID],
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
            skill_code="ClaimDirectionSkill",
            status="RUNNING",
            metrics_json={
                "input": {
                    "case_id": str(case_id),
                    "workflow_instance_id": str(node_run.instance_id),
                    "confirmed_party_keys": [str(x) for x in party_keys],
                    "confirmed_fact_refs": [
                        {
                            "fact_key": str(r.fact_key),
                            "fact_version": r.fact_version,
                        }
                        for r in fact_refs
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
        result: ClaimDirectionApplyResult,
        finished_at: datetime,
    ) -> None:
        metrics = dict(exec_row.metrics_json or {})
        metrics["finished_at"] = finished_at.isoformat()
        metrics["output"] = {
            "created": [
                {
                    "claim_direction_key": str(c.claim_direction_key),
                    "id": str(c.id),
                    "version": c.version,
                    "status": c.status,
                }
                for c in result.created
            ],
            "skipped": result.skipped,
            "rejected_proposals": result.rejected_proposals,
            "amendment_proposals": result.amendment_proposals,
            "risks": result.risks,
            "missing_confirmations": result.missing_confirmations,
        }
        exec_row.metrics_json = metrics
        if (
            result.rejected_proposals
            and not result.created
            and not result.skipped
        ):
            exec_row.status = "FAILED"
            exec_row.error_code = "ALL_CLAIM_PROPOSALS_REJECTED"
            exec_row.error_detail = "all claim direction proposals failed validation"
        else:
            exec_row.status = "SUCCEEDED"
            meta = getattr(self.engine, "last_meta", None)
            if isinstance(meta, dict):
                metrics["llm"] = meta
                exec_row.metrics_json = metrics
        self.session.flush()
