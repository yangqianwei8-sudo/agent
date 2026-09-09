"""Pleading Readiness Gate V1 — matrix + incident regression."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.agent.case_agent import CaseAgent
from backend.agent.dto import AgentErrorCode, AgentIntent
from backend.agent.intent_router import DeterministicIntentRouter
from backend.application.pleading_readiness import PleadingReadinessService
from backend.application.pleading_writer import PleadingWriterService
from backend.application.workspace import WorkspaceQueryService
from backend.domain.services import DomainService
from backend.models import DocumentDraft, DraftCitation, Fact, HumanDecision
from backend.schemas.pleading_readiness import PleadingNotReadyError, ReadinessStatus
from backend.tests.integration.test_pleading_writer import _seed_writer_world, _writer_args


def _add_confirmed_fact(
    svc: DomainService,
    *,
    case_id: uuid.UUID,
    statement: str,
    actor_id: uuid.UUID,
    number: str = "9",
) -> Fact:
    material = svc.register_material(
        case_id=case_id,
        filename=f"{uuid.uuid4().hex[:8]}.pdf",
        mime="application/pdf",
        byte_size=len(statement),
        content_hash=f"h-{uuid.uuid4().hex[:10]}",
        storage_key=f"k-{uuid.uuid4().hex[:10]}",
        created_by=actor_id,
    )
    ec = svc.create_extracted_content(
        material_id=material.id,
        extraction_method="pdfplumber",
        extraction_version="v1",
        full_text=statement,
        actor_id=actor_id,
    )
    span = svc.create_source_span(
        material_id=material.id,
        extracted_content_id=ec.id,
        character_start=0,
        character_end=len(statement),
        quote=statement,
        extraction_method="pdfplumber",
        extraction_version="v1",
    )
    evidence = svc.create_evidence_item(
        case_id=case_id,
        number=number,
        title=f"证据{number}",
        category="CONTRACT",
        summary=statement,
        source_span_ids=[span.id],
        actor_id=actor_id,
    )
    svc.accept_evidence(evidence.id, actor_id=actor_id)
    evidence = svc.repo.get_current_evidence(evidence.id)
    assert evidence is not None
    fact = svc.propose_fact(
        case_id=case_id,
        statement=statement,
        evidence_links=[
            {
                "evidence_item_id": evidence.id,
                "evidence_item_version": evidence.version,
            }
        ],
        actor_id=actor_id,
    )
    svc.confirm_fact(fact.fact_key, actor_id=actor_id)
    out = svc.repo.get_current_fact(fact.fact_key)
    assert out is not None
    return out


def _seed_incident_case(
    session: Session, *, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> tuple:
    """富茂 vs 中梁 — contract party != defendant, no bridge/performance/amount."""
    svc = DomainService(session)
    case = svc.create_case(title="富茂中梁事故回归", owner_user_id=owner_id)
    plaintiff = svc.create_party(
        case_id=case.id,
        role="PLAINTIFF",
        name="四川维海科技有限公司",
        party_type="ORG",
        actor_id=actor_id,
    )
    defendant = svc.create_party(
        case_id=case.id,
        role="DEFENDANT",
        name="中梁地产",
        party_type="ORG",
        actor_id=actor_id,
    )
    svc.confirm_party(plaintiff.party_key, actor_id=actor_id)
    svc.confirm_party(defendant.party_key, actor_id=actor_id)

    statements = [
        "合同签约甲方：四川富茂置业有限公司。合同成立。",
        "合同约定服务费按优化金额的8%计取，封顶30万元。",
        "合同约定工作范围包括设计优化与成果提交。",
    ]
    facts: list[Fact] = []
    for i, text in enumerate(statements):
        facts.append(
            _add_confirmed_fact(
                svc,
                case_id=case.id,
                statement=text,
                actor_id=actor_id,
                number=str(i + 1),
            )
        )

    claim = svc.create_claim_direction(
        case_id=case.id,
        payload={
            "overall_strategy": "确认合同关系并要求被告支付服务费",
            "claims": [
                {
                    "claim_type": "PAYMENT",
                    "description": "服务费",
                    "amount": 300000,
                    "currency": "CNY",
                    "calculation_basis": "封顶30万",
                    "supporting_fact_ids": [str(f.fact_key) for f in facts],
                }
            ],
        },
        actor_id=actor_id,
    )
    claim = svc.confirm_claim_direction(claim.claim_direction_key, actor_id=actor_id)
    return svc, case, [plaintiff.party_key, defendant.party_key], facts, claim


def _seed_ready_case(
    session: Session, *, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> tuple:
    return _seed_writer_world(session, owner_id=owner_id, actor_id=actor_id)


def _codes(result) -> set[str]:
    return {i.code for i in result.blocking_issues}


def test_a_missing_plaintiff(db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID) -> None:
    svc = DomainService(db_session)
    case = svc.create_case(title="缺原告", owner_user_id=owner_id)
    d = svc.create_party(
        case_id=case.id, role="DEFENDANT", name="被告A", party_type="ORG", actor_id=actor_id
    )
    svc.confirm_party(d.party_key, actor_id=actor_id)
    result = PleadingReadinessService(db_session).evaluate(case.id)
    assert result.status == ReadinessStatus.NOT_READY
    assert "PLAINTIFF_NOT_CONFIRMED" in _codes(result)


def test_b_missing_defendant(db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID) -> None:
    svc = DomainService(db_session)
    case = svc.create_case(title="缺被告", owner_user_id=owner_id)
    p = svc.create_party(
        case_id=case.id, role="PLAINTIFF", name="原告A", party_type="ORG", actor_id=actor_id
    )
    svc.confirm_party(p.party_key, actor_id=actor_id)
    result = PleadingReadinessService(db_session).evaluate(case.id)
    assert result.status == ReadinessStatus.NOT_READY
    assert "DEFENDANT_NOT_CONFIRMED" in _codes(result)


def test_c_contract_party_ne_defendant_no_bridge(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, *_ = _seed_incident_case(db_session, owner_id=owner_id, actor_id=actor_id)
    result = PleadingReadinessService(db_session).evaluate(case.id)
    assert result.status == ReadinessStatus.NOT_READY
    assert "DEFENDANT_LIABILITY_BASIS_MISSING" in _codes(result)


def test_d_liability_bridge_passes(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    svc, case, _, _, _ = _seed_incident_case(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    _add_confirmed_fact(
        svc,
        case_id=case.id,
        statement="中梁地产债务加入并确认承担付款责任。",
        actor_id=actor_id,
        number="20",
    )
    result = PleadingReadinessService(db_session).evaluate(case.id)
    assert "DEFENDANT_LIABILITY_BASIS_MISSING" not in _codes(result)


def test_e_contract_terms_only_no_performance(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, *_ = _seed_incident_case(db_session, owner_id=owner_id, actor_id=actor_id)
    result = PleadingReadinessService(db_session).evaluate(case.id)
    assert "PERFORMANCE_NOT_ESTABLISHED" in _codes(result)


def test_f_confirmed_delivery_passes_performance(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    svc, case, _, _, _ = _seed_incident_case(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    _add_confirmed_fact(
        svc,
        case_id=case.id,
        statement="原告已交付优化成果并经对方签收。",
        actor_id=actor_id,
        number="21",
    )
    result = PleadingReadinessService(db_session).evaluate(case.id)
    assert "PERFORMANCE_NOT_ESTABLISHED" not in _codes(result)


def test_g_cap_without_optimized_cost(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, *_ = _seed_incident_case(db_session, owner_id=owner_id, actor_id=actor_id)
    result = PleadingReadinessService(db_session).evaluate(case.id)
    assert "CLAIM_AMOUNT_NOT_PROVEN" in _codes(result)


def test_h_amount_chain_complete_passes(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, *_ = _seed_ready_case(db_session, owner_id=owner_id, actor_id=actor_id)
    result = PleadingReadinessService(db_session).evaluate(case.id)
    assert "CLAIM_AMOUNT_NOT_PROVEN" not in _codes(result)


def test_i_payment_condition_unknown(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, *_ = _seed_incident_case(db_session, owner_id=owner_id, actor_id=actor_id)
    result = PleadingReadinessService(db_session).evaluate(case.id)
    assert (
        "PAYMENT_CONDITION_NOT_ESTABLISHED" in _codes(result)
        or "PAYMENT_TERM_UNCLEAR" in _codes(result)
    )


def test_j_debt_due_unclear(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, *_ = _seed_incident_case(db_session, owner_id=owner_id, actor_id=actor_id)
    result = PleadingReadinessService(db_session).evaluate(case.id)
    assert "DEBT_DUE_STATUS_UNCLEAR" in _codes(result)


def test_k_conflict_affects_amount(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    svc, case, *_ = _seed_ready_case(db_session, owner_id=owner_id, actor_id=actor_id)
    _add_confirmed_fact(
        svc,
        case_id=case.id,
        statement="被告已付款500000元。",
        actor_id=actor_id,
        number="77",
    )
    result = PleadingReadinessService(db_session).evaluate(case.id)
    assert result.status == ReadinessStatus.NOT_READY
    assert "MATERIAL_FACT_CONFLICT" in _codes(result)


def test_l_stale_fact_cannot_satisfy(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    svc, case, _, _, _ = _seed_incident_case(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    f = _add_confirmed_fact(
        svc,
        case_id=case.id,
        statement="原告已交付优化成果并经对方签收。",
        actor_id=actor_id,
        number="30",
    )
    f.stale = True
    db_session.flush()
    result = PleadingReadinessService(db_session).evaluate(case.id)
    assert "PERFORMANCE_NOT_ESTABLISHED" in _codes(result)


def test_m_candidate_fact_cannot_satisfy(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    svc, case, _, _, _ = _seed_incident_case(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    material = svc.register_material(
        case_id=case.id,
        filename="cand.pdf",
        mime="application/pdf",
        byte_size=10,
        content_hash=f"h-{uuid.uuid4().hex[:8]}",
        storage_key=f"k-{uuid.uuid4().hex[:8]}",
        created_by=actor_id,
    )
    ec = svc.create_extracted_content(
        material_id=material.id,
        extraction_method="pdfplumber",
        extraction_version="v1",
        full_text="原告已交付",
        actor_id=actor_id,
    )
    span = svc.create_source_span(
        material_id=material.id,
        extracted_content_id=ec.id,
        character_start=0,
        character_end=5,
        quote="原告已交付",
        extraction_method="pdfplumber",
        extraction_version="v1",
    )
    evidence = svc.create_evidence_item(
        case_id=case.id,
        number="31",
        title="候选",
        category="CONTRACT",
        summary="x",
        source_span_ids=[span.id],
        actor_id=actor_id,
    )
    svc.accept_evidence(evidence.id, actor_id=actor_id)
    evidence = svc.repo.get_current_evidence(evidence.id)
    assert evidence is not None
    svc.propose_fact(
        case_id=case.id,
        statement="原告已交付优化成果并经对方签收。",
        evidence_links=[
            {
                "evidence_item_id": evidence.id,
                "evidence_item_version": evidence.version,
            }
        ],
        actor_id=actor_id,
    )
    result = PleadingReadinessService(db_session).evaluate(case.id)
    assert "PERFORMANCE_NOT_ESTABLISHED" in _codes(result)


def test_n_invalid_evidence_provenance_blocks_key_gate(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    from backend.models import ExtractedContent

    svc, case, _, _, _ = _seed_incident_case(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    f = _add_confirmed_fact(
        svc,
        case_id=case.id,
        statement="原告已交付优化成果并经对方签收。",
        actor_id=actor_id,
        number="32",
    )
    # Break EC
    from backend.models import EvidenceItemSpan, FactEvidenceLink, SourceSpan

    links = list(
        db_session.scalars(select(FactEvidenceLink).where(FactEvidenceLink.fact_id == f.id))
    )
    for link in links:
        spans = list(
            db_session.scalars(
                select(EvidenceItemSpan).where(
                    EvidenceItemSpan.evidence_item_id == link.evidence_item_id
                )
            )
        )
        for eis in spans:
            span = db_session.get(SourceSpan, eis.source_span_id)
            if span:
                ec = db_session.get(ExtractedContent, span.extracted_content_id)
                if ec:
                    ec.status = "FAILED"
    db_session.flush()
    result = PleadingReadinessService(db_session).evaluate(case.id)
    assert "PERFORMANCE_NOT_ESTABLISHED" in _codes(result) or (
        "KEY_FACT_PROVENANCE_INVALID" in _codes(result)
    )


def test_o_pending_unreadable_warning(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    svc, case, *_ = _seed_ready_case(db_session, owner_id=owner_id, actor_id=actor_id)
    svc.register_material(
        case_id=case.id,
        filename="pending_scan.pdf",
        mime="application/pdf",
        byte_size=10,
        content_hash=f"h-{uuid.uuid4().hex[:8]}",
        storage_key=f"k-{uuid.uuid4().hex[:8]}",
        created_by=actor_id,
    )
    # leave parse PENDING
    result = PleadingReadinessService(db_session).evaluate(case.id)
    assert any(w.code == "PENDING_MATERIAL_DISCLOSED" for w in result.warnings)


def test_p_jurisdiction_warning_when_missing(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, *_ = _seed_incident_case(db_session, owner_id=owner_id, actor_id=actor_id)
    result = PleadingReadinessService(db_session).evaluate(case.id)
    assert any(w.code == "JURISDICTION_NOT_FINALIZED" for w in result.warnings)


def test_q_not_ready_writer_zero_draft(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    svc, case, parties, facts, claim = _seed_incident_case(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    before_d = db_session.scalar(select(func.count()).select_from(DocumentDraft)) or 0
    before_c = db_session.scalar(select(func.count()).select_from(DraftCitation)) or 0
    before_hd = db_session.scalar(
        select(func.count()).select_from(HumanDecision).where(HumanDecision.case_id == case.id)
    ) or 0
    writer = PleadingWriterService(db_session)
    with pytest.raises(PleadingNotReadyError) as exc:
        writer.write(
            case_id=case.id,
            claim_direction_ref={
                "claim_direction_key": str(claim.claim_direction_key),
                "claim_direction_version": claim.version,
            },
            confirmed_fact_refs=[
                {"fact_key": str(f.fact_key), "fact_version": f.version} for f in facts
            ],
            accepted_evidence_refs=[],
            confirmed_party_keys=parties,
            actor_id=actor_id,
        )
    assert exc.value.readiness.status == ReadinessStatus.NOT_READY
    assert db_session.scalar(select(func.count()).select_from(DocumentDraft)) == before_d
    assert db_session.scalar(select(func.count()).select_from(DraftCitation)) == before_c
    assert (
        db_session.scalar(
            select(func.count()).select_from(HumanDecision).where(HumanDecision.case_id == case.id)
        )
        == before_hd
    )


def test_r_ready_writer_creates_draft(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, parties, facts, evidences, claim = _seed_ready_case(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    readiness = PleadingReadinessService(db_session).evaluate(case.id)
    assert readiness.status == ReadinessStatus.READY, [
        i.code for i in readiness.blocking_issues
    ]
    result = PleadingWriterService(db_session).write(
        actor_id=actor_id, **_writer_args(case, parties, facts, evidences, claim)
    )
    assert result.draft is not None
    assert result.draft.status == "DRAFT"


def test_incident_regression_agent_generate_blocked(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, *_ = _seed_incident_case(db_session, owner_id=owner_id, actor_id=actor_id)
    before_d = db_session.scalar(select(func.count()).select_from(DocumentDraft)) or 0
    agent = CaseAgent(
        db_session,
        actor_id=actor_id,
        intent_engine=DeterministicIntentRouter(),
    )
    resp = agent.handle_message(case_id=case.id, message="帮我生成起诉状")
    assert resp.intent == AgentIntent.GENERATE_COMPLAINT
    assert resp.error_code == AgentErrorCode.PLEADING_NOT_READY
    assert "还不建议生成起诉状" in resp.message or "关键问题" in resp.message
    codes = set()
    for ref in resp.references or []:
        if ref.get("type") == "pleading_readiness":
            codes.update(ref.get("blocking_codes") or [])
    readiness = PleadingReadinessService(db_session).evaluate(case.id)
    codes = codes or _codes(readiness)
    assert "DEFENDANT_LIABILITY_BASIS_MISSING" in codes
    assert "PERFORMANCE_NOT_ESTABLISHED" in codes
    assert "CLAIM_AMOUNT_NOT_PROVEN" in codes
    assert db_session.scalar(select(func.count()).select_from(DocumentDraft)) == before_d


def test_conversation_readiness_question(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, *_ = _seed_incident_case(db_session, owner_id=owner_id, actor_id=actor_id)
    agent = CaseAgent(
        db_session,
        actor_id=actor_id,
        intent_engine=DeterministicIntentRouter(),
    )
    resp = agent.handle_message(case_id=case.id, message="现在能不能生成起诉状？")
    assert resp.intent == AgentIntent.CASE_CONVERSATION
    assert "还不建议" in resp.message or "关键问题" in resp.message
    assert "中梁" in resp.message or "责任" in resp.message


def test_workspace_shows_readiness(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, *_ = _seed_incident_case(db_session, owner_id=owner_id, actor_id=actor_id)
    ws = WorkspaceQueryService(db_session).get_workspace(case.id)
    assert "pleading_readiness" in ws
    assert ws["pleading_readiness"]["status"] == "NOT_READY"
    assert ws["pleading_readiness"]["display_status"] == "尚未具备"
    assert ws["pleading_readiness"]["blocking_issues"]


def test_evaluate_is_mutation_free(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, *_ = _seed_incident_case(db_session, owner_id=owner_id, actor_id=actor_id)
    before_hd = db_session.scalar(
        select(func.count()).select_from(HumanDecision).where(HumanDecision.case_id == case.id)
    )
    before_facts = db_session.scalar(
        select(func.count()).select_from(Fact).where(Fact.case_id == case.id)
    )
    PleadingReadinessService(db_session).evaluate(case.id)
    assert (
        db_session.scalar(
            select(func.count()).select_from(HumanDecision).where(HumanDecision.case_id == case.id)
        )
        == before_hd
    )
    assert (
        db_session.scalar(select(func.count()).select_from(Fact).where(Fact.case_id == case.id))
        == before_facts
    )


def test_intent_readiness_question_not_generate() -> None:
    router = DeterministicIntentRouter()
    r = router.parse("现在能不能生成起诉状？")
    assert r.intent == AgentIntent.CASE_CONVERSATION
    r2 = router.parse("帮我生成起诉状")
    assert r2.intent == AgentIntent.GENERATE_COMPLAINT
