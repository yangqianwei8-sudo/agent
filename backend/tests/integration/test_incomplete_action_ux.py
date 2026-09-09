"""Incomplete Action Clarification UX — ask for slots, never UNKNOWN."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.agent.case_agent import CaseAgent
from backend.agent.dto import AgentIntent
from backend.agent.intent_router import DeterministicIntentRouter
from backend.domain.services import DomainService
from backend.llm.fake import FakeLLMClient
from backend.llm.intent_router import LLMIntentRouter
from backend.models import (
    CaseParty,
    EvidenceItem,
    Fact,
    HumanDecision,
)


def _make_case(session: Session, owner_id: uuid.UUID, title: str = "IncompleteUX"):
    return DomainService(session).create_case(
        title=title, owner_user_id=owner_id, actor_id=owner_id
    )


def _agent(session: Session, actor_id: uuid.UUID) -> CaseAgent:
    return CaseAgent(
        session, actor_id=actor_id, intent_engine=DeterministicIntentRouter()
    )


def _party_count(session: Session, case_id: uuid.UUID) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(CaseParty)
            .where(CaseParty.case_id == case_id, CaseParty.is_current.is_(True))
        )
        or 0
    )


def _decision_count(session: Session, case_id: uuid.UUID) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(HumanDecision)
            .where(HumanDecision.case_id == case_id)
        )
        or 0
    )


def _seed_evidence_facts(
    session: Session, *, case_id, actor_id: uuid.UUID, n_ev: int = 3, n_fact: int = 2
) -> None:
    svc = DomainService(session)
    text = "合同摘录用于 incomplete UX 测试。" * 2
    material = svc.register_material(
        case_id=case_id,
        filename="ux.txt",
        mime="text/plain",
        byte_size=len(text),
        content_hash=f"h-{uuid.uuid4().hex[:12]}",
        storage_key=f"k-{uuid.uuid4().hex[:12]}",
        created_by=actor_id,
    )
    ec = svc.create_extracted_content(
        material_id=material.id,
        extraction_method="plain",
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
        extraction_method="plain",
        extraction_version="v1",
        page=1,
    )
    items = []
    for i in range(1, n_ev + 1):
        items.append(
            svc.create_evidence_item(
                case_id=case_id,
                number=str(i),
                title=f"证据{i}",
                category="CONTRACT",
                summary=f"摘录{i}",
                source_span_ids=[span.id],
                actor_id=actor_id,
            )
        )
    # Accept first evidence so facts can be proposed; leave others PENDING
    accepted = svc.accept_evidence(items[0].id, actor_id=actor_id)
    for i in range(n_fact):
        svc.propose_fact(
            case_id=case_id,
            statement=f"候选事实陈述内容{i + 1}足够长用于测试。",
            actor_id=actor_id,
            evidence_links=[
                {
                    "evidence_item_id": accepted.id,
                    "evidence_item_version": accepted.version,
                }
            ],
        )


def test_a_accept_evidence_incomplete_not_unknown(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    case = _make_case(db_session, owner_id, "A")
    _seed_evidence_facts(db_session, case_id=case.id, actor_id=actor_id)
    before = _decision_count(db_session, case.id)
    r = _agent(db_session, actor_id).handle_message(case.id, "把证据接受一下")
    assert r.intent == AgentIntent.ACCEPT_EVIDENCE
    assert r.safety_result == "INCOMPLETE"
    assert r.routing_status == "INCOMPLETE"
    assert "证据" in r.message
    assert "哪" in r.message
    assert "UNKNOWN" not in (r.message or "")
    assert r.pending_action is not None
    assert _decision_count(db_session, case.id) == before


def test_b_short_answer_accepts_evidence(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    case = _make_case(db_session, owner_id, "B")
    _seed_evidence_facts(db_session, case_id=case.id, actor_id=actor_id)
    agent = _agent(db_session, actor_id)
    r1 = agent.handle_message(case.id, "把证据接受一下")
    r2 = agent.handle_message(case.id, "3", conversation_id=r1.conversation_id)
    assert r2.intent == AgentIntent.ACCEPT_EVIDENCE
    assert r2.error_code is None, r2.message
    row = db_session.scalars(
        select(EvidenceItem).where(
            EvidenceItem.case_id == case.id,
            EvidenceItem.is_current.is_(True),
            EvidenceItem.number == "3",
        )
    ).one()
    assert row.acceptance == "ACCEPTED"
    assert r2.pending_action is None


def test_c_confirm_fact_incomplete(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    case = _make_case(db_session, owner_id, "C")
    _seed_evidence_facts(db_session, case_id=case.id, actor_id=actor_id)
    r = _agent(db_session, actor_id).handle_message(case.id, "把事实确认一下")
    assert r.intent == AgentIntent.CONFIRM_FACT
    assert r.safety_result == "INCOMPLETE"
    assert "事实" in r.message


def test_d_short_answer_confirm_fact(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    case = _make_case(db_session, owner_id, "D")
    _seed_evidence_facts(db_session, case_id=case.id, actor_id=actor_id)
    agent = _agent(db_session, actor_id)
    r1 = agent.handle_message(case.id, "把事实确认一下")
    before = _decision_count(db_session, case.id)
    r2 = agent.handle_message(case.id, "2", conversation_id=r1.conversation_id)
    assert r2.intent == AgentIntent.CONFIRM_FACT
    assert r2.error_code is None, r2.message
    assert _decision_count(db_session, case.id) == before + 1


def test_e_create_defendant_incomplete(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    case = _make_case(db_session, owner_id, "E")
    r = _agent(db_session, actor_id).handle_message(case.id, "录入一个被告")
    assert r.intent == AgentIntent.CREATE_PARTY
    assert r.safety_result == "INCOMPLETE"
    assert "被告" in r.message and "名称" in r.message
    assert _party_count(db_session, case.id) == 0


def test_f_create_defendant_short_name(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    case = _make_case(db_session, owner_id, "F")
    agent = _agent(db_session, actor_id)
    r1 = agent.handle_message(case.id, "录入一个被告")
    r2 = agent.handle_message(
        case.id, "四川富茂置业有限公司", conversation_id=r1.conversation_id
    )
    assert r2.intent == AgentIntent.CREATE_PARTY
    assert _party_count(db_session, case.id) == 1
    p = db_session.scalars(
        select(CaseParty).where(
            CaseParty.case_id == case.id, CaseParty.is_current.is_(True)
        )
    ).one()
    assert p.role == "DEFENDANT"
    assert p.name == "四川富茂置业有限公司"


def test_g_amend_fact_asks_new_statement(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    case = _make_case(db_session, owner_id, "G")
    _seed_evidence_facts(db_session, case_id=case.id, actor_id=actor_id)
    r = _agent(db_session, actor_id).handle_message(case.id, "帮我改事实2")
    assert r.intent == AgentIntent.AMEND_FACT
    assert r.safety_result == "INCOMPLETE"
    assert "new_statement" in (r.missing_fields or []) or "修改" in r.message
    assert "事实2" in r.message or "内容" in r.message


def test_h_amend_fact_short_fill(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    case = _make_case(db_session, owner_id, "H")
    _seed_evidence_facts(db_session, case_id=case.id, actor_id=actor_id)
    # Existing Domain rule: only CONFIRMED facts can be amended
    domain = DomainService(db_session)
    facts = list(
        db_session.scalars(
            select(Fact)
            .where(
                Fact.case_id == case.id,
                Fact.is_current.is_(True),
                Fact.status == "CANDIDATE",
            )
            .order_by(Fact.created_at.asc())
        )
    )
    assert len(facts) >= 2
    domain.confirm_fact(facts[0].fact_key, actor_id=actor_id)
    domain.confirm_fact(facts[1].fact_key, actor_id=actor_id)
    agent = _agent(db_session, actor_id)
    r1 = agent.handle_message(case.id, "帮我改事实2")
    assert r1.intent == AgentIntent.AMEND_FACT
    assert r1.safety_result == "INCOMPLETE"
    r2 = agent.handle_message(
        case.id,
        "改成被告已付款10万元，尚欠20万元。",
        conversation_id=r1.conversation_id,
    )
    assert r2.intent == AgentIntent.AMEND_FACT
    assert r2.error_code is None, r2.message
    assert "10" in r2.message or "尚欠" in r2.message or "已" in r2.message


def test_i_pronoun_resolves_unique_focus(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    case = _make_case(db_session, owner_id, "I")
    _seed_evidence_facts(db_session, case_id=case.id, actor_id=actor_id)
    agent = _agent(db_session, actor_id)
    r1 = agent.handle_message(case.id, "证据3能证明什么？")
    assert r1.intent == AgentIntent.CASE_CONVERSATION
    r2 = agent.handle_message(case.id, "把它接受了", conversation_id=r1.conversation_id)
    assert r2.intent == AgentIntent.ACCEPT_EVIDENCE
    assert r2.error_code is None, r2.message
    row = db_session.scalars(
        select(EvidenceItem).where(
            EvidenceItem.case_id == case.id,
            EvidenceItem.is_current.is_(True),
            EvidenceItem.number == "3",
        )
    ).one()
    assert row.acceptance == "ACCEPTED"


def test_j_ambiguous_pronoun_no_mutation(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    case = _make_case(db_session, owner_id, "J")
    _seed_evidence_facts(db_session, case_id=case.id, actor_id=actor_id)
    agent = _agent(db_session, actor_id)
    cid = None
    r = agent.handle_message(case.id, "证据2能证明什么？")
    cid = r.conversation_id
    agent.handle_message(case.id, "证据3呢？", conversation_id=cid)
    before = _decision_count(db_session, case.id)
    r3 = agent.handle_message(case.id, "把它接受了", conversation_id=cid)
    assert r3.intent == AgentIntent.ACCEPT_EVIDENCE
    assert r3.safety_result in {"AMBIGUOUS", "INCOMPLETE"}
    assert _decision_count(db_session, case.id) == before


def test_k_cancel_pending(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    case = _make_case(db_session, owner_id, "K")
    _seed_evidence_facts(db_session, case_id=case.id, actor_id=actor_id)
    agent = _agent(db_session, actor_id)
    r1 = agent.handle_message(case.id, "把证据接受一下")
    assert r1.pending_action is not None
    r2 = agent.handle_message(case.id, "算了", conversation_id=r1.conversation_id)
    assert r2.pending_action is None
    assert _decision_count(db_session, case.id) == _decision_count(db_session, case.id)


def test_l_confirm_that_fact_not_unknown(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    case = _make_case(db_session, owner_id, "L")
    _seed_evidence_facts(db_session, case_id=case.id, actor_id=actor_id)
    r = _agent(db_session, actor_id).handle_message(case.id, "确认那个事实")
    assert r.intent == AgentIntent.CONFIRM_FACT
    assert r.intent != AgentIntent.UNKNOWN
    assert r.safety_result in {"INCOMPLETE", "AMBIGUOUS"}
    assert r.error_code is None or r.error_code.value != "UNKNOWN_INTENT"


def test_llm_router_accept_without_target_not_unknown() -> None:
    r = LLMIntentRouter(
        FakeLLMClient(
            responses=[
                {
                    "intent": "ACCEPT_EVIDENCE",
                    "target_text": "",
                    "arguments": {},
                    "confidence": 0.9,
                    "reason": "user wants accept",
                }
            ]
        )
    )
    out = r.parse("把证据接受一下")
    assert out.intent == AgentIntent.ACCEPT_EVIDENCE
    assert out.targets == []


def test_llm_router_unknown_falls_back_to_incomplete() -> None:
    r = LLMIntentRouter(
        FakeLLMClient(
            responses=[
                {
                    "intent": "UNKNOWN",
                    "target_text": "",
                    "arguments": {},
                    "confidence": 0.3,
                    "reason": "unsure",
                }
            ]
        )
    )
    out = r.parse("录入一个被告")
    assert out.intent == AgentIntent.CREATE_PARTY
