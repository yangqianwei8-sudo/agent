"""Phase 6 — Case Analyst / Fact Candidate anti-examples and N4–N6 gates."""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.application.case_analyst import CaseAnalystService
from backend.domain.errors import ValidationError
from backend.domain.services import DomainService
from backend.models import (
    AuditLog,
    EvidenceItem,
    Fact,
    FactEvidenceLink,
    HumanDecision,
    Issue,
    LegalTheory,
    SkillExecution,
    TimelineEvent,
)
from backend.schemas.case_analyst import (
    AnalystEngineResult,
    ConflictItem,
    EvidenceRef,
    FactProposal,
    IssueProposal,
    LegalTheoryProposal,
    MissingEvidenceItem,
)
from backend.skills.case_analyst import ScriptedCaseAnalystEngine
from backend.tests.workflow.helpers import ensure_pleading_prep_template, node_by_code
from backend.workflow.runtime import WorkflowRuntime


def _seed_accepted_evidence(
    session: Session,
    *,
    owner_id: uuid.UUID,
    actor_id: uuid.UUID,
    text: str = "2025年3月1日，原告与被告签订《设计优化咨询合同》。",
    title: str = "设计合同签署页",
    category: str = "CONTRACT",
    case=None,
) -> tuple:
    svc = DomainService(session)
    if case is None:
        case = svc.create_case(title="P6 Case", owner_user_id=owner_id)
    material = svc.register_material(
        case_id=case.id,
        filename=f"{uuid.uuid4().hex[:8]}.pdf",
        mime="application/pdf",
        byte_size=len(text),
        content_hash=f"h-{uuid.uuid4().hex[:12]}",
        storage_key=f"k-{uuid.uuid4().hex[:12]}",
        created_by=actor_id,
    )
    ec = svc.create_extracted_content(
        material_id=material.id,
        extraction_method="pdfplumber",
        extraction_version="v1",
        full_text=text,
        actor_id=actor_id,
    )
    span = svc.create_source_span(
        material_id=material.id,
        extracted_content_id=ec.id,
        character_start=0,
        character_end=len(text),
        quote=text,
        extraction_method="pdfplumber",
        extraction_version="v1",
        page=1,
    )
    item = svc.create_evidence_item(
        case_id=case.id,
        number="1",
        title=title,
        category=category,
        summary=text[:200],
        source_span_ids=[span.id],
        actor_id=actor_id,
    )
    svc.accept_evidence(item.id, actor_id=actor_id)
    item = svc.repo.get_current_evidence(item.id)
    assert item is not None
    return svc, case, material, ec, span, item


def _ref(item: EvidenceItem) -> dict:
    return {
        "evidence_item_id": str(item.id),
        "evidence_item_version": item.version,
    }


def _fact_proposal(
    item: EvidenceItem,
    *,
    statement: str = "双方于2025年3月1日签订合同。",
    fact_type: str = "CONTRACT_SIGNING",
    extra_items: list[EvidenceItem] | None = None,
) -> FactProposal:
    refs = [EvidenceRef(evidence_item_id=item.id, evidence_item_version=item.version)]
    for other in extra_items or []:
        refs.append(
            EvidenceRef(
                evidence_item_id=other.id,
                evidence_item_version=other.version,
            )
        )
    return FactProposal(
        proposal_id=uuid.uuid4(),
        statement=statement,
        fact_type=fact_type,
        occurred_at=date(2025, 3, 1),
        precision="DAY",
        supporting_evidence_refs=refs,
        confidence=0.9,
        analyst_reason="材料记载签署日期与双方名称。",
    )


# ----- A–H provenance input -----


def test_a_missing_version_rejected(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, _, _, _, item = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    svc = CaseAnalystService(db_session)
    with pytest.raises(ValidationError, match="evidence_item_version required"):
        svc.analyze(
            case_id=case.id,
            accepted_evidence_refs=[{"evidence_item_id": str(item.id)}],
            actor_id=actor_id,
        )


def test_b_nonexistent_evidence_rejected(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, _, _, _, _ = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    svc = CaseAnalystService(db_session)
    with pytest.raises(ValidationError, match="not found"):
        svc.analyze(
            case_id=case.id,
            accepted_evidence_refs=[
                {"evidence_item_id": str(uuid.uuid4()), "evidence_item_version": 1}
            ],
            actor_id=actor_id,
        )


def test_c_cross_case_evidence_rejected(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case_a, _, _, _, _ = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id, text="案件A合同正文足够长。"
    )
    _, _, _, _, _, item_b = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id, text="案件B付款通知正文足够长。"
    )
    svc = CaseAnalystService(db_session)
    with pytest.raises(ValidationError, match="cross-case"):
        svc.analyze(
            case_id=case_a.id,
            accepted_evidence_refs=[_ref(item_b)],
            actor_id=actor_id,
        )


def test_d_pending_evidence_rejected(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    domain, case, _, _, span, _ = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    pending = domain.create_evidence_item(
        case_id=case.id,
        number="2",
        title="未确认",
        category="OTHER",
        summary="pending",
        source_span_ids=[span.id],
        actor_id=actor_id,
    )
    assert pending.acceptance == "PENDING"
    svc = CaseAnalystService(db_session)
    with pytest.raises(ValidationError, match="PENDING"):
        svc.analyze(
            case_id=case.id,
            accepted_evidence_refs=[_ref(pending)],
            actor_id=actor_id,
        )


def test_e_excluded_evidence_rejected(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    domain, case, _, _, _, item = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    # Re-create path: exclude after accept
    domain.exclude_evidence(item.id, actor_id=actor_id)
    # Need a different ACCEPTED item to have a case, but analyze with EXCLUDED ref
    _, _, _, _, _, other = _seed_accepted_evidence(
        db_session,
        owner_id=owner_id,
        actor_id=actor_id,
        text="另一份已接受证据正文足够长。",
        case=case,
    )
    excluded = domain.repo.get_current_evidence(item.id)
    assert excluded is not None and excluded.acceptance == "EXCLUDED"
    svc = CaseAnalystService(db_session)
    with pytest.raises(ValidationError, match="EXCLUDED"):
        svc.analyze(
            case_id=case.id,
            accepted_evidence_refs=[_ref(excluded)],
            actor_id=actor_id,
        )
    _ = other


def test_f_missing_version_number_rejected(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, _, _, _, item = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    svc = CaseAnalystService(db_session)
    with pytest.raises(ValidationError, match="version not found"):
        svc.analyze(
            case_id=case.id,
            accepted_evidence_refs=[
                {
                    "evidence_item_id": str(item.id),
                    "evidence_item_version": 99,
                }
            ],
            actor_id=actor_id,
        )


def test_g_broken_provenance_rejected(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    domain, case, _, ec, _, item = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    # Break provenance: mark EC FAILED after accept (bypass domain)
    ec.status = "FAILED"
    ec.full_text = None
    db_session.flush()
    svc = CaseAnalystService(db_session)
    with pytest.raises(ValidationError, match="provenance|ExtractedContent"):
        svc.analyze(
            case_id=case.id,
            accepted_evidence_refs=[_ref(item)],
            actor_id=actor_id,
        )
    _ = domain


def test_h_no_implicit_latest_version(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    domain, case, _, _, span, item = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    # Create v2 via amend
    amended = domain.amend_evidence_item(
        item.id, actor_id=actor_id, title="修订标题", source_span_ids=[span.id]
    )
    domain.accept_evidence(amended.id, actor_id=actor_id)
    assert amended.version == 2
    svc = CaseAnalystService(db_session)
    with pytest.raises(ValidationError, match="evidence_item_version required"):
        svc.analyze(
            case_id=case.id,
            accepted_evidence_refs=[{"evidence_item_id": str(item.id)}],
            actor_id=actor_id,
        )


# ----- I–N fact proposals -----


def test_i_fact_without_evidence_rejected(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, _, _, _, item = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    bad = FactProposal.model_construct(
        proposal_id=uuid.uuid4(),
        statement="被告拖欠服务费100万元",
        fact_type="PAYMENT",
        supporting_evidence_refs=[],
        confidence=0.5,
        analyst_reason="无证据",
        uncertainties=[],
    )
    engine = ScriptedCaseAnalystEngine(AnalystEngineResult(facts=[bad]))
    svc = CaseAnalystService(db_session, engine=engine)
    result = svc.analyze(
        case_id=case.id, accepted_evidence_refs=[_ref(item)], actor_id=actor_id
    )
    assert result.created_facts == []
    assert result.rejected_proposals


def test_j_fact_unknown_evidence_rejected(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, _, _, _, item = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    fake = EvidenceRef(evidence_item_id=uuid.uuid4(), evidence_item_version=1)
    prop = FactProposal(
        proposal_id=uuid.uuid4(),
        statement="双方于2025年3月1日签订合同。",
        fact_type="CONTRACT_SIGNING",
        supporting_evidence_refs=[fake],
        confidence=0.5,
        analyst_reason="x",
    )
    svc = CaseAnalystService(
        db_session, engine=ScriptedCaseAnalystEngine(AnalystEngineResult(facts=[prop]))
    )
    result = svc.analyze(
        case_id=case.id, accepted_evidence_refs=[_ref(item)], actor_id=actor_id
    )
    assert result.created_facts == []
    assert any(
        "outside" in r["error"] or "nonexistent" in r["error"]
        for r in result.rejected_proposals
    )


def test_k_fact_outside_scope_rejected(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, _, _, _, item_in = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id, text="范围内证据正文足够长。"
    )
    _, _, _, _, _, item_out = _seed_accepted_evidence(
        db_session,
        owner_id=owner_id,
        actor_id=actor_id,
        text="范围外证据正文足够长。",
        case=case,
    )
    prop = _fact_proposal(item_out)
    svc = CaseAnalystService(
        db_session, engine=ScriptedCaseAnalystEngine(AnalystEngineResult(facts=[prop]))
    )
    result = svc.analyze(
        case_id=case.id, accepted_evidence_refs=[_ref(item_in)], actor_id=actor_id
    )
    assert result.created_facts == []
    assert any("outside" in r["error"] for r in result.rejected_proposals)


def test_l_multi_evidence_one_fact(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, _, _, _, a = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id, text="合同载明交付条款正文。"
    )
    _, _, _, _, _, b = _seed_accepted_evidence(
        db_session,
        owner_id=owner_id,
        actor_id=actor_id,
        text="邮件确认成果已发送正文。",
        case=case,
        title="交付邮件",
    )
    prop = _fact_proposal(
        a,
        statement="原告于某日向被告交付成果。",
        fact_type="DELIVERY",
        extra_items=[b],
    )
    svc = CaseAnalystService(
        db_session, engine=ScriptedCaseAnalystEngine(AnalystEngineResult(facts=[prop]))
    )
    result = svc.analyze(
        case_id=case.id,
        accepted_evidence_refs=[_ref(a), _ref(b)],
        actor_id=actor_id,
    )
    assert len(result.created_facts) == 1
    links = db_session.scalars(
        select(FactEvidenceLink).where(
            FactEvidenceLink.fact_id == result.created_facts[0].id
        )
    ).all()
    assert {(x.evidence_item_id, x.evidence_item_version) for x in links} == {
        (a.id, a.version),
        (b.id, b.version),
    }


def test_m_one_evidence_multiple_facts(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, _, _, _, item = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    props = [
        _fact_proposal(
            item,
            statement="双方于2025年3月1日签订合同。",
            fact_type="CONTRACT_SIGNING",
        ),
        _fact_proposal(
            item,
            statement="合同约定服务费为人民币十万元。",
            fact_type="CONTRACT_TERM",
        ),
    ]
    svc = CaseAnalystService(
        db_session, engine=ScriptedCaseAnalystEngine(AnalystEngineResult(facts=props))
    )
    result = svc.analyze(
        case_id=case.id, accepted_evidence_refs=[_ref(item)], actor_id=actor_id
    )
    assert len(result.created_facts) == 2


def test_n_ai_creates_candidate_only(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, _, _, _, item = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    svc = CaseAnalystService(
        db_session,
        engine=ScriptedCaseAnalystEngine(
            AnalystEngineResult(facts=[_fact_proposal(item)])
        ),
    )
    result = svc.analyze(
        case_id=case.id, accepted_evidence_refs=[_ref(item)], actor_id=actor_id
    )
    assert len(result.created_facts) == 1
    assert result.created_facts[0].status == "CANDIDATE"
    assert result.timeline_ids


# ----- O–R human gate -----


def test_o_confirm_fact_decision_audit(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    domain, case, _, _, _, item = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    svc = CaseAnalystService(
        db_session,
        engine=ScriptedCaseAnalystEngine(
            AnalystEngineResult(facts=[_fact_proposal(item)])
        ),
    )
    fact = svc.analyze(
        case_id=case.id, accepted_evidence_refs=[_ref(item)], actor_id=actor_id
    ).created_facts[0]
    confirmed = domain.confirm_fact(fact.fact_key, actor_id=actor_id)
    assert confirmed.status == "CONFIRMED"
    assert confirmed.confirm_decision_id is not None
    decisions = db_session.scalars(
        select(HumanDecision).where(HumanDecision.case_id == case.id)
    ).all()
    assert any(d.decision_type == "CONFIRM_FACT" for d in decisions)
    assert db_session.scalars(
        select(AuditLog).where(AuditLog.action == "confirm_fact")
    ).first()


def test_p_reject_fact_keeps_history(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    domain, case, _, _, _, item = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    svc = CaseAnalystService(
        db_session,
        engine=ScriptedCaseAnalystEngine(
            AnalystEngineResult(facts=[_fact_proposal(item)])
        ),
    )
    fact = svc.analyze(
        case_id=case.id, accepted_evidence_refs=[_ref(item)], actor_id=actor_id
    ).created_facts[0]
    rejected = domain.reject_fact(fact.fact_key, actor_id=actor_id)
    assert rejected.status == "REJECTED"
    still = db_session.scalars(
        select(Fact).where(Fact.fact_key == fact.fact_key, Fact.is_current.is_(True))
    ).one()
    assert still.status == "REJECTED"


def test_q_amend_confirmed_requires_human_decision(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    domain, case, _, _, _, item = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    svc = CaseAnalystService(
        db_session,
        engine=ScriptedCaseAnalystEngine(
            AnalystEngineResult(facts=[_fact_proposal(item)])
        ),
    )
    fact = svc.analyze(
        case_id=case.id, accepted_evidence_refs=[_ref(item)], actor_id=actor_id
    ).created_facts[0]
    domain.confirm_fact(fact.fact_key, actor_id=actor_id)
    amended = domain.amend_fact(
        fact.fact_key, new_statement="双方于2025年3月1日签订合同（修订）。", actor_id=actor_id
    )
    assert amended.version == 2
    assert amended.status == "CONFIRMED"
    assert amended.confirm_decision_id is not None
    old = db_session.scalars(
        select(Fact).where(Fact.fact_key == fact.fact_key, Fact.version == 1)
    ).one()
    assert old.status == "SUPERSEDED"
    assert any(
        d.decision_type == "AMEND_FACT"
        for d in db_session.scalars(
            select(HumanDecision).where(HumanDecision.case_id == case.id)
        )
    )


def test_r_ai_cannot_amend_confirmed(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    domain, case, _, _, _, item = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    svc = CaseAnalystService(
        db_session,
        engine=ScriptedCaseAnalystEngine(
            AnalystEngineResult(
                facts=[
                    _fact_proposal(
                        item, statement="双方于2025年3月1日签订合同。"
                    )
                ]
            )
        ),
    )
    first = svc.analyze(
        case_id=case.id, accepted_evidence_refs=[_ref(item)], actor_id=actor_id
    ).created_facts[0]
    domain.confirm_fact(first.fact_key, actor_id=actor_id)

    svc2 = CaseAnalystService(
        db_session,
        engine=ScriptedCaseAnalystEngine(
            AnalystEngineResult(
                facts=[
                    _fact_proposal(
                        item,
                        statement="双方于2025年3月2日签订合同。",
                    )
                ]
            )
        ),
    )
    second = svc2.analyze(
        case_id=case.id, accepted_evidence_refs=[_ref(item)], actor_id=actor_id
    )
    # Confirmed fact unchanged
    confirmed = domain.repo.get_current_fact(first.fact_key)
    assert confirmed is not None
    assert confirmed.status == "CONFIRMED"
    assert "3月1日" in confirmed.statement
    # New candidate created for lawyer review
    assert second.created_facts
    assert second.created_facts[0].status == "CANDIDATE"
    assert second.confirmed_amendment_proposals


# ----- S–U duplicates -----


def test_s_duplicate_fact_skipped(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, _, _, _, item = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    prop = _fact_proposal(item)
    svc = CaseAnalystService(
        db_session, engine=ScriptedCaseAnalystEngine(AnalystEngineResult(facts=[prop]))
    )
    first = svc.analyze(
        case_id=case.id, accepted_evidence_refs=[_ref(item)], actor_id=actor_id
    )
    svc2 = CaseAnalystService(
        db_session,
        engine=ScriptedCaseAnalystEngine(
            AnalystEngineResult(
                facts=[
                    _fact_proposal(
                        item,
                        statement=prop.statement,
                        fact_type=prop.fact_type,
                    )
                ]
            )
        ),
    )
    second = svc2.analyze(
        case_id=case.id, accepted_evidence_refs=[_ref(item)], actor_id=actor_id
    )
    assert second.created_facts == []
    assert second.skipped
    assert second.fact_keys == [first.created_facts[0].fact_key]


def test_u_uncertain_same_evidence_not_merged(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    """Different statements + same evidence → separate Candidates (no silent merge)."""
    _, case, _, _, _, item = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    props = [
        _fact_proposal(item, statement="甲方于2025年5月1日支付三十万元。"),
        _fact_proposal(item, statement="甲方于2025年5月3日支付三十万元。"),
    ]
    svc = CaseAnalystService(
        db_session, engine=ScriptedCaseAnalystEngine(AnalystEngineResult(facts=props))
    )
    result = svc.analyze(
        case_id=case.id, accepted_evidence_refs=[_ref(item)], actor_id=actor_id
    )
    assert len(result.created_facts) == 2
    assert {f.fact_key for f in result.created_facts} == set(result.fact_keys)


# ----- V–Y legal boundary -----


def test_v_legal_conclusion_not_fact(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, _, _, _, item = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    prop = _fact_proposal(item, statement="被告构成根本违约。")
    svc = CaseAnalystService(
        db_session, engine=ScriptedCaseAnalystEngine(AnalystEngineResult(facts=[prop]))
    )
    result = svc.analyze(
        case_id=case.id, accepted_evidence_refs=[_ref(item)], actor_id=actor_id
    )
    assert result.created_facts == []
    assert any("legal conclusion" in r["error"] for r in result.rejected_proposals)


def test_w_factual_statement_allowed(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, _, _, _, item = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    prop = _fact_proposal(item, statement="双方于2025-03-01签订合同")
    svc = CaseAnalystService(
        db_session, engine=ScriptedCaseAnalystEngine(AnalystEngineResult(facts=[prop]))
    )
    result = svc.analyze(
        case_id=case.id, accepted_evidence_refs=[_ref(item)], actor_id=actor_id
    )
    assert len(result.created_facts) == 1


def test_x_y_issue_and_theory_do_not_propose_fact(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
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
                    statement="现有材料是否足以证明成果已完成交付？",
                    analyst_reason="争议点候选",
                )
            ],
            legal_theories=[
                LegalTheoryProposal(
                    proposal_id=uuid.uuid4(),
                    theory_summary="若交付事实能够确认，可能涉及合同付款条件是否成就。",
                    analyst_reason="分析候选",
                )
            ],
        )
    )
    before = db_session.scalar(select(func.count()).select_from(Fact)) or 0
    svc = CaseAnalystService(db_session, engine=engine)
    result = svc.analyze(
        case_id=case.id, accepted_evidence_refs=[_ref(item)], actor_id=actor_id
    )
    after = db_session.scalar(select(func.count()).select_from(Fact)) or 0
    assert result.created_facts == []
    assert after == before
    assert result.issue_ids
    assert result.legal_theory_ids
    assert db_session.get(Issue, result.issue_ids[0]).layer == "CANDIDATE"
    assert db_session.get(LegalTheory, result.legal_theory_ids[0]).layer == "CANDIDATE"


# ----- Z conflict -----


def test_z_evidence_conflict_not_silently_confirmed(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, _, _, _, a = _seed_accepted_evidence(
        db_session,
        owner_id=owner_id,
        actor_id=actor_id,
        text="付款日期记载为2025年5月1日。",
        title="付款凭证A",
    )
    _, _, _, _, _, b = _seed_accepted_evidence(
        db_session,
        owner_id=owner_id,
        actor_id=actor_id,
        text="付款日期记载为2025年5月3日。",
        title="付款凭证B",
        case=case,
    )
    engine = ScriptedCaseAnalystEngine(
        AnalystEngineResult(
            facts=[],
            conflicts=[
                ConflictItem(
                    type="EVIDENCE_CONFLICT",
                    description="两份材料对付款日期记载不一致",
                    evidence_refs=[
                        EvidenceRef(
                            evidence_item_id=a.id, evidence_item_version=a.version
                        ),
                        EvidenceRef(
                            evidence_item_id=b.id, evidence_item_version=b.version
                        ),
                    ],
                )
            ],
            missing_evidence=[
                MissingEvidenceItem(
                    description="缺少双方确认的统一付款日期材料",
                    reason="现有证据记载冲突",
                    related_evidence_refs=[
                        EvidenceRef(
                            evidence_item_id=a.id, evidence_item_version=a.version
                        )
                    ],
                )
            ],
        )
    )
    svc = CaseAnalystService(db_session, engine=engine)
    result = svc.analyze(
        case_id=case.id,
        accepted_evidence_refs=[_ref(a), _ref(b)],
        actor_id=actor_id,
    )
    assert result.created_facts == []
    assert result.conflicts
    assert result.missing_evidence
    assert db_session.scalar(
        select(func.count())
        .select_from(Fact)
        .where(Fact.case_id == case.id, Fact.status == "CONFIRMED")
    ) in (0, None)


# ----- stale -----


def test_stale_on_evidence_excluded_not_auto_rejected(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    domain, case, _, _, _, item = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    svc = CaseAnalystService(
        db_session,
        engine=ScriptedCaseAnalystEngine(
            AnalystEngineResult(facts=[_fact_proposal(item)])
        ),
    )
    fact = svc.analyze(
        case_id=case.id, accepted_evidence_refs=[_ref(item)], actor_id=actor_id
    ).created_facts[0]
    domain.confirm_fact(fact.fact_key, actor_id=actor_id)
    domain.exclude_evidence(item.id, actor_id=actor_id)
    refreshed = domain.repo.get_current_fact(fact.fact_key)
    assert refreshed is not None
    assert refreshed.stale is True
    assert refreshed.status == "CONFIRMED"


# ----- Workflow N4–N6 -----


def _advance_to_n4(runtime: WorkflowRuntime, case_id: uuid.UUID):
    ensure_pleading_prep_template(runtime.session)
    inst = runtime.create_instance(case_id=case_id)
    r = runtime.start_instance(inst.id)
    n4 = node_by_code(runtime.session, inst.template_id, "N4_ANALYZE")
    safety = 0
    while True:
        safety += 1
        if safety > 30:
            raise AssertionError("did not reach N4_ANALYZE")
        if r.node_run is not None and r.node_run.node_id == n4.id:
            return inst, r.node_run
        if r.instance.status == "WAITING_USER":
            # Human gates (e.g. N3): resume then complete on next loops.
            r = runtime.resume_instance(r.instance.id, command_id=uuid.uuid4())
            continue
        if r.node_run is None:
            raise AssertionError(f"stuck without node_run: status={r.instance.status}")
        r = runtime.complete_node(r.node_run.id)


def test_workflow_n4_n5_n6_stops_before_n7(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    domain, case, _, _, _, item = _seed_accepted_evidence(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    # Parties for N5
    party = domain.create_party(
        case_id=case.id,
        role="PLAINTIFF",
        name="原告公司",
        party_type="ORG",
        actor_id=actor_id,
    )
    defendant = domain.create_party(
        case_id=case.id,
        role="DEFENDANT",
        name="被告公司",
        party_type="ORG",
        actor_id=actor_id,
    )

    runtime = WorkflowRuntime(db_session)
    inst, n4_run = _advance_to_n4(runtime, case.id)

    analyst = CaseAnalystService(
        db_session,
        engine=ScriptedCaseAnalystEngine(
            AnalystEngineResult(facts=[_fact_proposal(item)])
        ),
    )
    result = analyst.run_n4_analyze(
        instance_id=inst.id,
        node_run_id=n4_run.id,
        accepted_evidence_refs=[_ref(item)],
        actor_id=actor_id,
    )
    assert result.created_facts
    assert result.created_facts[0].status == "CANDIDATE"

    skill = db_session.scalars(
        select(SkillExecution).where(SkillExecution.node_run_id == n4_run.id)
    ).one()
    assert skill.skill_code == "CaseAnalystSkill"
    assert skill.status == "SUCCEEDED"

    inst = runtime.get_instance(inst.id)
    assert inst.status == "WAITING_USER"
    assert inst.waiting_reason == "PARTY"

    with pytest.raises(ValidationError, match="party gate"):
        analyst.assert_n5_ready_to_complete(inst.id)

    domain.confirm_party(party.party_key, actor_id=actor_id)
    domain.confirm_party(defendant.party_key, actor_id=actor_id)
    analyst.assert_n5_ready_to_complete(inst.id)

    resumed = runtime.resume_instance(inst.id, command_id=uuid.uuid4())
    n5 = node_by_code(db_session, inst.template_id, "N5_CONFIRM_PARTIES")
    assert resumed.node_run is not None
    assert resumed.node_run.node_id == n5.id

    # Complete N5 → advance to N6 WAITING_USER
    after_n5 = analyst.complete_n5_confirm_parties(
        instance_id=inst.id, node_run_id=resumed.node_run.id, auto_advance=True
    )
    assert after_n5.instance.status == "WAITING_USER"
    assert after_n5.instance.waiting_reason == "FACT"

    with pytest.raises(ValidationError, match="fact gate|CANDIDATE"):
        analyst.assert_n6_ready_to_complete(inst.id)

    for key in result.fact_keys:
        domain.confirm_fact(key, actor_id=actor_id)
    analyst.assert_n6_ready_to_complete(inst.id)

    resumed6 = runtime.resume_instance(inst.id, command_id=uuid.uuid4())
    n6 = node_by_code(db_session, inst.template_id, "N6_CONFIRM_FACTS")
    assert resumed6.node_run is not None
    assert resumed6.node_run.node_id == n6.id

    done = analyst.complete_n6_confirm_facts(
        instance_id=inst.id,
        node_run_id=resumed6.node_run.id,
        auto_advance=False,
    )
    assert done.node_run is not None
    assert done.node_run.status == "SUCCEEDED"
    n7 = node_by_code(db_session, inst.template_id, "N7_CONFIRM_CLAIMS")
    assert runtime.list_node_runs(inst.id, node_id=n7.id) == []

    # Timeline derived from Fact proposal with occurred_at
    assert db_session.scalars(
        select(TimelineEvent).where(TimelineEvent.case_id == case.id)
    ).first()
