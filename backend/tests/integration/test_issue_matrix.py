"""V2-P2 — Issue Matrix production integration tests."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.application.case_analyst import CaseAnalystService
from backend.application.issue_matrix import IssueMatrixService
from backend.application.pleading_readiness import PleadingReadinessService
from backend.domain.errors import ValidationError
from backend.domain.services import DomainService
from backend.main import app
from backend.models import LegalTheory
from backend.schemas.case_analyst import (
    AnalystEngineResult,
    IssueLinkEvidenceProposal,
    IssueLinkFactProposal,
    IssueProposal,
    LegalTheoryFactRef,
    LegalTheoryProposal,
)
from backend.skills.case_analyst import ScriptedCaseAnalystEngine
from backend.tests.integration.test_case_analyst import _ref, _seed_accepted_evidence
from backend.tests.integration.test_issue_domain import _seed_confirmed_fact


@pytest.fixture
def api_client(db_session: Session):
    from backend.infrastructure.db import get_db_session

    def _override():
        yield db_session

    app.dependency_overrides[get_db_session] = _override
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def test_a_candidate_issue_in_matrix(db_session, owner_id, actor_id):
    svc = DomainService(db_session)
    case = svc.create_case(title="Matrix A", owner_user_id=owner_id)
    svc.propose_issue(case_id=case.id, statement="交付争议")
    matrix = IssueMatrixService(db_session).build(case.id)
    assert len(matrix.items) == 1
    assert matrix.items[0].status == "CANDIDATE"
    assert matrix.items[0].lawyer_confirmation_state == "AI_CANDIDATE"
    assert matrix.candidate_issue_count == 1
    assert matrix.confirmed_issue_count == 0


def test_b_confirmed_issue_in_matrix(db_session, owner_id, actor_id):
    svc = DomainService(db_session)
    case = svc.create_case(title="Matrix B", owner_user_id=owner_id)
    issue = svc.propose_issue(case_id=case.id, statement="付款条件")
    svc.confirm_issue(issue.issue_key, actor_id=actor_id)
    matrix = IssueMatrixService(db_session).build(case.id)
    assert matrix.items[0].status == "CONFIRMED"
    assert matrix.items[0].lawyer_confirmation_state == "LAWYER_CONFIRMED"
    assert matrix.confirmed_issue_count == 1


def test_c_rejected_not_in_default_matrix(db_session, owner_id, actor_id):
    svc = DomainService(db_session)
    case = svc.create_case(title="Matrix C", owner_user_id=owner_id)
    issue = svc.propose_issue(case_id=case.id, statement="将被驳回")
    svc.reject_issue(issue.issue_key, actor_id=actor_id)
    matrix = IssueMatrixService(db_session).build(case.id)
    assert matrix.items == []


def test_d_support_adverse_context_facts(db_session, owner_id, actor_id):
    svc, case, _, _, _, item = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    fact = _seed_confirmed_fact(svc, case_id=case.id, actor_id=actor_id, item=item)
    issue = svc.propose_issue(case_id=case.id, statement="角色分离")
    svc.confirm_issue(issue.issue_key, actor_id=actor_id)
    current = svc.repo.get_current_issue(issue.issue_key)
    assert current
    for role in ("SUPPORT", "ADVERSE", "CONTEXT"):
        svc.link_fact_to_issue(
            case_id=case.id,
            issue_key=current.issue_key,
            issue_version=current.version,
            fact_key=fact.fact_key,
            fact_version=fact.version,
            role=role,
            actor_id=actor_id,
        )
    item_m = IssueMatrixService(db_session).build(case.id).items[0]
    assert len(item_m.supporting_facts) == 1
    assert len(item_m.adverse_facts) == 1
    assert len(item_m.context_facts) == 1
    assert item_m.supporting_facts[0].fact_version == fact.version


def test_e_evidence_roles(db_session, owner_id, actor_id):
    svc, case, _, _, _, item = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    issue = svc.propose_issue(case_id=case.id, statement="证据角色")
    svc.confirm_issue(issue.issue_key, actor_id=actor_id)
    current = svc.repo.get_current_issue(issue.issue_key)
    assert current
    for role in ("SUPPORT", "ADVERSE", "CONTEXT"):
        svc.link_evidence_to_issue(
            case_id=case.id,
            issue_key=current.issue_key,
            issue_version=current.version,
            evidence_item_id=item.id,
            evidence_item_version=item.version,
            role=role,
            actor_id=actor_id,
        )
    item_m = IssueMatrixService(db_session).build(case.id).items[0]
    assert len(item_m.supporting_evidence) == 1
    assert len(item_m.adverse_evidence) == 1
    assert len(item_m.context_evidence) == 1


def test_f_fact_gap(db_session, owner_id, actor_id):
    svc = DomainService(db_session)
    case = svc.create_case(title="Fact gap", owner_user_id=owner_id)
    issue = svc.propose_issue(case_id=case.id, statement="无事实支撑")
    svc.confirm_issue(issue.issue_key, actor_id=actor_id)
    gaps = IssueMatrixService(db_session).build(case.id).items[0].fact_gaps
    assert any(g.type == "FACT_GAP" for g in gaps)


def test_g_evidence_gap(db_session, owner_id, actor_id):
    svc, case, _, _, _, item = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    fact = svc.propose_fact(
        case_id=case.id,
        statement="有事实无有效证据",
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
    fact = svc.repo.get_current_fact(fact.fact_key)
    assert fact
    issue = svc.propose_issue(case_id=case.id, statement="证据缺口")
    svc.confirm_issue(issue.issue_key, actor_id=actor_id)
    current = svc.repo.get_current_issue(issue.issue_key)
    assert current
    svc.link_fact_to_issue(
        case_id=case.id,
        issue_key=current.issue_key,
        issue_version=current.version,
        fact_key=fact.fact_key,
        fact_version=fact.version,
        role="SUPPORT",
        actor_id=actor_id,
    )
    svc.exclude_evidence(item.id, actor_id=actor_id)
    gaps = IssueMatrixService(db_session).build(case.id).items[0].evidence_gaps
    assert any(g.type == "EVIDENCE_GAP" for g in gaps)


def test_h_explicit_versions_in_matrix(db_session, owner_id, actor_id):
    svc, case, _, _, _, item = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    fact = _seed_confirmed_fact(svc, case_id=case.id, actor_id=actor_id, item=item)
    issue = svc.propose_issue(case_id=case.id, statement="版本显式")
    svc.confirm_issue(issue.issue_key, actor_id=actor_id)
    current = svc.repo.get_current_issue(issue.issue_key)
    assert current
    svc.link_fact_to_issue(
        case_id=case.id,
        issue_key=current.issue_key,
        issue_version=current.version,
        fact_key=fact.fact_key,
        fact_version=fact.version,
        role="SUPPORT",
        actor_id=actor_id,
    )
    row = IssueMatrixService(db_session).build(case.id).items[0]
    assert row.issue_version == 1
    assert row.supporting_facts[0].fact_version == fact.version


def test_i_cross_case_link_rejected(db_session, owner_id, actor_id):
    svc = DomainService(db_session)
    case_a = svc.create_case(title="A", owner_user_id=owner_id)
    case_b = svc.create_case(title="B", owner_user_id=owner_id)
    _, _, _, _, _, item_b = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id, case=case_b
    )
    fact_b = _seed_confirmed_fact(svc, case_id=case_b.id, actor_id=actor_id, item=item_b)
    issue_a = svc.propose_issue(case_id=case_a.id, statement="A")
    svc.confirm_issue(issue_a.issue_key, actor_id=actor_id)
    current = svc.repo.get_current_issue(issue_a.issue_key)
    assert current
    with pytest.raises(ValidationError, match="cross-case"):
        svc.link_fact_to_issue(
            case_id=case_a.id,
            issue_key=current.issue_key,
            issue_version=current.version,
            fact_key=fact_b.fact_key,
            fact_version=fact_b.version,
            role="SUPPORT",
            actor_id=actor_id,
        )


def test_j_candidate_not_confirmed_readiness_warning(db_session, owner_id, actor_id):
    svc = DomainService(db_session)
    case = svc.create_case(title="Readiness cand", owner_user_id=owner_id)
    svc.propose_issue(case_id=case.id, statement="仅候选")
    readiness = PleadingReadinessService(db_session).evaluate(case.id)
    codes = {w.code for w in readiness.warnings}
    assert "ISSUE_ONLY_CANDIDATE" in codes


def test_k_confirmed_issue_readiness_no_candidate_warning(
    db_session, owner_id, actor_id
):
    svc = DomainService(db_session)
    case = svc.create_case(title="Readiness ok", owner_user_id=owner_id)
    issue = svc.propose_issue(case_id=case.id, statement="已确认")
    svc.confirm_issue(issue.issue_key, actor_id=actor_id)
    readiness = PleadingReadinessService(db_session).evaluate(case.id)
    codes = {w.code for w in readiness.warnings}
    assert "ISSUE_ONLY_CANDIDATE" not in codes


def test_l_legal_theory_guard(db_session, owner_id, actor_id):
    svc, case, _, _, _, item = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    fact = _seed_confirmed_fact(svc, case_id=case.id, actor_id=actor_id, item=item)
    candidate = svc.propose_fact(
        case_id=case.id,
        statement="候选事实",
        importance="SUPPORTING",
        evidence_links=[
            {
                "evidence_item_id": item.id,
                "evidence_item_version": item.version,
                "link_role": "PROVES",
            }
        ],
        actor_id=actor_id,
    )
    engine = ScriptedCaseAnalystEngine(
        AnalystEngineResult(
            legal_theories=[
                LegalTheoryProposal(
                    proposal_id=uuid.uuid4(),
                    theory_summary="理论候选",
                    supporting_fact_refs=[
                        LegalTheoryFactRef(
                            fact_key=fact.fact_key, fact_version=fact.version
                        ),
                        LegalTheoryFactRef(
                            fact_key=candidate.fact_key, fact_version=candidate.version
                        ),
                    ],
                )
            ]
        )
    )
    result = CaseAnalystService(db_session, engine=engine).analyze(
        case_id=case.id, accepted_evidence_refs=[_ref(item)], actor_id=actor_id
    )
    theory_row = db_session.get(LegalTheory, result.legal_theory_ids[0])
    assert theory_row is not None
    assert theory_row.supporting_fact_ids == [str(fact.fact_key)]
    theories = IssueMatrixService(db_session).build(case.id).legal_theories
    assert len(theories) == 1
    assert theories[0]["supporting_fact_ids"] == [str(fact.fact_key)]


def test_m_analyst_issue_link_proposals(db_session, owner_id, actor_id):
    svc, case, _, _, _, item = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    fact = _seed_confirmed_fact(svc, case_id=case.id, actor_id=actor_id, item=item)
    engine = ScriptedCaseAnalystEngine(
        AnalystEngineResult(
            issues=[
                IssueProposal(
                    proposal_id=uuid.uuid4(),
                    statement="Analyst 链接",
                    fact_link_proposals=[
                        IssueLinkFactProposal(
                            fact_key=fact.fact_key,
                            fact_version=fact.version,
                            role="SUPPORT",
                        )
                    ],
                    evidence_link_proposals=[
                        IssueLinkEvidenceProposal(
                            evidence_item_id=item.id,
                            evidence_item_version=item.version,
                            role="CONTEXT",
                        )
                    ],
                )
            ]
        )
    )
    result = CaseAnalystService(db_session, engine=engine).analyze(
        case_id=case.id, accepted_evidence_refs=[_ref(item)], actor_id=actor_id
    )
    assert result.issue_ids
    matrix = IssueMatrixService(db_session).build(case.id).items[0]
    assert matrix.status == "CANDIDATE"
    assert len(matrix.supporting_facts) == 1
    assert len(matrix.context_evidence) == 1


def test_n_amend_switches_matrix_to_v2(db_session, owner_id, actor_id):
    svc = DomainService(db_session)
    case = svc.create_case(title="Amend matrix", owner_user_id=owner_id)
    issue = svc.propose_issue(case_id=case.id, statement="V1 表述")
    svc.confirm_issue(issue.issue_key, actor_id=actor_id)
    svc.amend_issue(issue.issue_key, new_statement="V2 表述", actor_id=actor_id)
    matrix = IssueMatrixService(db_session).build(case.id)
    assert len(matrix.items) == 1
    assert matrix.items[0].issue_version == 2
    assert matrix.items[0].statement == "V2 表述"
    v1 = svc.repo.get_issue_version(issue.issue_key, 1)
    assert v1 and v1.status == "SUPERSEDED"


def test_o_issue_domain_regression_confirm(db_session, owner_id, actor_id):
    svc = DomainService(db_session)
    case = svc.create_case(title="P1 regression", owner_user_id=owner_id)
    issue = svc.propose_issue(case_id=case.id, statement="回归")
    confirmed = svc.confirm_issue(issue.issue_key, actor_id=actor_id)
    assert confirmed.status == "CONFIRMED"


def test_p_api_issue_matrix(api_client, db_session, owner_id, actor_id):
    svc = DomainService(db_session)
    case = svc.create_case(title="API matrix", owner_user_id=owner_id)
    svc.propose_issue(case_id=case.id, statement="API 可见")
    res = api_client.get(f"/api/cases/{case.id}/issue-matrix")
    assert res.status_code == 200
    body = res.json()
    assert body["case_id"] == str(case.id)
    assert len(body["items"]) == 1
    wrong = api_client.get(f"/api/cases/{uuid.uuid4()}/issue-matrix")
    assert wrong.status_code == 404


def test_q_workspace_includes_issue_matrix(api_client, db_session, owner_id):
    svc = DomainService(db_session)
    case = svc.create_case(title="WS matrix", owner_user_id=owner_id)
    svc.propose_issue(case_id=case.id, statement="工作区")
    ws = api_client.get(f"/api/cases/{case.id}/workspace").json()
    assert "issue_matrix" in ws
    assert "gaps" in ws
    assert len(ws["issue_matrix"]["items"]) == 1
