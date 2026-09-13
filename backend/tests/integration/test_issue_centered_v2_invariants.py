"""Issue-centered V2 — explicit code-level assertions for four invariants (Issue #73 SSOT)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from backend.domain.errors import ConflictError, NotFoundError, ValidationError
from backend.domain.services import DomainService
from backend.models import AuditLog, HumanDecision
from backend.tests.integration.test_case_analyst import _seed_accepted_evidence
from backend.tests.integration.test_issue_centered_v2 import _seed_fact


def test_invariant_1_no_production_claim_direction_creation(
    db_session, owner_id, actor_id
) -> None:
    """INV-1: production path must not create ClaimDirection without _legacy_compat."""
    svc = DomainService(db_session)
    case = svc.create_case(title="INV1", owner_user_id=owner_id)
    with pytest.raises(ValidationError, match="ClaimDirection production"):
        svc.create_claim_direction(
            case_id=case.id,
            payload={"claims": [], "parties": {}},
            actor_id=actor_id,
        )
    # Explicit: no ClaimDirection row created
    from backend.models import ClaimDirection

    rows = list(
        db_session.scalars(
            select(ClaimDirection).where(ClaimDirection.case_id == case.id)
        )
    )
    assert rows == []


def test_invariant_2_proof_task_fact_link_rejects_implicit_and_cross_case(
    db_session, owner_id, actor_id
) -> None:
    """INV-2: ProofTaskFactLink requires explicit versions; rejects cross-case links."""
    svc = DomainService(db_session)
    c1 = svc.create_case(title="INV2-A", owner_user_id=owner_id)
    c2 = svc.create_case(title="INV2-B", owner_user_id=owner_id)
    *_, item = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id, case=c1
    )
    fact = _seed_fact(svc, c1.id, actor_id, item)
    issue2 = svc.confirm_issue(
        svc.propose_issue(case_id=c2.id, statement="跨案焦点").issue_key,
        actor_id=actor_id,
    )
    task = svc.create_lawyer_proof_task(
        case_id=c2.id,
        issue_key=issue2.issue_key,
        issue_version=issue2.version,
        description="跨案任务",
        actor_id=actor_id,
    )
    # Implicit/nonexistent proof_task_version → NotFoundError (not silent current/latest)
    with pytest.raises(NotFoundError, match="proof task version not found"):
        svc.link_fact_to_proof_task(
            case_id=c2.id,
            proof_task_key=task.proof_task_key,
            proof_task_version=task.version + 99,
            fact_key=fact.fact_key,
            fact_version=fact.version,
            role="SUPPORT",
            actor_id=actor_id,
        )
    # Implicit/nonexistent fact_version → NotFoundError
    with pytest.raises(NotFoundError, match="fact version not found"):
        svc.link_fact_to_proof_task(
            case_id=c2.id,
            proof_task_key=task.proof_task_key,
            proof_task_version=task.version,
            fact_key=fact.fact_key,
            fact_version=fact.version + 99,
            role="SUPPORT",
            actor_id=actor_id,
        )
    # Cross-case fact → ValidationError
    with pytest.raises(ValidationError, match="cross-case"):
        svc.link_fact_to_proof_task(
            case_id=c2.id,
            proof_task_key=task.proof_task_key,
            proof_task_version=task.version,
            fact_key=fact.fact_key,
            fact_version=fact.version,
            role="SUPPORT",
            actor_id=actor_id,
        )


def test_invariant_3_formal_defense_requires_opponent_material_ref(
    db_session, owner_id, actor_id
) -> None:
    """INV-3: FORMAL_DEFENSE position requires opponent_material_ref."""
    svc = DomainService(db_session)
    case = svc.create_case(title="INV3", owner_user_id=owner_id)
    issue = svc.confirm_issue(
        svc.propose_issue(case_id=case.id, statement="抗辩焦点").issue_key,
        actor_id=actor_id,
    )
    with pytest.raises(ValidationError, match="FORMAL_DEFENSE"):
        svc.create_lawyer_position(
            case_id=case.id,
            issue_key=issue.issue_key,
            issue_version=issue.version,
            side="OPPONENT",
            position_type="FORMAL_DEFENSE",
            statement="对方正式抗辩",
            actor_id=actor_id,
            opponent_material_ref=None,
        )
    pos = svc.create_lawyer_position(
        case_id=case.id,
        issue_key=issue.issue_key,
        issue_version=issue.version,
        side="OPPONENT",
        position_type="FORMAL_DEFENSE",
        statement="对方正式抗辩",
        actor_id=actor_id,
        opponent_material_ref="material:answer-001",
    )
    assert pos.opponent_material_ref == "material:answer-001"
    assert pos.position_type == "FORMAL_DEFENSE"


def test_invariant_4_merge_split_emit_human_decision_and_audit_log(
    db_session, owner_id, actor_id
) -> None:
    """INV-4: merge_issues and split_issue emit HumanDecision + AuditLog."""
    svc = DomainService(db_session)
    case = svc.create_case(title="INV4", owner_user_id=owner_id)
    i1 = svc.confirm_issue(
        svc.propose_issue(case_id=case.id, statement="焦点甲").issue_key,
        actor_id=actor_id,
    )
    i2 = svc.confirm_issue(
        svc.propose_issue(case_id=case.id, statement="焦点乙").issue_key,
        actor_id=actor_id,
    )
    merged = svc.merge_issues(
        case_id=case.id,
        source_issue_keys=[i1.issue_key, i2.issue_key],
        merged_statement="合并焦点",
        actor_id=actor_id,
    )
    assert merged.status == "CONFIRMED"
    merge_decisions = list(
        db_session.scalars(
            select(HumanDecision).where(
                HumanDecision.case_id == case.id,
                HumanDecision.decision_type == "MERGE_ISSUES",
            )
        )
    )
    assert len(merge_decisions) == 1
    merge_audits = list(
        db_session.scalars(
            select(AuditLog).where(
                AuditLog.case_id == case.id,
                AuditLog.action == "merge_issues",
            )
        )
    )
    assert len(merge_audits) >= 1

    split = svc.split_issue(
        case_id=case.id,
        source_issue_key=merged.issue_key,
        targets=[
            {"statement": "拆分甲", "fact_links": []},
            {"statement": "拆分乙", "fact_links": []},
        ],
        actor_id=actor_id,
    )
    assert len(split) == 2
    split_decisions = list(
        db_session.scalars(
            select(HumanDecision).where(
                HumanDecision.case_id == case.id,
                HumanDecision.decision_type == "SPLIT_ISSUE",
            )
        )
    )
    assert len(split_decisions) == 1
    split_audits = list(
        db_session.scalars(
            select(AuditLog).where(
                AuditLog.case_id == case.id,
                AuditLog.action == "split_issue",
            )
        )
    )
    assert len(split_audits) >= 1
