"""V2-P1 — Issue domain foundation: versioning, links, human gates."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.application.case_analyst import CaseAnalystService
from backend.application.pleading_readiness import PleadingReadinessService
from backend.domain.errors import NotFoundError, ValidationError
from backend.domain.services import DomainService
from backend.models import (
    AuditLog,
    FactEvidenceLink,
    HumanDecision,
    Issue,
    IssueFactLink,
    LegalTheory,
)
from backend.schemas.case_analyst import (
    AnalystEngineResult,
    IssueProposal,
    LegalTheoryProposal,
)
from backend.skills.case_analyst import ScriptedCaseAnalystEngine
from backend.tests.integration.test_case_analyst import _ref, _seed_accepted_evidence


def _seed_confirmed_fact(
    svc: DomainService,
    *,
    case_id: uuid.UUID,
    actor_id: uuid.UUID,
    item,
    statement: str = "双方于2025年3月1日签订合同。",
):
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


def test_a_ai_proposal_creates_candidate(db_session: Session, owner_id, actor_id) -> None:
    svc = DomainService(db_session)
    case = svc.create_case(title="Issue AI", owner_user_id=owner_id)
    issue = svc.propose_issue(
        case_id=case.id,
        statement="是否已完成交付？",
        analyst_run_id=uuid.uuid4(),
    )
    assert issue.status == "CANDIDATE"
    assert issue.source_type == "AI_PROPOSED"
    assert issue.version == 1
    assert issue.is_current is True
    assert issue.layer == "CANDIDATE"


def test_b_ai_cannot_create_confirmed(db_session: Session, owner_id) -> None:
    svc = DomainService(db_session)
    case = svc.create_case(title="Issue gate", owner_user_id=owner_id)
    issue = svc.propose_issue(case_id=case.id, statement="候选焦点")
    assert issue.status != "CONFIRMED"
    assert issue.confirm_decision_id is None


def test_c_lawyer_confirm_creates_decision_and_audit(
    db_session: Session, owner_id, actor_id
) -> None:
    svc = DomainService(db_session)
    case = svc.create_case(title="Confirm issue", owner_user_id=owner_id)
    issue = svc.propose_issue(case_id=case.id, statement="交付争议")
    before_dec = db_session.scalar(select(func.count()).select_from(HumanDecision)) or 0
    before_audit = db_session.scalar(select(func.count()).select_from(AuditLog)) or 0
    confirmed = svc.confirm_issue(issue.issue_key, actor_id=actor_id)
    assert confirmed.status == "CONFIRMED"
    assert confirmed.layer == "CONFIRMED"
    assert confirmed.confirm_decision_id is not None
    after_dec = db_session.scalar(select(func.count()).select_from(HumanDecision)) or 0
    after_audit = db_session.scalar(select(func.count()).select_from(AuditLog)) or 0
    assert after_dec == before_dec + 1
    assert after_audit > before_audit


def test_d_lawyer_created_issue_is_confirmed(
    db_session: Session, owner_id, actor_id
) -> None:
    svc = DomainService(db_session)
    case = svc.create_case(title="Lawyer issue", owner_user_id=owner_id)
    issue = svc.create_lawyer_issue(
        case_id=case.id,
        statement="付款条件是否成就",
        actor_id=actor_id,
    )
    assert issue.source_type == "LAWYER_CREATED"
    assert issue.status == "CONFIRMED"
    assert issue.confirm_decision_id is not None


def test_e_amend_confirmed_issue_versions(
    db_session: Session, owner_id, actor_id
) -> None:
    svc = DomainService(db_session)
    case = svc.create_case(title="Amend issue", owner_user_id=owner_id)
    issue = svc.propose_issue(case_id=case.id, statement="旧表述")
    svc.confirm_issue(issue.issue_key, actor_id=actor_id)
    v2 = svc.amend_issue(
        issue.issue_key,
        new_statement="新表述",
        actor_id=actor_id,
        change_reason="律师调整",
    )
    v1 = svc.repo.get_issue_version(issue.issue_key, 1)
    assert v1 is not None
    assert v1.status == "SUPERSEDED"
    assert v1.is_current is False
    assert v1.statement == "旧表述"
    assert v2.version == 2
    assert v2.is_current is True
    assert v2.statement == "新表述"
    assert v2.source_type == "LAWYER_REFINED"
    assert svc.repo.get_current_issue(issue.issue_key).id == v2.id


def test_f_reject_candidate(db_session: Session, owner_id, actor_id) -> None:
    svc = DomainService(db_session)
    case = svc.create_case(title="Reject issue", owner_user_id=owner_id)
    issue = svc.propose_issue(case_id=case.id, statement="将被驳回")
    rejected = svc.reject_issue(issue.issue_key, actor_id=actor_id)
    assert rejected.status == "REJECTED"
    decision = db_session.get(HumanDecision, rejected.confirm_decision_id) if False else None
    _ = decision
    rows = db_session.scalars(
        select(HumanDecision).where(
            HumanDecision.decision_type == "REJECT_ISSUE",
            HumanDecision.target_id == issue.issue_key,
        )
    ).all()
    assert len(rows) == 1


def test_g_issue_fact_link_roles_and_exact_version(
    db_session: Session, owner_id, actor_id
) -> None:
    svc, case, _, _, _, item = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    fact = _seed_confirmed_fact(svc, case_id=case.id, actor_id=actor_id, item=item)
    issue = svc.propose_issue(case_id=case.id, statement="交付焦点")
    svc.confirm_issue(issue.issue_key, actor_id=actor_id)
    current = svc.repo.get_current_issue(issue.issue_key)
    assert current is not None
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
    links = db_session.scalars(select(IssueFactLink)).all()
    assert len(links) == 3
    assert all(link.fact_version == fact.version for link in links)


def test_h_cross_case_and_missing_version_rejected(
    db_session: Session, owner_id, actor_id
) -> None:
    svc = DomainService(db_session)
    case_a = svc.create_case(title="Case A", owner_user_id=owner_id)
    case_b = svc.create_case(title="Case B", owner_user_id=owner_id)
    _, _, _, _, _, item_b = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id, case=case_b
    )
    fact_b = _seed_confirmed_fact(svc, case_id=case_b.id, actor_id=actor_id, item=item_b)
    issue_a = svc.propose_issue(case_id=case_a.id, statement="A 焦点")
    svc.confirm_issue(issue_a.issue_key, actor_id=actor_id)
    current = svc.repo.get_current_issue(issue_a.issue_key)
    assert current is not None
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
    with pytest.raises(NotFoundError, match="not found"):
        svc.link_fact_to_issue(
            case_id=case_a.id,
            issue_key=current.issue_key,
            issue_version=current.version,
            fact_key=fact_b.fact_key,
            fact_version=999,
            role="SUPPORT",
            actor_id=actor_id,
        )


def test_i_duplicate_issue_fact_link_prevented(
    db_session: Session, owner_id, actor_id
) -> None:
    svc, case, _, _, _, item = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    fact = _seed_confirmed_fact(svc, case_id=case.id, actor_id=actor_id, item=item)
    issue = svc.propose_issue(case_id=case.id, statement="重复链接")
    svc.confirm_issue(issue.issue_key, actor_id=actor_id)
    current = svc.repo.get_current_issue(issue.issue_key)
    assert current is not None
    svc.link_fact_to_issue(
        case_id=case.id,
        issue_key=current.issue_key,
        issue_version=current.version,
        fact_key=fact.fact_key,
        fact_version=fact.version,
        role="SUPPORT",
        actor_id=actor_id,
    )
    with pytest.raises(IntegrityError):
        svc.link_fact_to_issue(
            case_id=case.id,
            issue_key=current.issue_key,
            issue_version=current.version,
            fact_key=fact.fact_key,
            fact_version=fact.version,
            role="SUPPORT",
            actor_id=actor_id,
        )


def test_j_issue_evidence_link_and_semantic_boundary(
    db_session: Session, owner_id, actor_id
) -> None:
    svc, case, _, _, _, item = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    issue = svc.propose_issue(case_id=case.id, statement="证据关联")
    svc.confirm_issue(issue.issue_key, actor_id=actor_id)
    current = svc.repo.get_current_issue(issue.issue_key)
    assert current is not None
    svc.link_evidence_to_issue(
        case_id=case.id,
        issue_key=current.issue_key,
        issue_version=current.version,
        evidence_item_id=item.id,
        evidence_item_version=item.version,
        role="SUPPORT",
        explanation="与交付时间点相关",
        actor_id=actor_id,
    )
    with pytest.raises(ValidationError, match="legal proof"):
        svc.link_evidence_to_issue(
            case_id=case.id,
            issue_key=current.issue_key,
            issue_version=current.version,
            evidence_item_id=item.id,
            evidence_item_version=item.version,
            role="ADVERSE",
            explanation="E07 proves defendant bears liability",
            actor_id=actor_id,
        )


def test_k_issue_evidence_link_does_not_replace_fact_evidence_link(
    db_session: Session, owner_id, actor_id
) -> None:
    svc, case, _, _, _, item = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    _seed_confirmed_fact(svc, case_id=case.id, actor_id=actor_id, item=item)
    issue = svc.propose_issue(case_id=case.id, statement="证明链边界")
    svc.confirm_issue(issue.issue_key, actor_id=actor_id)
    current = svc.repo.get_current_issue(issue.issue_key)
    assert current is not None
    svc.link_evidence_to_issue(
        case_id=case.id,
        issue_key=current.issue_key,
        issue_version=current.version,
        evidence_item_id=item.id,
        evidence_item_version=item.version,
        role="CONTEXT",
        actor_id=actor_id,
    )
    fel_count = db_session.scalar(select(func.count()).select_from(FactEvidenceLink)) or 0
    assert fel_count >= 1
    readiness = PleadingReadinessService(db_session).evaluate(case.id)
    codes = {i.code for i in readiness.blocking_issues}
    assert "ISSUE_EVIDENCE_LINK" not in codes


def test_l_amend_copies_links_to_new_version(
    db_session: Session, owner_id, actor_id
) -> None:
    svc, case, _, _, _, item = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    fact = _seed_confirmed_fact(svc, case_id=case.id, actor_id=actor_id, item=item)
    issue = svc.propose_issue(case_id=case.id, statement="复制链接")
    svc.confirm_issue(issue.issue_key, actor_id=actor_id)
    current = svc.repo.get_current_issue(issue.issue_key)
    assert current is not None
    svc.link_fact_to_issue(
        case_id=case.id,
        issue_key=current.issue_key,
        issue_version=current.version,
        fact_key=fact.fact_key,
        fact_version=fact.version,
        role="SUPPORT",
        actor_id=actor_id,
    )
    svc.link_evidence_to_issue(
        case_id=case.id,
        issue_key=current.issue_key,
        issue_version=current.version,
        evidence_item_id=item.id,
        evidence_item_version=item.version,
        role="CONTEXT",
        actor_id=actor_id,
    )
    v2 = svc.amend_issue(issue.issue_key, new_statement="新表述", actor_id=actor_id)
    v1_fact_links = svc.repo.list_issue_fact_links(issue.issue_key, 1)
    v2_fact_links = svc.repo.list_issue_fact_links(issue.issue_key, v2.version)
    v1_ev_links = svc.repo.list_issue_evidence_links(issue.issue_key, 1)
    v2_ev_links = svc.repo.list_issue_evidence_links(issue.issue_key, v2.version)
    assert len(v1_fact_links) == 1
    assert len(v2_fact_links) == 1
    assert v1_fact_links[0].fact_version == v2_fact_links[0].fact_version
    assert len(v1_ev_links) == 1
    assert len(v2_ev_links) == 1


def test_m_analyst_integration(
    db_session: Session, owner_id, actor_id
) -> None:
    _, case, _, _, _, item = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    engine = ScriptedCaseAnalystEngine(
        AnalystEngineResult(
            facts=[],
            issues=[
                IssueProposal(
                    proposal_id=uuid.uuid4(),
                    statement="材料是否足以证明交付？",
                    analyst_reason="争议点",
                )
            ],
            legal_theories=[
                LegalTheoryProposal(
                    proposal_id=uuid.uuid4(),
                    theory_summary="若交付成立则付款条件可能成就",
                    analyst_reason="理论候选",
                )
            ],
        )
    )
    svc = CaseAnalystService(db_session, engine=engine)
    result = svc.analyze(
        case_id=case.id, accepted_evidence_refs=[_ref(item)], actor_id=actor_id
    )
    issue = db_session.get(Issue, result.issue_ids[0])
    assert issue is not None
    assert issue.status == "CANDIDATE"
    assert issue.source_type == "AI_PROPOSED"
    assert issue.version == 1
    assert issue.is_current is True
    assert issue.layer == "CANDIDATE"
    theory = db_session.get(LegalTheory, result.legal_theory_ids[0])
    assert theory is not None
    assert theory.layer == "CANDIDATE"
