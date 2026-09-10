"""V2-P3 — Claim / Relief domain foundation tests."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.application.case_analyst import CaseAnalystService
from backend.application.claim_view import ClaimViewService
from backend.application.issue_matrix import IssueMatrixService
from backend.application.pleading_readiness import PleadingReadinessService
from backend.domain.errors import ValidationError
from backend.domain.services import DomainService
from backend.main import app
from backend.models import AuditLog, Claim, HumanDecision
from backend.schemas.case_analyst import (
    AnalystEngineResult,
    ClaimLinkFactProposal,
    ClaimLinkIssueProposal,
    ClaimProposal,
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


def test_a_ai_propose_candidate(db_session, owner_id):
    svc = DomainService(db_session)
    case = svc.create_case(title="Claim AI", owner_user_id=owner_id)
    claim = svc.propose_claim(
        case_id=case.id,
        claim_type="PAYMENT",
        title="支付服务费",
        statement="请求被告支付服务费100000元",
        amount=100000.0,
        currency="CNY",
    )
    assert claim.status == "CANDIDATE"
    assert claim.source_type == "AI_PROPOSED"
    assert claim.amount_is_suggested is True


def test_b_ai_cannot_confirm(db_session, owner_id, actor_id):
    svc = DomainService(db_session)
    case = svc.create_case(title="Gate", owner_user_id=owner_id)
    claim = svc.propose_claim(
        case_id=case.id,
        claim_type="PAYMENT",
        title="诉请",
        statement="候选诉请",
    )
    assert claim.confirm_decision_id is None


def test_c_lawyer_confirm(db_session, owner_id, actor_id):
    svc = DomainService(db_session)
    case = svc.create_case(title="Confirm", owner_user_id=owner_id)
    claim = svc.propose_claim(
        case_id=case.id,
        claim_type="PAYMENT",
        title="支付",
        statement="支付诉请",
        amount=50000.0,
        currency="CNY",
    )
    before = db_session.scalar(select(func.count()).select_from(HumanDecision)) or 0
    confirmed = svc.confirm_claim(claim.claim_key, actor_id=actor_id)
    assert confirmed.status == "CONFIRMED"
    assert confirmed.amount_is_suggested is False
    after = db_session.scalar(select(func.count()).select_from(HumanDecision)) or 0
    assert after == before + 1
    assert db_session.scalar(select(func.count()).select_from(AuditLog)) or 0 > 0


def test_d_lawyer_create(db_session, owner_id, actor_id):
    svc = DomainService(db_session)
    case = svc.create_case(title="Create", owner_user_id=owner_id)
    claim = svc.create_lawyer_claim(
        case_id=case.id,
        claim_type="PAYMENT",
        title="律师创建",
        statement="律师直接创建诉请",
        actor_id=actor_id,
        amount=1000.0,
        currency="CNY",
    )
    assert claim.status == "CONFIRMED"
    assert claim.source_type == "LAWYER_CREATED"


def test_e_amend_v1_to_v2(db_session, owner_id, actor_id):
    svc = DomainService(db_session)
    case = svc.create_case(title="Amend", owner_user_id=owner_id)
    claim = svc.propose_claim(
        case_id=case.id,
        claim_type="PAYMENT",
        title="旧标题",
        statement="旧表述",
    )
    svc.confirm_claim(claim.claim_key, actor_id=actor_id)
    v2 = svc.amend_claim(
        claim.claim_key,
        title="新标题",
        statement="新表述",
        actor_id=actor_id,
    )
    v1 = svc.repo.get_relief_claim_version(claim.claim_key, 1)
    assert v1 and v1.status == "SUPERSEDED" and not v1.is_current
    assert v2.version == 2 and v2.is_current and v2.title == "新标题"


def test_f_reject(db_session, owner_id, actor_id):
    svc = DomainService(db_session)
    case = svc.create_case(title="Reject", owner_user_id=owner_id)
    claim = svc.propose_claim(
        case_id=case.id, claim_type="OTHER", title="驳回", statement="将被驳回"
    )
    rejected = svc.reject_claim(claim.claim_key, actor_id=actor_id)
    assert rejected.status == "REJECTED"


def test_g_issue_link_exact_version(db_session, owner_id, actor_id):
    svc, case, _, _, _, item = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    _seed_confirmed_fact(svc, case_id=case.id, actor_id=actor_id, item=item)
    issue = svc.propose_issue(case_id=case.id, statement="争点")
    svc.confirm_issue(issue.issue_key, actor_id=actor_id)
    issue_cur = svc.repo.get_current_issue(issue.issue_key)
    assert issue_cur
    claim = svc.propose_claim(
        case_id=case.id, claim_type="PAYMENT", title="诉请", statement="基于争点"
    )
    svc.confirm_claim(claim.claim_key, actor_id=actor_id)
    claim_cur = svc.repo.get_current_relief_claim(claim.claim_key)
    assert claim_cur
    svc.link_issue_to_claim(
        case_id=case.id,
        claim_key=claim_cur.claim_key,
        claim_version=claim_cur.version,
        issue_key=issue_cur.issue_key,
        issue_version=issue_cur.version,
        role="BASIS",
        actor_id=actor_id,
    )
    view = ClaimViewService(db_session).build(case.id).items[0]
    assert len(view.basis_issues) == 1
    assert view.basis_issues[0].issue_version == issue_cur.version


def test_h_fact_link_and_cross_case(db_session, owner_id, actor_id):
    svc = DomainService(db_session)
    case_a = svc.create_case(title="A", owner_user_id=owner_id)
    case_b = svc.create_case(title="B", owner_user_id=owner_id)
    _, _, _, _, _, item_b = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id, case=case_b
    )
    fact_b = _seed_confirmed_fact(svc, case_id=case_b.id, actor_id=actor_id, item=item_b)
    claim = svc.create_lawyer_claim(
        case_id=case_a.id,
        claim_type="PAYMENT",
        title="A诉请",
        statement="A",
        actor_id=actor_id,
    )
    with pytest.raises(ValidationError, match="cross-case"):
        svc.link_fact_to_claim(
            case_id=case_a.id,
            claim_key=claim.claim_key,
            claim_version=claim.version,
            fact_key=fact_b.fact_key,
            fact_version=fact_b.version,
            role="BASIS",
            actor_id=actor_id,
        )


def test_i_candidate_readiness_warning(db_session, owner_id):
    svc = DomainService(db_session)
    case = svc.create_case(title="R", owner_user_id=owner_id)
    svc.propose_claim(
        case_id=case.id, claim_type="PAYMENT", title="c", statement="c"
    )
    codes = {
        w.code
        for w in PleadingReadinessService(db_session).evaluate(case.id).warnings
    }
    assert "CLAIM_ONLY_CANDIDATE" in codes


def test_j_confirmed_in_workspace(api_client, db_session, owner_id, actor_id):
    svc = DomainService(db_session)
    case = svc.create_case(title="WS", owner_user_id=owner_id)
    claim = svc.propose_claim(
        case_id=case.id, claim_type="PAYMENT", title="w", statement="w"
    )
    svc.confirm_claim(claim.claim_key, actor_id=actor_id)
    ws = api_client.get(f"/api/cases/{case.id}/workspace").json()
    assert "claims" in ws
    assert ws["claims"]["confirmed_count"] == 1


def test_k_superseded_hidden_by_default(db_session, owner_id, actor_id):
    svc = DomainService(db_session)
    case = svc.create_case(title="Hist", owner_user_id=owner_id)
    claim = svc.propose_claim(
        case_id=case.id, claim_type="PAYMENT", title="v1", statement="v1"
    )
    svc.confirm_claim(claim.claim_key, actor_id=actor_id)
    svc.amend_claim(claim.claim_key, statement="v2", actor_id=actor_id)
    view = ClaimViewService(db_session).build(case.id)
    assert len(view.items) == 1
    assert view.items[0].claim_version == 2
    hist = ClaimViewService(db_session).build(case.id, include_history=True)
    assert len(hist.items) == 2


def test_l_confirm_clears_suggested_amount(db_session, owner_id, actor_id):
    svc = DomainService(db_session)
    case = svc.create_case(title="Amt", owner_user_id=owner_id)
    claim = svc.propose_claim(
        case_id=case.id,
        claim_type="PAYMENT",
        title="金额",
        statement="请求支付",
        amount=99999.0,
        currency="CNY",
    )
    assert claim.amount_is_suggested is True
    confirmed = svc.confirm_claim(claim.claim_key, actor_id=actor_id)
    assert confirmed.amount_is_suggested is False


def test_m_analyst_propose_claim(db_session, owner_id, actor_id):
    _, case, _, _, _, item = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    svc = DomainService(db_session)
    fact = _seed_confirmed_fact(svc, case_id=case.id, actor_id=actor_id, item=item)
    issue = svc.propose_issue(case_id=case.id, statement="争点")
    svc.confirm_issue(issue.issue_key, actor_id=actor_id)
    issue_cur = svc.repo.get_current_issue(issue.issue_key)
    assert issue_cur
    engine = ScriptedCaseAnalystEngine(
        AnalystEngineResult(
            claims=[
                ClaimProposal(
                    proposal_id=uuid.uuid4(),
                    claim_type="PAYMENT",
                    title="Analyst诉请",
                    statement="请求支付服务费",
                    amount=100000.0,
                    currency="CNY",
                    issue_link_proposals=[
                        ClaimLinkIssueProposal(
                            issue_key=issue_cur.issue_key,
                            issue_version=issue_cur.version,
                            role="BASIS",
                        )
                    ],
                    fact_link_proposals=[
                        ClaimLinkFactProposal(
                            fact_key=fact.fact_key,
                            fact_version=fact.version,
                            role="AMOUNT_BASIS",
                        )
                    ],
                )
            ]
        )
    )
    result = CaseAnalystService(db_session, engine=engine).analyze(
        case_id=case.id, accepted_evidence_refs=[_ref(item)], actor_id=actor_id
    )
    assert result.claim_ids
    row = db_session.get(Claim, result.claim_ids[0])
    assert row and row.status == "CANDIDATE"
    view = ClaimViewService(db_session).build(case.id).items[0]
    assert view.amount_is_suggested is True
    assert len(view.basis_issues) == 1


def test_n_issue_matrix_regression(db_session, owner_id):
    svc = DomainService(db_session)
    case = svc.create_case(title="IM reg", owner_user_id=owner_id)
    svc.propose_issue(case_id=case.id, statement="争点")
    assert IssueMatrixService(db_session).build(case.id).candidate_issue_count == 1


def test_o_api_claims(api_client, db_session, owner_id):
    svc = DomainService(db_session)
    case = svc.create_case(title="API", owner_user_id=owner_id)
    svc.propose_claim(
        case_id=case.id, claim_type="PAYMENT", title="api", statement="api"
    )
    res = api_client.get(f"/api/cases/{case.id}/claims")
    assert res.status_code == 200
    assert len(res.json()["items"]) == 1


def test_p_duplicate_issue_link(db_session, owner_id, actor_id):
    svc, case, _, _, _, item = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    issue = svc.propose_issue(case_id=case.id, statement="争点")
    svc.confirm_issue(issue.issue_key, actor_id=actor_id)
    issue_cur = svc.repo.get_current_issue(issue.issue_key)
    claim = svc.create_lawyer_claim(
        case_id=case.id,
        claim_type="PAYMENT",
        title="dup",
        statement="dup",
        actor_id=actor_id,
    )
    assert issue_cur
    svc.link_issue_to_claim(
        case_id=case.id,
        claim_key=claim.claim_key,
        claim_version=claim.version,
        issue_key=issue_cur.issue_key,
        issue_version=issue_cur.version,
        role="BASIS",
        actor_id=actor_id,
    )
    with pytest.raises(IntegrityError):
        svc.link_issue_to_claim(
            case_id=case.id,
            claim_key=claim.claim_key,
            claim_version=claim.version,
            issue_key=issue_cur.issue_key,
            issue_version=issue_cur.version,
            role="BASIS",
            actor_id=actor_id,
        )
