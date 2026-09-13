"""Issue-centered V2 domain mutations — mixed into DomainService.

Explicit invariants enforced in this module (Issue #80 repair SSOT / #73 / #60 / #83 / #86 / #85 / #84):
  INV-1: _reject_claim_direction_production_mutation blocks ClaimDirection writes.
  INV-2: link_fact_to_proof_task resolves via get_proof_task_version/get_fact_version
         only (never get_current_*); rejects non-positive versions and cross-case links.
  INV-3: create_lawyer_position requires opponent_material_ref for FORMAL_DEFENSE.
  INV-4: merge_issues and split_issue persist HumanDecision + AuditLog before mutation.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from backend.domain.enums import (
    ClaimLinkStatus,
    ConflictFactRole,
    ConflictSourceType,
    ConflictStatus,
    DecisionResult,
    IssueLinkStatus,
    IssueSourceType,
    IssueStatus,
    LawyerAssessmentStatus,
    PositionSide,
    PositionSourceType,
    PositionStatus,
    PositionType,
    ProofGapSourceType,
    ProofGapStatus,
    ProofGapType,
    ProofTaskFactLinkRole,
    ProofTaskSourceType,
    ProofTaskStatus,
)
from backend.domain.errors import ConflictError, NotFoundError, ValidationError
from backend.models import (
    AuditLog,
    ConflictFactLink,
    HumanDecision,
    Issue,
    IssueConflict,
    IssueFactLink,
    IssueLegalTheoryLink,
    IssuePosition,
    LawyerAssessment,
    ProofGap,
    ProofTask,
    ProofTaskFactLink,
)

if TYPE_CHECKING:
    from backend.domain.services import DomainService

__all__ = [
    "IssueCenteredDomainMixin",
    "_guard_cross_case_proof_task_fact_pair",
    "_guard_explicit_proof_task_fact_versions",
    "_guard_formal_defense_opponent_material_ref",
    "_guard_formal_defense_side",
    "_normalize_opponent_material_ref",
    "_reject_claim_direction_production_mutation",
    "_require_structure_mutation_audit",
]


def _now() -> datetime:
    return datetime.now(UTC)


def _reject_claim_direction_production_mutation(*, _legacy_compat: bool) -> None:
    """INV-1: block ClaimDirection creation on production mutation paths."""
    if not _legacy_compat:
        raise ValidationError(
            "ClaimDirection production mutation disabled; use Claim Domain instead"
        )


def _normalize_opponent_material_ref(ref: str | None) -> str | None:
    """INV-3: strip whitespace so blank refs cannot bypass FORMAL_DEFENSE guard."""
    if ref is None:
        return None
    stripped = ref.strip()
    return stripped or None


def _guard_explicit_proof_task_fact_versions(
    proof_task_version: int,
    fact_version: int,
) -> None:
    """INV-2: reject zero/negative versions that could alias implicit current/latest."""
    if proof_task_version < 1 or fact_version < 1:
        raise ValidationError(
            "ProofTaskFactLink requires explicit positive proof_task_version and fact_version"
        )


def _guard_formal_defense_opponent_material_ref(
    position_type: str,
    opponent_material_ref: str | None,
) -> None:
    """INV-3: FORMAL_DEFENSE must cite opponent source material."""
    if position_type == PositionType.FORMAL_DEFENSE.value:
        if not opponent_material_ref or not str(opponent_material_ref).strip():
            raise ValidationError("FORMAL_DEFENSE requires opponent material reference")


def _guard_formal_defense_side(position_type: str, side: str) -> None:
    """INV-3: FORMAL_DEFENSE positions must be recorded on the OPPONENT side."""
    if position_type == PositionType.FORMAL_DEFENSE.value and side != PositionSide.OPPONENT.value:
        raise ValidationError("FORMAL_DEFENSE must be on OPPONENT side")


def _guard_cross_case_proof_task_fact_pair(task: ProofTask, fact: Any) -> None:
    """INV-2: proof task and fact must belong to the same case."""
    if task.case_id != fact.case_id:
        raise ValidationError("cross-case proof task fact link rejected")


def _resolve_proof_task_and_fact_for_link(
    svc: DomainService,
    *,
    case_id: UUID,
    proof_task_key: UUID,
    proof_task_version: int,
    fact_key: UUID,
    fact_version: int,
) -> tuple[ProofTask, Any]:
    """INV-2: resolve only via explicit version lookups (never get_current_*)."""
    _guard_explicit_proof_task_fact_versions(proof_task_version, fact_version)
    task = svc.repo.get_proof_task_version(proof_task_key, proof_task_version)
    if task is None:
        raise NotFoundError("proof task version not found")
    if task.case_id != case_id:
        raise ValidationError("cross-case proof task link rejected")
    fact = svc.repo.get_fact_version(fact_key, fact_version)
    if fact is None:
        raise NotFoundError("fact version not found")
    if fact.case_id != case_id:
        raise ValidationError("cross-case fact link rejected")
    return task, fact


def _persist_issue_structure_decision(
    svc: DomainService,
    *,
    case_id: UUID,
    actor_id: UUID,
    decision_type: str,
    target_id: UUID,
    payload: dict[str, Any],
) -> Any:
    """INV-4: HumanDecision must be flushed before merge/split mutations proceed."""
    decision = svc._new_decision(
        case_id=case_id,
        actor_id=actor_id,
        decision_type=decision_type,
        target_type="Issue",
        target_id=target_id,
        result=DecisionResult.CONFIRMED.value,
        payload=payload,
    )
    svc.repo.add_decision(decision)
    svc.repo.flush()
    if decision.id is None:
        raise ConflictError(f"{decision_type} decision failed to persist")
    return decision


def _persist_structure_mutation_audit(
    svc: DomainService,
    *,
    case_id: UUID,
    actor_id: UUID,
    action: str,
    entity_id: UUID,
    decision: Any,
    after: dict[str, Any],
) -> None:
    """INV-4: AuditLog must be flushed before merge/split mutations proceed."""
    payload = {**after, "decision_id": str(decision.id)}
    svc._audit(
        actor_id,
        action,
        "issues",
        entity_id,
        case_id=case_id,
        after=payload,
    )
    svc.repo.flush()


def _require_structure_mutation_audit(
    svc: DomainService,
    *,
    case_id: UUID,
    action: str,
    decision_type: str,
) -> None:
    """INV-4: merge/split must emit HumanDecision + AuditLog before returning."""
    from sqlalchemy import select

    decision = svc.session.scalars(
        select(HumanDecision)
        .where(
            HumanDecision.case_id == case_id,
            HumanDecision.decision_type == decision_type,
        )
        .order_by(HumanDecision.created_at.desc())
        .limit(1)
    ).first()
    if decision is None:
        raise ConflictError(f"{action} must emit HumanDecision before completing")
    audit = svc.session.scalars(
        select(AuditLog)
        .where(
            AuditLog.case_id == case_id,
            AuditLog.action == action,
            AuditLog.entity_type == "issues",
        )
        .order_by(AuditLog.created_at.desc())
        .limit(1)
    ).first()
    if audit is None:
        raise ConflictError(f"{action} must emit AuditLog before completing")
    decision_id = str(decision.id)
    after = audit.after_json or {}
    if after.get("decision_id") != decision_id:
        raise ConflictError(
            f"{action} AuditLog.after_json must reference HumanDecision decision_id"
        )


class IssueCenteredDomainMixin:
    """Issue-centered workspace V2 mutations."""

    session: Any
    repo: Any

    def _require_issue_version(self, issue_key: UUID, issue_version: int) -> Issue:
        issue = self.repo.get_issue_version(issue_key, issue_version)
        if issue is None:
            raise NotFoundError("issue version not found")
        return issue

    # ----- IssuePosition -----

    def propose_position(
        self: DomainService,
        *,
        case_id: UUID,
        issue_key: UUID,
        issue_version: int,
        side: str,
        position_type: str,
        statement: str,
        analyst_run_id: UUID | None = None,
        actor_id: UUID | None = None,
    ) -> IssuePosition:
        """AI may propose OUR assertion or ANTICIPATED_DEFENSE only."""
        self._require_case(case_id)
        issue = self._require_issue_version(issue_key, issue_version)
        if issue.case_id != case_id:
            raise ValidationError("issue case_id mismatch")
        if side not in {PositionSide.OUR.value}:
            raise ValidationError("AI may only propose OUR positions")
        if position_type not in {
            PositionType.ASSERTION.value,
            PositionType.ANTICIPATED_DEFENSE.value,
        }:
            raise ValidationError("AI may not propose FORMAL_DEFENSE")
        pos = IssuePosition(
            position_key=uuid.uuid4(),
            case_id=case_id,
            issue_key=issue_key,
            issue_version=issue_version,
            side=side,
            position_type=position_type,
            source_type=PositionSourceType.AI_PROPOSED.value,
            status=PositionStatus.CANDIDATE.value,
            statement=statement,
            version=1,
            is_current=True,
            analyst_run_id=analyst_run_id,
        )
        self.repo.add(pos)
        self.repo.flush()
        self._audit(
            actor_id or uuid.UUID(int=0),
            "propose_position",
            "issue_positions",
            pos.id,
            case_id=case_id,
            after={"position_key": str(pos.position_key), "status": pos.status},
        )
        return pos

    def create_lawyer_position(
        self: DomainService,
        *,
        case_id: UUID,
        issue_key: UUID,
        issue_version: int,
        side: str,
        position_type: str,
        statement: str,
        actor_id: UUID,
        opponent_material_ref: str | None = None,
    ) -> IssuePosition:
        self._require_case(case_id)
        issue = self._require_issue_version(issue_key, issue_version)
        if issue.case_id != case_id:
            raise ValidationError("issue case_id mismatch")
        opponent_material_ref = _normalize_opponent_material_ref(opponent_material_ref)
        _guard_formal_defense_opponent_material_ref(position_type, opponent_material_ref)
        _guard_formal_defense_side(position_type, side)
        if position_type == PositionType.FORMAL_DEFENSE.value:
            source = PositionSourceType.OPPONENT_MATERIAL.value
        else:
            source = PositionSourceType.LAWYER_CREATED.value
        pos_key = uuid.uuid4()
        decision = self._new_decision(
            case_id=case_id,
            actor_id=actor_id,
            decision_type="CREATE_POSITION",
            target_type="IssuePosition",
            target_id=pos_key,
            result=DecisionResult.CONFIRMED.value,
            payload={"statement": statement, "position_type": position_type},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        pos = IssuePosition(
            position_key=pos_key,
            case_id=case_id,
            issue_key=issue_key,
            issue_version=issue_version,
            side=side,
            position_type=position_type,
            source_type=source,
            status=PositionStatus.CONFIRMED.value,
            statement=statement,
            opponent_material_ref=opponent_material_ref,
            version=1,
            is_current=True,
            confirm_decision_id=decision.id,
        )
        self.repo.add(pos)
        self.repo.flush()
        self._audit(
            actor_id,
            "create_lawyer_position",
            "issue_positions",
            pos.id,
            case_id=case_id,
            after={"position_key": str(pos.position_key), "decision_id": str(decision.id)},
        )
        return pos

    def confirm_position(
        self: DomainService,
        position_key: UUID,
        *,
        actor_id: UUID,
    ) -> IssuePosition:
        pos = self.repo.get_current_position(position_key)
        if pos is None:
            raise NotFoundError("position not found")
        if pos.status != PositionStatus.CANDIDATE.value:
            raise ConflictError("only CANDIDATE positions can be confirmed")
        decision = self._new_decision(
            case_id=pos.case_id,
            actor_id=actor_id,
            decision_type="CONFIRM_POSITION",
            target_type="IssuePosition",
            target_id=pos.position_key,
            result=DecisionResult.CONFIRMED.value,
            payload={"position_key": str(pos.position_key), "version": pos.version},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        pos.status = PositionStatus.CONFIRMED.value
        pos.confirm_decision_id = decision.id
        pos.updated_at = _now()
        self._audit(
            actor_id,
            "confirm_position",
            "issue_positions",
            pos.id,
            case_id=pos.case_id,
            after={"status": pos.status, "decision_id": str(decision.id)},
        )
        return pos

    def reject_position(
        self: DomainService,
        position_key: UUID,
        *,
        actor_id: UUID,
    ) -> IssuePosition:
        pos = self.repo.get_current_position(position_key)
        if pos is None:
            raise NotFoundError("position not found")
        if pos.status != PositionStatus.CANDIDATE.value:
            raise ConflictError("only CANDIDATE positions can be rejected")
        decision = self._new_decision(
            case_id=pos.case_id,
            actor_id=actor_id,
            decision_type="REJECT_POSITION",
            target_type="IssuePosition",
            target_id=pos.position_key,
            result=DecisionResult.REJECTED.value,
            payload={"position_key": str(pos.position_key)},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        pos.status = PositionStatus.REJECTED.value
        pos.updated_at = _now()
        self._audit(
            actor_id,
            "reject_position",
            "issue_positions",
            pos.id,
            case_id=pos.case_id,
            after={"status": pos.status},
        )
        return pos

    # ----- ProofTask -----

    def propose_proof_task(
        self: DomainService,
        *,
        case_id: UUID,
        issue_key: UUID,
        issue_version: int,
        description: str,
        analyst_run_id: UUID | None = None,
        actor_id: UUID | None = None,
    ) -> ProofTask:
        self._require_case(case_id)
        issue = self._require_issue_version(issue_key, issue_version)
        if issue.case_id != case_id:
            raise ValidationError("issue case_id mismatch")
        task = ProofTask(
            proof_task_key=uuid.uuid4(),
            case_id=case_id,
            issue_key=issue_key,
            issue_version=issue_version,
            description=description,
            status=ProofTaskStatus.CANDIDATE.value,
            source_type=ProofTaskSourceType.AI_PROPOSED.value,
            version=1,
            is_current=True,
            analyst_run_id=analyst_run_id,
        )
        self.repo.add(task)
        self.repo.flush()
        self._audit(
            actor_id or uuid.UUID(int=0),
            "propose_proof_task",
            "proof_tasks",
            task.id,
            case_id=case_id,
            after={"proof_task_key": str(task.proof_task_key)},
        )
        return task

    def adopt_proof_task(
        self: DomainService,
        proof_task_key: UUID,
        *,
        actor_id: UUID,
    ) -> ProofTask:
        task = self.repo.get_current_proof_task(proof_task_key)
        if task is None:
            raise NotFoundError("proof task not found")
        if task.status != ProofTaskStatus.CANDIDATE.value:
            raise ConflictError("only CANDIDATE proof tasks can be adopted")
        decision = self._new_decision(
            case_id=task.case_id,
            actor_id=actor_id,
            decision_type="ADOPT_PROOF_TASK",
            target_type="ProofTask",
            target_id=task.proof_task_key,
            result=DecisionResult.CONFIRMED.value,
            payload={"proof_task_key": str(task.proof_task_key)},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        task.status = ProofTaskStatus.ADOPTED.value
        task.confirm_decision_id = decision.id
        task.updated_at = _now()
        self._audit(
            actor_id,
            "adopt_proof_task",
            "proof_tasks",
            task.id,
            case_id=task.case_id,
            after={"status": task.status},
        )
        return task

    def create_lawyer_proof_task(
        self: DomainService,
        *,
        case_id: UUID,
        issue_key: UUID,
        issue_version: int,
        description: str,
        actor_id: UUID,
    ) -> ProofTask:
        self._require_case(case_id)
        issue = self._require_issue_version(issue_key, issue_version)
        if issue.case_id != case_id:
            raise ValidationError("issue case_id mismatch")
        task_key = uuid.uuid4()
        decision = self._new_decision(
            case_id=case_id,
            actor_id=actor_id,
            decision_type="CREATE_PROOF_TASK",
            target_type="ProofTask",
            target_id=task_key,
            result=DecisionResult.CONFIRMED.value,
            payload={"description": description},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        task = ProofTask(
            proof_task_key=task_key,
            case_id=case_id,
            issue_key=issue_key,
            issue_version=issue_version,
            description=description,
            status=ProofTaskStatus.ADOPTED.value,
            source_type=ProofTaskSourceType.LAWYER_CREATED.value,
            version=1,
            is_current=True,
            confirm_decision_id=decision.id,
        )
        self.repo.add(task)
        self.repo.flush()
        self._audit(
            actor_id,
            "create_lawyer_proof_task",
            "proof_tasks",
            task.id,
            case_id=case_id,
            after={"proof_task_key": str(task.proof_task_key)},
        )
        return task

    def waive_proof_task(
        self: DomainService,
        proof_task_key: UUID,
        *,
        actor_id: UUID,
    ) -> ProofTask:
        task = self.repo.get_current_proof_task(proof_task_key)
        if task is None:
            raise NotFoundError("proof task not found")
        if task.status not in {ProofTaskStatus.ADOPTED.value, ProofTaskStatus.CANDIDATE.value}:
            raise ConflictError("proof task cannot be waived in current status")
        decision = self._new_decision(
            case_id=task.case_id,
            actor_id=actor_id,
            decision_type="WAIVE_PROOF_TASK",
            target_type="ProofTask",
            target_id=task.proof_task_key,
            result=DecisionResult.CONFIRMED.value,
            payload={"proof_task_key": str(task.proof_task_key)},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        task.status = ProofTaskStatus.WAIVED.value
        task.updated_at = _now()
        self._audit(
            actor_id,
            "waive_proof_task",
            "proof_tasks",
            task.id,
            case_id=task.case_id,
            after={"status": task.status},
        )
        return task

    def link_fact_to_proof_task(
        self: DomainService,
        *,
        case_id: UUID,
        proof_task_key: UUID,
        proof_task_version: int,
        fact_key: UUID,
        fact_version: int,
        role: str,
        actor_id: UUID,
    ) -> ProofTaskFactLink:
        # INV-2: reject invalid/implicit versions before any DB row lookup.
        _guard_explicit_proof_task_fact_versions(proof_task_version, fact_version)
        self._require_case(case_id)
        task, fact = _resolve_proof_task_and_fact_for_link(
            self,
            case_id=case_id,
            proof_task_key=proof_task_key,
            proof_task_version=proof_task_version,
            fact_key=fact_key,
            fact_version=fact_version,
        )
        _guard_cross_case_proof_task_fact_pair(task, fact)
        if role not in {r.value for r in ProofTaskFactLinkRole}:
            raise ValidationError(f"invalid proof task fact link role: {role}")
        link = ProofTaskFactLink(
            case_id=case_id,
            proof_task_key=proof_task_key,
            proof_task_version=proof_task_version,
            fact_key=fact_key,
            fact_version=fact_version,
            role=role,
            status=IssueLinkStatus.ACTIVE.value,
            created_by=actor_id,
        )
        try:
            self.repo.add(link)
            self.repo.flush()
        except IntegrityError as exc:
            raise ConflictError("duplicate proof task fact link") from exc
        self._audit(
            actor_id,
            "link_fact_to_proof_task",
            "proof_task_fact_links",
            link.id,
            case_id=case_id,
            after={
                "proof_task_key": str(proof_task_key),
                "fact_key": str(fact_key),
                "role": role,
            },
        )
        return link

    # ----- IssueConflict -----

    def create_conflict(
        self: DomainService,
        *,
        case_id: UUID,
        issue_key: UUID,
        issue_version: int,
        description: str,
        source_type: str = ConflictSourceType.AI_DETECTED.value,
        fact_refs: list[dict[str, Any]] | None = None,
        actor_id: UUID | None = None,
        analyst_run_id: UUID | None = None,
    ) -> IssueConflict:
        self._require_case(case_id)
        issue = self._require_issue_version(issue_key, issue_version)
        if issue.case_id != case_id:
            raise ValidationError("issue case_id mismatch")
        conflict = IssueConflict(
            conflict_key=uuid.uuid4(),
            case_id=case_id,
            issue_key=issue_key,
            issue_version=issue_version,
            description=description,
            status=ConflictStatus.OPEN.value
            if source_type == ConflictSourceType.LAWYER_CREATED.value
            else ConflictStatus.CANDIDATE.value,
            source_type=source_type,
            analyst_run_id=analyst_run_id,
        )
        self.repo.add(conflict)
        self.repo.flush()
        for ref in fact_refs or []:
            fk = UUID(str(ref["fact_key"]))
            fv = int(ref["fact_version"])
            role = str(ref.get("role", ConflictFactRole.CONTEXT.value))
            fact = self.repo.get_fact_version(fk, fv)
            if fact is None or fact.case_id != case_id:
                raise ValidationError("invalid conflict fact ref")
            link = ConflictFactLink(
                case_id=case_id,
                conflict_id=conflict.id,
                fact_key=fk,
                fact_version=fv,
                role=role,
            )
            self.repo.add(link)
        self.repo.flush()
        self._audit(
            actor_id or uuid.UUID(int=0),
            "create_conflict",
            "issue_conflicts",
            conflict.id,
            case_id=case_id,
            after={"conflict_key": str(conflict.conflict_key), "status": conflict.status},
        )
        return conflict

    def resolve_conflict(
        self: DomainService,
        conflict_id: UUID,
        *,
        resolution_note: str,
        actor_id: UUID,
    ) -> IssueConflict:
        conflict = self.session.get(IssueConflict, conflict_id)
        if conflict is None:
            raise NotFoundError("conflict not found")
        if conflict.status in {ConflictStatus.RESOLVED.value, ConflictStatus.DISMISSED.value}:
            raise ConflictError("conflict already closed")
        decision = self._new_decision(
            case_id=conflict.case_id,
            actor_id=actor_id,
            decision_type="RESOLVE_CONFLICT",
            target_type="IssueConflict",
            target_id=conflict.conflict_key,
            result=DecisionResult.CONFIRMED.value,
            payload={"resolution_note": resolution_note},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        conflict.status = ConflictStatus.RESOLVED.value
        conflict.resolution_note = resolution_note
        conflict.resolve_decision_id = decision.id
        conflict.updated_at = _now()
        self._audit(
            actor_id,
            "resolve_conflict",
            "issue_conflicts",
            conflict.id,
            case_id=conflict.case_id,
            after={"status": conflict.status},
        )
        return conflict

    def dismiss_conflict(
        self: DomainService,
        conflict_id: UUID,
        *,
        resolution_note: str,
        actor_id: UUID,
    ) -> IssueConflict:
        conflict = self.session.get(IssueConflict, conflict_id)
        if conflict is None:
            raise NotFoundError("conflict not found")
        if conflict.status in {ConflictStatus.RESOLVED.value, ConflictStatus.DISMISSED.value}:
            raise ConflictError("conflict already closed")
        decision = self._new_decision(
            case_id=conflict.case_id,
            actor_id=actor_id,
            decision_type="DISMISS_CONFLICT",
            target_type="IssueConflict",
            target_id=conflict.conflict_key,
            result=DecisionResult.CONFIRMED.value,
            payload={"resolution_note": resolution_note},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        conflict.status = ConflictStatus.DISMISSED.value
        conflict.resolution_note = resolution_note
        conflict.resolve_decision_id = decision.id
        conflict.updated_at = _now()
        self._audit(
            actor_id,
            "dismiss_conflict",
            "issue_conflicts",
            conflict.id,
            case_id=conflict.case_id,
            after={"status": conflict.status},
        )
        return conflict

    # ----- ProofGap -----

    def create_proof_gap(
        self: DomainService,
        *,
        case_id: UUID,
        issue_key: UUID,
        issue_version: int,
        gap_type: str,
        description: str,
        source_type: str = ProofGapSourceType.AI_DETECTED.value,
        proof_task_key: UUID | None = None,
        proof_task_version: int | None = None,
        what_exists: str | None = None,
        what_is_missing: str | None = None,
        why_it_matters: str | None = None,
        suggested_material_types: list[str] | None = None,
        actor_id: UUID | None = None,
        analyst_run_id: UUID | None = None,
    ) -> ProofGap:
        self._require_case(case_id)
        issue = self._require_issue_version(issue_key, issue_version)
        if issue.case_id != case_id:
            raise ValidationError("issue case_id mismatch")
        if gap_type not in {t.value for t in ProofGapType}:
            raise ValidationError(f"invalid gap type: {gap_type}")
        gap = ProofGap(
            gap_key=uuid.uuid4(),
            case_id=case_id,
            issue_key=issue_key,
            issue_version=issue_version,
            proof_task_key=proof_task_key,
            proof_task_version=proof_task_version,
            gap_type=gap_type,
            status=ProofGapStatus.OPEN.value,
            source_type=source_type,
            description=description,
            what_exists=what_exists,
            what_is_missing=what_is_missing,
            why_it_matters=why_it_matters,
            suggested_material_types=suggested_material_types,
            analyst_run_id=analyst_run_id,
        )
        self.repo.add(gap)
        self.repo.flush()
        self._audit(
            actor_id or uuid.UUID(int=0),
            "create_proof_gap",
            "proof_gaps",
            gap.id,
            case_id=case_id,
            after={"gap_key": str(gap.gap_key), "gap_type": gap_type},
        )
        return gap

    def resolve_proof_gap(
        self: DomainService,
        gap_id: UUID,
        *,
        resolution_note: str,
        actor_id: UUID,
    ) -> ProofGap:
        gap = self.session.get(ProofGap, gap_id)
        if gap is None:
            raise NotFoundError("proof gap not found")
        if gap.status != ProofGapStatus.OPEN.value:
            raise ConflictError("only OPEN gaps can be resolved")
        decision = self._new_decision(
            case_id=gap.case_id,
            actor_id=actor_id,
            decision_type="RESOLVE_PROOF_GAP",
            target_type="ProofGap",
            target_id=gap.gap_key,
            result=DecisionResult.CONFIRMED.value,
            payload={"resolution_note": resolution_note},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        gap.status = ProofGapStatus.RESOLVED.value
        gap.resolution_note = resolution_note
        gap.resolve_decision_id = decision.id
        gap.updated_at = _now()
        self._audit(
            actor_id,
            "resolve_proof_gap",
            "proof_gaps",
            gap.id,
            case_id=gap.case_id,
            after={"status": gap.status},
        )
        return gap

    def waive_proof_gap(
        self: DomainService,
        gap_id: UUID,
        *,
        resolution_note: str,
        actor_id: UUID,
    ) -> ProofGap:
        gap = self.session.get(ProofGap, gap_id)
        if gap is None:
            raise NotFoundError("proof gap not found")
        if gap.status != ProofGapStatus.OPEN.value:
            raise ConflictError("only OPEN gaps can be waived")
        decision = self._new_decision(
            case_id=gap.case_id,
            actor_id=actor_id,
            decision_type="WAIVE_PROOF_GAP",
            target_type="ProofGap",
            target_id=gap.gap_key,
            result=DecisionResult.CONFIRMED.value,
            payload={"resolution_note": resolution_note},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        gap.status = ProofGapStatus.WAIVED.value
        gap.resolution_note = resolution_note
        gap.resolve_decision_id = decision.id
        gap.updated_at = _now()
        self._audit(
            actor_id,
            "waive_proof_gap",
            "proof_gaps",
            gap.id,
            case_id=gap.case_id,
            after={"status": gap.status},
        )
        return gap

    # ----- LawyerAssessment (lawyer-only) -----

    def create_lawyer_assessment(
        self: DomainService,
        *,
        case_id: UUID,
        issue_key: UUID,
        issue_version: int,
        content: str,
        actor_id: UUID,
        is_ai_actor: bool = False,
    ) -> LawyerAssessment:
        if is_ai_actor:
            raise ValidationError("AI cannot create LawyerAssessment")
        self._require_case(case_id)
        issue = self._require_issue_version(issue_key, issue_version)
        if issue.case_id != case_id:
            raise ValidationError("issue case_id mismatch")
        assessment_key = uuid.uuid4()
        decision = self._new_decision(
            case_id=case_id,
            actor_id=actor_id,
            decision_type="CREATE_LAWYER_ASSESSMENT",
            target_type="LawyerAssessment",
            target_id=assessment_key,
            result=DecisionResult.CONFIRMED.value,
            payload={"content": content[:200]},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        assessment = LawyerAssessment(
            assessment_key=assessment_key,
            case_id=case_id,
            issue_key=issue_key,
            issue_version=issue_version,
            content=content,
            status=LawyerAssessmentStatus.ACTIVE.value,
            version=1,
            is_current=True,
            confirm_decision_id=decision.id,
        )
        self.repo.add(assessment)
        self.repo.flush()
        self._audit(
            actor_id,
            "create_lawyer_assessment",
            "lawyer_assessments",
            assessment.id,
            case_id=case_id,
            after={"assessment_key": str(assessment_key)},
        )
        return assessment

    def amend_lawyer_assessment(
        self: DomainService,
        assessment_key: UUID,
        *,
        new_content: str,
        actor_id: UUID,
        is_ai_actor: bool = False,
    ) -> LawyerAssessment:
        if is_ai_actor:
            raise ValidationError("AI cannot amend LawyerAssessment")
        old = self.repo.get_current_lawyer_assessment(assessment_key)
        if old is None:
            raise NotFoundError("assessment not found")
        if old.status != LawyerAssessmentStatus.ACTIVE.value:
            raise ConflictError("only ACTIVE assessments can be amended")
        decision = self._new_decision(
            case_id=old.case_id,
            actor_id=actor_id,
            decision_type="AMEND_LAWYER_ASSESSMENT",
            target_type="LawyerAssessment",
            target_id=old.assessment_key,
            result=DecisionResult.AMENDED.value,
            payload={"from_version": old.version},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        old.status = LawyerAssessmentStatus.SUPERSEDED.value
        old.is_current = False
        old.updated_at = _now()
        new_assessment = LawyerAssessment(
            assessment_key=old.assessment_key,
            case_id=old.case_id,
            issue_key=old.issue_key,
            issue_version=old.issue_version,
            content=new_content,
            status=LawyerAssessmentStatus.ACTIVE.value,
            version=old.version + 1,
            is_current=True,
            supersedes_id=old.id,
            confirm_decision_id=decision.id,
        )
        self.repo.add(new_assessment)
        self.repo.flush()
        self._audit(
            actor_id,
            "amend_lawyer_assessment",
            "lawyer_assessments",
            new_assessment.id,
            case_id=old.case_id,
            after={"version": new_assessment.version},
        )
        return new_assessment

    def withdraw_lawyer_assessment(
        self: DomainService,
        assessment_key: UUID,
        *,
        actor_id: UUID,
    ) -> LawyerAssessment:
        assessment = self.repo.get_current_lawyer_assessment(assessment_key)
        if assessment is None:
            raise NotFoundError("assessment not found")
        decision = self._new_decision(
            case_id=assessment.case_id,
            actor_id=actor_id,
            decision_type="WITHDRAW_LAWYER_ASSESSMENT",
            target_type="LawyerAssessment",
            target_id=assessment.assessment_key,
            result=DecisionResult.CONFIRMED.value,
            payload={"assessment_key": str(assessment_key)},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        assessment.status = LawyerAssessmentStatus.WITHDRAWN.value
        assessment.updated_at = _now()
        self._audit(
            actor_id,
            "withdraw_lawyer_assessment",
            "lawyer_assessments",
            assessment.id,
            case_id=assessment.case_id,
            after={"status": assessment.status},
        )
        return assessment

    # ----- Issue merge / split -----

    def merge_issues(
        self: DomainService,
        *,
        case_id: UUID,
        source_issue_keys: list[UUID],
        merged_statement: str,
        actor_id: UUID,
    ) -> Issue:
        if len(source_issue_keys) < 2:
            raise ValidationError("merge requires at least two issues")
        sources: list[Issue] = []
        for key in source_issue_keys:
            issue = self._require_current_issue(key)
            if issue.case_id != case_id:
                raise ValidationError("cross-case merge rejected")
            if issue.status != IssueStatus.CONFIRMED.value:
                raise ValidationError("only CONFIRMED issues can be merged")
            sources.append(issue)

        decision = _persist_issue_structure_decision(
            self,
            case_id=case_id,
            actor_id=actor_id,
            decision_type="MERGE_ISSUES",
            target_id=uuid.uuid4(),
            payload={
                "source_keys": [str(k) for k in source_issue_keys],
                "merged_statement": merged_statement,
            },
        )
        _persist_structure_mutation_audit(
            self,
            case_id=case_id,
            actor_id=actor_id,
            action="merge_issues",
            entity_id=decision.target_id,
            decision=decision,
            after={
                "source_keys": [str(k) for k in source_issue_keys],
                "merged_statement": merged_statement,
            },
        )

        new_key = uuid.uuid4()
        merged = Issue(
            issue_key=new_key,
            case_id=case_id,
            statement=merged_statement,
            order_index=min(i.order_index for i in sources),
            source_type=IssueSourceType.LAWYER_REFINED.value,
            status=IssueStatus.CONFIRMED.value,
            version=1,
            is_current=True,
            confirm_decision_id=decision.id,
        )
        from backend.domain.services import _sync_issue_layer

        _sync_issue_layer(merged)
        self.repo.add(merged)
        self.repo.flush()

        seen_fact_links: set[tuple] = set()
        for src in sources:
            for link in self.repo.list_issue_fact_links(src.issue_key, src.version):
                key = (link.fact_key, link.fact_version, link.role)
                if key in seen_fact_links:
                    continue
                seen_fact_links.add(key)
                self.repo.add(
                    IssueFactLink(
                        case_id=case_id,
                        issue_key=merged.issue_key,
                        issue_version=merged.version,
                        fact_key=link.fact_key,
                        fact_version=link.fact_version,
                        role=link.role,
                        status=IssueLinkStatus.ACTIVE.value,
                        explanation=link.explanation,
                        created_by=actor_id,
                    )
                )
            src.status = IssueStatus.SUPERSEDED.value
            src.is_current = False
            src.updated_at = _now()
            _sync_issue_layer(src)
        self.repo.flush()
        _require_structure_mutation_audit(
            self,
            case_id=case_id,
            action="merge_issues",
            decision_type="MERGE_ISSUES",
        )
        return merged

    def split_issue(
        self: DomainService,
        *,
        case_id: UUID,
        source_issue_key: UUID,
        targets: list[dict[str, Any]],
        actor_id: UUID,
    ) -> list[Issue]:
        """Lawyer explicitly assigns links to target issues."""
        if not targets or len(targets) < 2:
            raise ValidationError("split requires at least two target issues")
        source = self._require_current_issue(source_issue_key)
        if source.case_id != case_id:
            raise ValidationError("cross-case split rejected")
        if source.status != IssueStatus.CONFIRMED.value:
            raise ValidationError("only CONFIRMED issues can be split")

        decision = _persist_issue_structure_decision(
            self,
            case_id=case_id,
            actor_id=actor_id,
            decision_type="SPLIT_ISSUE",
            target_id=source.issue_key,
            payload={"source_key": str(source_issue_key), "target_count": len(targets)},
        )
        _persist_structure_mutation_audit(
            self,
            case_id=case_id,
            actor_id=actor_id,
            action="split_issue",
            entity_id=source.issue_key,
            decision=decision,
            after={
                "source_key": str(source_issue_key),
                "target_count": len(targets),
            },
        )

        from backend.domain.services import _sync_issue_layer

        created: list[Issue] = []
        for idx, target in enumerate(targets):
            statement = str(target["statement"])
            issue = Issue(
                issue_key=uuid.uuid4(),
                case_id=case_id,
                statement=statement,
                order_index=source.order_index + idx,
                source_type=IssueSourceType.LAWYER_REFINED.value,
                status=IssueStatus.CONFIRMED.value,
                version=1,
                is_current=True,
                parent_issue_key=source.issue_key,
                confirm_decision_id=decision.id,
            )
            _sync_issue_layer(issue)
            self.repo.add(issue)
            self.repo.flush()
            for link_spec in target.get("fact_links") or []:
                self.link_fact_to_issue(
                    case_id=case_id,
                    issue_key=issue.issue_key,
                    issue_version=issue.version,
                    fact_key=UUID(str(link_spec["fact_key"])),
                    fact_version=int(link_spec["fact_version"]),
                    role=str(link_spec.get("role", "SUPPORT")),
                    actor_id=actor_id,
                )
            created.append(issue)

        source.status = IssueStatus.SUPERSEDED.value
        source.is_current = False
        source.updated_at = _now()
        _sync_issue_layer(source)
        self.repo.flush()
        _require_structure_mutation_audit(
            self,
            case_id=case_id,
            action="split_issue",
            decision_type="SPLIT_ISSUE",
        )
        return created

    def link_legal_theory_to_issue(
        self: DomainService,
        *,
        case_id: UUID,
        issue_key: UUID,
        issue_version: int,
        legal_theory_id: UUID,
        role: str,
        actor_id: UUID,
    ) -> IssueLegalTheoryLink:
        self._require_case(case_id)
        issue = self._require_issue_version(issue_key, issue_version)
        if issue.case_id != case_id:
            raise ValidationError("issue case_id mismatch")
        link = IssueLegalTheoryLink(
            case_id=case_id,
            issue_key=issue_key,
            issue_version=issue_version,
            legal_theory_id=legal_theory_id,
            role=role,
            status=ClaimLinkStatus.ACTIVE.value,
        )
        self.repo.add(link)
        self.repo.flush()
        self._audit(
            actor_id,
            "link_legal_theory_to_issue",
            "issue_legal_theory_links",
            link.id,
            case_id=case_id,
            after={"legal_theory_id": str(legal_theory_id), "role": role},
        )
        return link
