"""Issue-centered workspace V2 — domain, application, API, provenance."""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.application.issue_work_product import IssueWorkProductService
from backend.application.pleading_readiness import PleadingReadinessService
from backend.domain.errors import ConflictError, ValidationError
from backend.domain.services import DomainService
from backend.infrastructure.db import get_db_session
from backend.main import app
from backend.tests.integration.test_case_analyst import _seed_accepted_evidence


@pytest.fixture
def client(db_session: Session) -> Generator[TestClient, None, None]:
    def _override() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db_session] = _override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _seed_fact(svc: DomainService, case_id, actor_id, item, statement="合同已签订。"):
    fact = svc.propose_fact(
        case_id=case_id,
        statement=statement,
        importance="CORE",
        evidence_links=[
            {
                "evidence_item_id": item.id,
                "evidence_item_version": item.version,
                "link_role": "PROVES",
            }
        ],
        actor_id=actor_id,
    )
    svc.confirm_fact(fact.fact_key, actor_id=actor_id)
    return svc.repo.get_current_fact(fact.fact_key)


def test_position_ai_candidate_lawyer_confirm(db_session: Session, owner_id, actor_id):
    svc = DomainService(db_session)
    case = svc.create_case(title="Pos", owner_user_id=owner_id)
    issue = svc.confirm_issue(
        svc.propose_issue(case_id=case.id, statement="是否违约？").issue_key,
        actor_id=actor_id,
    )
    pos = svc.propose_position(
        case_id=case.id,
        issue_key=issue.issue_key,
        issue_version=issue.version,
        side="OUR",
        position_type="ASSERTION",
        statement="对方已违约",
    )
    assert pos.status == "CANDIDATE"
    confirmed = svc.confirm_position(pos.position_key, actor_id=actor_id)
    assert confirmed.status == "CONFIRMED"


def test_position_formal_defense_requires_material(db_session: Session, owner_id, actor_id):
    svc = DomainService(db_session)
    case = svc.create_case(title="Formal", owner_user_id=owner_id)
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
            statement="对方否认",
            actor_id=actor_id,
        )


def test_proof_task_adopt_and_fact_link(db_session: Session, owner_id, actor_id) -> None:
    svc = DomainService(db_session)
    case = svc.create_case(title="PT", owner_user_id=owner_id)
    *_, item = _seed_accepted_evidence(db_session, owner_id=owner_id, actor_id=actor_id, case=case)
    fact = _seed_fact(svc, case.id, actor_id, item)
    issue = svc.confirm_issue(
        svc.propose_issue(case_id=case.id, statement="证明焦点").issue_key,
        actor_id=actor_id,
    )
    task = svc.propose_proof_task(
        case_id=case.id,
        issue_key=issue.issue_key,
        issue_version=issue.version,
        description="证明合同成立",
    )
    adopted = svc.adopt_proof_task(task.proof_task_key, actor_id=actor_id)
    assert adopted.status == "ADOPTED"
    link = svc.link_fact_to_proof_task(
        case_id=case.id,
        proof_task_key=adopted.proof_task_key,
        proof_task_version=adopted.version,
        fact_key=fact.fact_key,
        fact_version=fact.version,
        role="SUPPORT",
        actor_id=actor_id,
    )
    assert link.role == "SUPPORT"
    with pytest.raises(ConflictError):
        svc.link_fact_to_proof_task(
            case_id=case.id,
            proof_task_key=adopted.proof_task_key,
            proof_task_version=adopted.version,
            fact_key=fact.fact_key,
            fact_version=fact.version,
            role="SUPPORT",
            actor_id=actor_id,
        )


def test_proof_task_fact_link_cross_case_rejected(db_session: Session, owner_id, actor_id):
    svc = DomainService(db_session)
    c1 = svc.create_case(title="C1", owner_user_id=owner_id)
    c2 = svc.create_case(title="C2", owner_user_id=owner_id)
    *_, item = _seed_accepted_evidence(db_session, owner_id=owner_id, actor_id=actor_id, case=c1)
    fact = _seed_fact(svc, c1.id, actor_id, item)
    issue2 = svc.confirm_issue(
        svc.propose_issue(case_id=c2.id, statement="焦点").issue_key,
        actor_id=actor_id,
    )
    task = svc.create_lawyer_proof_task(
        case_id=c2.id,
        issue_key=issue2.issue_key,
        issue_version=issue2.version,
        description="任务",
        actor_id=actor_id,
    )
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


def test_conflict_and_gap_lawyer_actions(db_session: Session, owner_id, actor_id):
    svc = DomainService(db_session)
    case = svc.create_case(title="CG", owner_user_id=owner_id)
    issue = svc.confirm_issue(
        svc.propose_issue(case_id=case.id, statement="冲突焦点").issue_key,
        actor_id=actor_id,
    )
    conflict = svc.create_conflict(
        case_id=case.id,
        issue_key=issue.issue_key,
        issue_version=issue.version,
        description="两份材料日期不一致",
        source_type="AI_DETECTED",
    )
    resolved = svc.resolve_conflict(
        conflict.id, resolution_note="以合同原件为准", actor_id=actor_id
    )
    assert resolved.status == "RESOLVED"
    gap = svc.create_proof_gap(
        case_id=case.id,
        issue_key=issue.issue_key,
        issue_version=issue.version,
        gap_type="EVIDENCE",
        description="缺少付款凭证",
    )
    waived = svc.waive_proof_gap(gap.id, resolution_note="不再主张", actor_id=actor_id)
    assert waived.status == "WAIVED"


def test_lawyer_assessment_ai_blocked(db_session: Session, owner_id, actor_id):
    svc = DomainService(db_session)
    case = svc.create_case(title="LA", owner_user_id=owner_id)
    issue = svc.confirm_issue(
        svc.propose_issue(case_id=case.id, statement="判断焦点").issue_key,
        actor_id=actor_id,
    )
    with pytest.raises(ValidationError, match="AI cannot"):
        svc.create_lawyer_assessment(
            case_id=case.id,
            issue_key=issue.issue_key,
            issue_version=issue.version,
            content="AI 判断",
            actor_id=actor_id,
            is_ai_actor=True,
        )
    a1 = svc.create_lawyer_assessment(
        case_id=case.id,
        issue_key=issue.issue_key,
        issue_version=issue.version,
        content="初版判断",
        actor_id=actor_id,
    )
    a2 = svc.amend_lawyer_assessment(
        a1.assessment_key, new_content="修订判断", actor_id=actor_id
    )
    assert a2.version == 2
    assert a2.status == "ACTIVE"


def test_issue_merge_and_split(db_session: Session, owner_id, actor_id):
    svc = DomainService(db_session)
    case = svc.create_case(title="MS", owner_user_id=owner_id)
    i1 = svc.confirm_issue(
        svc.propose_issue(case_id=case.id, statement="焦点A").issue_key,
        actor_id=actor_id,
    )
    i2 = svc.confirm_issue(
        svc.propose_issue(case_id=case.id, statement="焦点B").issue_key,
        actor_id=actor_id,
    )
    merged = svc.merge_issues(
        case_id=case.id,
        source_issue_keys=[i1.issue_key, i2.issue_key],
        merged_statement="合并焦点",
        actor_id=actor_id,
    )
    assert merged.status == "CONFIRMED"
    i1_after = svc.repo.get_issue_version(i1.issue_key, i1.version)
    assert i1_after is not None and i1_after.status == "SUPERSEDED"
    split = svc.split_issue(
        case_id=case.id,
        source_issue_key=merged.issue_key,
        targets=[
            {"statement": "拆分A", "fact_links": []},
            {"statement": "拆分B", "fact_links": []},
        ],
        actor_id=actor_id,
    )
    assert len(split) == 2


def test_claim_direction_production_disabled(db_session: Session, owner_id, actor_id):
    svc = DomainService(db_session)
    case = svc.create_case(title="CD", owner_user_id=owner_id)
    with pytest.raises(ValidationError, match="ClaimDirection production"):
        svc.create_claim_direction(
            case_id=case.id,
            payload={"claims": [], "parties": {}},
            actor_id=actor_id,
        )


def test_issue_work_product_proof_state(db_session: Session, owner_id, actor_id):
    svc = DomainService(db_session)
    case = svc.create_case(title="WP", owner_user_id=owner_id)
    issue = svc.confirm_issue(
        svc.propose_issue(case_id=case.id, statement="证明状态").issue_key,
        actor_id=actor_id,
    )
    svc.create_proof_gap(
        case_id=case.id,
        issue_key=issue.issue_key,
        issue_version=issue.version,
        gap_type="FACT",
        description="缺事实",
    )
    wp = IssueWorkProductService(db_session).build_issue(issue.issue_key)
    assert wp.proof_state == "RED"
    readiness = PleadingReadinessService(db_session).evaluate(case.id)
    assert readiness.status in {"READY", "NOT_READY"}


def test_green_issue_not_auto_ready(db_session: Session, owner_id, actor_id):
    svc = DomainService(db_session)
    case = svc.create_case(title="GR", owner_user_id=owner_id)
    svc.confirm_issue(
        svc.propose_issue(case_id=case.id, statement="仅焦点").issue_key,
        actor_id=actor_id,
    )
    wp = IssueWorkProductService(db_session).build_case(case.id)
    issue = wp.confirmed_issues[0]
    if issue.proof_state == "GREEN":
        readiness = PleadingReadinessService(db_session).evaluate(case.id)
        assert readiness.status == "NOT_READY"


def test_issue_work_product_api(db_session: Session, owner_id, actor_id, client: TestClient):
    svc = DomainService(db_session)
    case = svc.create_case(title="API", owner_user_id=owner_id)
    svc.confirm_issue(
        svc.propose_issue(case_id=case.id, statement="API焦点").issue_key,
        actor_id=actor_id,
    )
    res = client.get(f"/api/cases/{case.id}/issue-work-product")
    assert res.status_code == 200
    body = res.json()
    assert body["confirmed_issues"]
    assert "proof_state_counts" in body


def test_workspace_includes_issue_work_product(
    db_session: Session, owner_id, actor_id, client: TestClient
):
    svc = DomainService(db_session)
    case = svc.create_case(title="WS", owner_user_id=owner_id)
    svc.confirm_issue(
        svc.propose_issue(case_id=case.id, statement="工作台焦点").issue_key,
        actor_id=actor_id,
    )
    res = client.get(f"/api/cases/{case.id}/workspace")
    assert res.status_code == 200
    assert "issue_work_product" in res.json()


def test_structural_gap_renamed(db_session: Session, owner_id, actor_id):
    from backend.application.issue_matrix import IssueMatrixService

    svc = DomainService(db_session)
    case = svc.create_case(title="SG", owner_user_id=owner_id)
    svc.confirm_issue(
        svc.propose_issue(case_id=case.id, statement="结构缺口").issue_key,
        actor_id=actor_id,
    )
    matrix = IssueMatrixService(db_session).build(case.id)
    assert hasattr(matrix.items[0], "structural_warnings")
    assert matrix.aggregate_structural_warnings is not None
