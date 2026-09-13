"""Issue-centered V2 — four invariant assertions (Issue #73 repair SSOT / #60)."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.application.issue_work_product import (
    IssueWorkProductService,
    assert_read_only_projection,
    enforce_inv1_read_only,
    guard_claim_direction_production_mutation,
    is_read_only_projection,
)
from backend.domain.enums import DecisionResult
from backend.domain.errors import ConflictError, NotFoundError, ValidationError
from backend.domain.issue_centered import (
    ISSUE_CENTERED_V2_INVARIANT_IDS,
    _guard_cross_case_proof_task_fact_pair,
    _guard_explicit_proof_task_fact_versions,
    _guard_formal_defense_opponent_material_ref,
    _guard_formal_defense_side,
    _guard_resolved_explicit_versions,
    _normalize_opponent_material_ref,
    _reject_claim_direction_production_mutation,
    _require_structure_mutation_audit,
    list_issue_centered_v2_invariant_ids,
)
from backend.domain.services import DomainService
from backend.models import AuditLog, HumanDecision, Issue
from backend.tests.integration.test_case_analyst import _seed_accepted_evidence
from backend.tests.integration.test_issue_centered_v2 import _seed_fact


def test_issue73_invariant_registry_complete() -> None:
    """Issue #73 repair: production module exposes all four invariant identifiers."""
    assert list_issue_centered_v2_invariant_ids() == ISSUE_CENTERED_V2_INVARIANT_IDS
    assert ISSUE_CENTERED_V2_INVARIANT_IDS == ("INV-1", "INV-2", "INV-3", "INV-4")


def test_invariant_guard_functions_reject_invalid_inputs() -> None:
    """Direct unit checks on INV-1/2/3 guard helpers (executed code, not prose)."""
    with pytest.raises(ValidationError, match="ClaimDirection production"):
        _reject_claim_direction_production_mutation(_legacy_compat=False)
    _reject_claim_direction_production_mutation(_legacy_compat=True)
    with pytest.raises(ValidationError, match="explicit positive"):
        _guard_explicit_proof_task_fact_versions(0, 1)
    with pytest.raises(ValidationError, match="explicit positive"):
        _guard_explicit_proof_task_fact_versions(1, -1)
    from types import SimpleNamespace

    task = SimpleNamespace(version=2)
    fact = SimpleNamespace(version=1)
    with pytest.raises(ValidationError, match="implicit current/latest rejected"):
        _guard_resolved_explicit_versions(
            proof_task_version=1,
            fact_version=1,
            task=task,
            fact=fact,
        )

    with pytest.raises(ValidationError, match="FORMAL_DEFENSE"):
        _guard_formal_defense_opponent_material_ref("FORMAL_DEFENSE", None)
    with pytest.raises(ValidationError, match="FORMAL_DEFENSE"):
        _guard_formal_defense_opponent_material_ref("FORMAL_DEFENSE", "   ")
    assert _normalize_opponent_material_ref("  material:answer-001  ") == "material:answer-001"
    assert _normalize_opponent_material_ref("   ") is None
    _guard_formal_defense_opponent_material_ref(
        "FORMAL_DEFENSE", "material:answer-001"
    )
    with pytest.raises(ValidationError, match="OPPONENT side"):
        _guard_formal_defense_side("FORMAL_DEFENSE", "OUR")
    _guard_formal_defense_side("FORMAL_DEFENSE", "OPPONENT")
    assert is_read_only_projection() is True
    with pytest.raises(RuntimeError, match="read-only"):
        assert_read_only_projection("claim_directions")
    with pytest.raises(RuntimeError, match="read-only"):
        enforce_inv1_read_only("issues")
    with pytest.raises(RuntimeError, match="read-only"):
        IssueWorkProductService.guard_write_attempt("claim_directions")
    with pytest.raises(ValidationError, match="ClaimDirection production"):
        guard_claim_direction_production_mutation(_legacy_compat=False)
    guard_claim_direction_production_mutation(_legacy_compat=True)


def test_invariant_1_no_production_claim_direction_creation(
    db_session, owner_id, actor_id
) -> None:
    """INV-1: production path must not create ClaimDirection without _legacy_compat."""
    svc = DomainService(db_session)
    case = svc.create_case(title="INV1", owner_user_id=owner_id)
    with patch(
        "backend.domain.services._reject_claim_direction_production_mutation",
        wraps=_reject_claim_direction_production_mutation,
    ) as guard:
        with pytest.raises(ValidationError, match="ClaimDirection production"):
            svc.create_claim_direction(
                case_id=case.id,
                payload={"claims": [], "parties": {}},
                actor_id=actor_id,
            )
        guard.assert_called_once_with(_legacy_compat=False)
    # Explicit: no ClaimDirection row created
    from backend.models import ClaimDirection

    rows = list(
        db_session.scalars(
            select(ClaimDirection).where(ClaimDirection.case_id == case.id)
        )
    )
    assert rows == []


def test_invariant_1_amend_claim_direction_blocked_without_legacy_compat(
    db_session, owner_id, actor_id
) -> None:
    """INV-1: amend_claim_direction creates ClaimDirection rows and must honor the guard."""
    svc = DomainService(db_session)
    case = svc.create_case(title="INV1-amend", owner_user_id=owner_id)
    *_, item = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id, case=case
    )
    fact = _seed_fact(svc, case.id, actor_id, item)
    payload = {
        "overall_strategy": "主张价款",
        "claims": [
            {
                "claim_type": "PAYMENT",
                "description": "支付设计费",
                "amount": 100,
                "currency": "CNY",
                "supporting_fact_ids": [str(fact.fact_key)],
            }
        ],
    }
    claim = svc.create_claim_direction(
        _legacy_compat=True,
        case_id=case.id,
        payload=payload,
        actor_id=actor_id,
    )
    claim = svc.confirm_claim_direction(claim.claim_direction_key, actor_id=actor_id)
    new_payload = {
        **payload,
        "overall_strategy": "修订策略",
        "claims": [{**payload["claims"][0], "amount": 200}],
    }
    with patch(
        "backend.domain.services._reject_claim_direction_production_mutation",
        wraps=_reject_claim_direction_production_mutation,
    ) as guard:
        with pytest.raises(ValidationError, match="ClaimDirection production"):
            svc.amend_claim_direction(
                claim.claim_direction_key,
                payload=new_payload,
                actor_id=actor_id,
            )
        guard.assert_called_once_with(_legacy_compat=False)


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
    # Zero/negative versions → ValidationError (explicit positive versions required)
    with pytest.raises(ValidationError, match="explicit positive"):
        svc.link_fact_to_proof_task(
            case_id=c2.id,
            proof_task_key=task.proof_task_key,
            proof_task_version=0,
            fact_key=fact.fact_key,
            fact_version=fact.version,
            role="SUPPORT",
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
    with pytest.raises(ValidationError, match="cross-case fact link rejected"):
        svc.link_fact_to_proof_task(
            case_id=c2.id,
            proof_task_key=task.proof_task_key,
            proof_task_version=task.version,
            fact_key=fact.fact_key,
            fact_version=fact.version,
            role="SUPPORT",
            actor_id=actor_id,
        )
    # Cross-case proof task (task in c2, link attempted under c1) → ValidationError
    issue1 = svc.confirm_issue(
        svc.propose_issue(case_id=c1.id, statement="案A焦点").issue_key,
        actor_id=actor_id,
    )
    task1 = svc.create_lawyer_proof_task(
        case_id=c1.id,
        issue_key=issue1.issue_key,
        issue_version=issue1.version,
        description="案A任务",
        actor_id=actor_id,
    )
    fact2 = _seed_fact(svc, c2.id, actor_id, item)
    with pytest.raises(ValidationError, match="cross-case proof task link rejected"):
        svc.link_fact_to_proof_task(
            case_id=c2.id,
            proof_task_key=task1.proof_task_key,
            proof_task_version=task1.version,
            fact_key=fact2.fact_key,
            fact_version=fact2.version,
            role="SUPPORT",
            actor_id=actor_id,
        )


def test_invariant_2_link_fact_to_proof_task_never_uses_current_resolution(
    db_session, owner_id, actor_id
) -> None:
    """INV-2: link_fact_to_proof_task must resolve via explicit version lookups only."""
    svc = DomainService(db_session)
    case = svc.create_case(title="INV2-explicit", owner_user_id=owner_id)
    *_, item = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id, case=case
    )
    fact = _seed_fact(svc, case.id, actor_id, item)
    issue = svc.confirm_issue(
        svc.propose_issue(case_id=case.id, statement="显式版本焦点").issue_key,
        actor_id=actor_id,
    )
    task = svc.create_lawyer_proof_task(
        case_id=case.id,
        issue_key=issue.issue_key,
        issue_version=issue.version,
        description="显式版本任务",
        actor_id=actor_id,
    )

    def _reject_current(*_args, **_kwargs):
        raise AssertionError("implicit current/latest resolution rejected")

    with patch.object(svc.repo, "get_current_proof_task", side_effect=_reject_current):
        with patch.object(svc.repo, "get_current_fact", side_effect=_reject_current):
            link = svc.link_fact_to_proof_task(
                case_id=case.id,
                proof_task_key=task.proof_task_key,
                proof_task_version=task.version,
                fact_key=fact.fact_key,
                fact_version=fact.version,
                role="SUPPORT",
                actor_id=actor_id,
            )
    assert link.proof_task_version == task.version
    assert link.fact_version == fact.version


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


def test_invariant_3_formal_defense_rejects_whitespace_only_material_ref(
    db_session, owner_id, actor_id
) -> None:
    """INV-3: whitespace-only opponent_material_ref is normalized then rejected."""
    svc = DomainService(db_session)
    case = svc.create_case(title="INV3-ws", owner_user_id=owner_id)
    issue = svc.confirm_issue(
        svc.propose_issue(case_id=case.id, statement="空白材料焦点").issue_key,
        actor_id=actor_id,
    )
    with pytest.raises(ValidationError, match="FORMAL_DEFENSE"):
        svc.create_lawyer_position(
            case_id=case.id,
            issue_key=issue.issue_key,
            issue_version=issue.version,
            side="OPPONENT",
            position_type="FORMAL_DEFENSE",
            statement="空白材料抗辩",
            actor_id=actor_id,
            opponent_material_ref="   \t  ",
        )


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
    assert merge_decisions[0].id is not None
    assert merge_decisions[0].decision_type == "MERGE_ISSUES"
    assert merge_decisions[0].result == "CONFIRMED"
    merge_audits = list(
        db_session.scalars(
            select(AuditLog).where(
                AuditLog.case_id == case.id,
                AuditLog.action == "merge_issues",
            )
        )
    )
    assert len(merge_audits) >= 1
    assert merge_audits[0].entity_type == "issues"
    assert merge_audits[0].after_json is not None
    assert merge_audits[0].after_json.get("decision_id") == str(merge_decisions[0].id)
    assert merge_audits[0].after_json.get("merged_statement") == "合并焦点"

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
    assert split_decisions[0].id is not None
    assert split_decisions[0].decision_type == "SPLIT_ISSUE"
    assert split_decisions[0].result == "CONFIRMED"
    split_audits = list(
        db_session.scalars(
            select(AuditLog).where(
                AuditLog.case_id == case.id,
                AuditLog.action == "split_issue",
            )
        )
    )
    assert len(split_audits) >= 1
    assert split_audits[0].entity_type == "issues"
    assert split_audits[0].after_json is not None
    assert split_audits[0].after_json.get("decision_id") == str(split_decisions[0].id)
    assert split_audits[0].after_json.get("target_count") == 2


def test_invariant_4_merge_persists_decision_and_audit_before_issue_mutation(
    db_session, owner_id, actor_id
) -> None:
    """INV-4: HumanDecision + AuditLog exist before merge mutates issue rows."""
    svc = DomainService(db_session)
    case = svc.create_case(title="INV4-order", owner_user_id=owner_id)
    i1 = svc.confirm_issue(
        svc.propose_issue(case_id=case.id, statement="顺序甲").issue_key,
        actor_id=actor_id,
    )
    i2 = svc.confirm_issue(
        svc.propose_issue(case_id=case.id, statement="顺序乙").issue_key,
        actor_id=actor_id,
    )
    observed: list[str] = []
    original_add = svc.repo.add

    def _tracking_add(entity):
        if isinstance(entity, Issue) and entity.statement == "顺序合并":
            merge_decisions = list(
                db_session.scalars(
                    select(HumanDecision).where(
                        HumanDecision.case_id == case.id,
                        HumanDecision.decision_type == "MERGE_ISSUES",
                    )
                )
            )
            merge_audits = list(
                db_session.scalars(
                    select(AuditLog).where(
                        AuditLog.case_id == case.id,
                        AuditLog.action == "merge_issues",
                    )
                )
            )
            assert len(merge_decisions) == 1
            assert merge_decisions[0].id is not None
            assert len(merge_audits) == 1
            assert merge_audits[0].after_json is not None
            assert merge_audits[0].after_json.get("decision_id") == str(
                merge_decisions[0].id
            )
            observed.append("preflight")
        return original_add(entity)

    with patch.object(svc.repo, "add", side_effect=_tracking_add):
        svc.merge_issues(
            case_id=case.id,
            source_issue_keys=[i1.issue_key, i2.issue_key],
            merged_statement="顺序合并",
            actor_id=actor_id,
        )
    assert observed == ["preflight"]


def test_invariant_2_cross_case_pair_guard_rejects_mismatched_cases() -> None:
    """INV-2: _guard_cross_case_proof_task_fact_pair rejects mismatched case_id."""
    from types import SimpleNamespace

    task = SimpleNamespace(case_id=uuid.uuid4())
    fact = SimpleNamespace(case_id=uuid.uuid4())
    with pytest.raises(ValidationError, match="cross-case proof task fact link rejected"):
        _guard_cross_case_proof_task_fact_pair(task, fact)


def test_invariant_3_formal_defense_rejects_wrong_side(
    db_session, owner_id, actor_id
) -> None:
    """INV-3: FORMAL_DEFENSE must be recorded on OPPONENT side."""
    svc = DomainService(db_session)
    case = svc.create_case(title="INV3-side", owner_user_id=owner_id)
    issue = svc.confirm_issue(
        svc.propose_issue(case_id=case.id, statement="抗辩侧焦点").issue_key,
        actor_id=actor_id,
    )
    with pytest.raises(ValidationError, match="OPPONENT side"):
        svc.create_lawyer_position(
            case_id=case.id,
            issue_key=issue.issue_key,
            issue_version=issue.version,
            side="OUR",
            position_type="FORMAL_DEFENSE",
            statement="错误侧正式抗辩",
            actor_id=actor_id,
            opponent_material_ref="material:answer-001",
        )


def test_invariant_4_guard_rejects_missing_human_decision_or_audit(
    db_session, owner_id, actor_id
) -> None:
    """INV-4: _require_structure_mutation_audit fails closed without HumanDecision or AuditLog."""
    svc = DomainService(db_session)
    case = svc.create_case(title="INV4-guard", owner_user_id=owner_id)
    with pytest.raises(ConflictError, match="HumanDecision"):
        _require_structure_mutation_audit(
            svc,
            case_id=case.id,
            action="merge_issues",
            decision_type="MERGE_ISSUES",
        )
    decision = HumanDecision(
        case_id=case.id,
        actor_id=actor_id,
        decision_type="MERGE_ISSUES",
        target_type="Issue",
        target_id=uuid.uuid4(),
        result=DecisionResult.CONFIRMED.value,
        input_payload_json={"probe": True},
    )
    db_session.add(decision)
    db_session.flush()
    with pytest.raises(ConflictError, match="AuditLog"):
        _require_structure_mutation_audit(
            svc,
            case_id=case.id,
            action="merge_issues",
            decision_type="MERGE_ISSUES",
        )


def test_invariant_4_guard_rejects_audit_without_decision_id_linkage(
    db_session, owner_id, actor_id
) -> None:
    """INV-4: AuditLog.after_json must reference the emitted HumanDecision id."""
    svc = DomainService(db_session)
    case = svc.create_case(title="INV4-audit-link", owner_user_id=owner_id)
    decision = HumanDecision(
        case_id=case.id,
        actor_id=actor_id,
        decision_type="MERGE_ISSUES",
        target_type="Issue",
        target_id=uuid.uuid4(),
        result=DecisionResult.CONFIRMED.value,
        input_payload_json={"probe": True},
    )
    db_session.add(decision)
    db_session.flush()
    db_session.add(
        AuditLog(
            case_id=case.id,
            actor_id=actor_id,
            action="merge_issues",
            entity_type="issues",
            entity_id=uuid.uuid4(),
            after_json={"issue_key": str(uuid.uuid4())},
        )
    )
    db_session.flush()
    with pytest.raises(ConflictError, match="decision_id"):
        _require_structure_mutation_audit(
            svc,
            case_id=case.id,
            action="merge_issues",
            decision_type="MERGE_ISSUES",
        )


def test_invariant_2_db_rejects_nonpositive_proof_task_fact_versions(
    db_session, owner_id, actor_id
) -> None:
    """INV-2: DB check constraints reject proof_task_version/fact_version < 1."""
    from backend.models import ProofTaskFactLink

    svc = DomainService(db_session)
    case = svc.create_case(title="INV2-DB", owner_user_id=owner_id)
    issue = svc.confirm_issue(
        svc.propose_issue(case_id=case.id, statement="DB约束焦点").issue_key,
        actor_id=actor_id,
    )
    task = svc.create_lawyer_proof_task(
        case_id=case.id,
        issue_key=issue.issue_key,
        issue_version=issue.version,
        description="DB约束任务",
        actor_id=actor_id,
    )
    *_, item = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id, case=case
    )
    fact = _seed_fact(svc, case.id, actor_id, item)
    with pytest.raises(IntegrityError):
        db_session.add(
            ProofTaskFactLink(
                case_id=case.id,
                proof_task_key=task.proof_task_key,
                proof_task_version=0,
                fact_key=fact.fact_key,
                fact_version=fact.version,
                role="SUPPORT",
                status="ACTIVE",
                created_by=actor_id,
            )
        )
        db_session.flush()
    db_session.rollback()


def test_invariant_3_db_rejects_formal_defense_without_material_ref(
    db_session, owner_id, actor_id
) -> None:
    """INV-3: DB check constraint rejects FORMAL_DEFENSE without opponent_material_ref."""
    from backend.models import IssuePosition

    svc = DomainService(db_session)
    case = svc.create_case(title="INV3-DB", owner_user_id=owner_id)
    issue = svc.confirm_issue(
        svc.propose_issue(case_id=case.id, statement="DB抗辩焦点").issue_key,
        actor_id=actor_id,
    )
    with pytest.raises(IntegrityError):
        db_session.add(
            IssuePosition(
                position_key=uuid.uuid4(),
                case_id=case.id,
                issue_key=issue.issue_key,
                issue_version=issue.version,
                side="OPPONENT",
                position_type="FORMAL_DEFENSE",
                source_type="OPPONENT_MATERIAL",
                status="CONFIRMED",
                statement="无材料引用",
                opponent_material_ref=None,
                version=1,
                is_current=True,
            )
        )
        db_session.flush()
    db_session.rollback()
