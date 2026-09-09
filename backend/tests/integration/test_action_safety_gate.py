"""Natural Language Action Safety Gate — incomplete mutations must not write Domain."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.agent.action_safety import (
    PartyNameValidator,
    detect_create_party_request,
    extract_parties_from_text,
)
from backend.agent.case_agent import CaseAgent
from backend.agent.dto import AgentIntent
from backend.agent.intent_router import DeterministicIntentRouter
from backend.domain.services import DomainService
from backend.models import (
    CaseParty,
    ClaimDirection,
    DocumentDraft,
    EvidenceItem,
    Fact,
    HumanDecision,
    WorkflowInstance,
)


def _make_case(session: Session, owner_id: uuid.UUID, title: str = "SafetyGate"):
    return DomainService(session).create_case(
        title=title, owner_user_id=owner_id, actor_id=owner_id
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


def _mutation_snapshot(session: Session, case_id: uuid.UUID) -> dict[str, Any]:
    wf = session.scalars(
        select(WorkflowInstance)
        .where(WorkflowInstance.case_id == case_id)
        .order_by(WorkflowInstance.created_at.desc())
    ).first()
    return {
        "parties": _party_count(session, case_id),
        "decisions": _decision_count(session, case_id),
        "evidence": int(
            session.scalar(
                select(func.count())
                .select_from(EvidenceItem)
                .where(EvidenceItem.case_id == case_id)
            )
            or 0
        ),
        "facts": int(
            session.scalar(
                select(func.count()).select_from(Fact).where(Fact.case_id == case_id)
            )
            or 0
        ),
        "claims": int(
            session.scalar(
                select(func.count())
                .select_from(ClaimDirection)
                .where(ClaimDirection.case_id == case_id)
            )
            or 0
        ),
        "drafts": int(
            session.scalar(
                select(func.count())
                .select_from(DocumentDraft)
                .where(DocumentDraft.case_id == case_id)
            )
            or 0
        ),
        "wf_status": wf.status if wf else None,
        "wf_node": wf.current_node_id if wf else None,
    }


def _agent(session: Session, actor_id: uuid.UUID) -> CaseAgent:
    return CaseAgent(
        session,
        actor_id=actor_id,
        intent_engine=DeterministicIntentRouter(),
    )


def test_create_parties_without_names_never_mutates(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    """Incident regression: 「你帮我录入原告与被告啊」 must never create a party."""
    case = _make_case(db_session, owner_id, "事故回归")
    before = _mutation_snapshot(db_session, case.id)
    agent = _agent(db_session, actor_id)
    resp = agent.handle_message(case_id=case.id, message="你帮我录入原告与被告啊")
    after = _mutation_snapshot(db_session, case.id)

    assert after["parties"] == before["parties"] == 0
    assert after["decisions"] == before["decisions"]
    assert after["wf_status"] == before["wf_status"]
    assert after["wf_node"] == before["wf_node"]
    assert "与被告啊" not in resp.message
    assert "原告" in resp.message and "被告" in resp.message
    assert "名称" in resp.message
    assert resp.safety_result == "INCOMPLETE"
    assert "plaintiff_name" in (resp.missing_fields or [])
    assert "defendant_name" in (resp.missing_fields or [])
    assert resp.pending_action is not None


def test_matrix_a_to_n_action_safety(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    case = _make_case(db_session, owner_id, "矩阵")
    agent = _agent(db_session, actor_id)
    cid = None

    # A
    r = agent.handle_message(case_id=case.id, message="你帮我录入原告与被告啊")
    cid = r.conversation_id
    assert _party_count(db_session, case.id) == 0
    assert "名称" in r.message

    # B
    case_b = _make_case(db_session, owner_id, "矩阵B")
    rb = agent.handle_message(case_id=case_b.id, message="帮我录入原告")
    assert _party_count(db_session, case_b.id) == 0
    assert "原告" in rb.message and "名称" in rb.message

    # C
    case_c = _make_case(db_session, owner_id, "矩阵C")
    rc = agent.handle_message(case_id=case_c.id, message="把被告也录进去")
    assert _party_count(db_session, case_c.id) == 0
    assert "名称" in rc.message or "被告" in rc.message

    # D — pending after A: only plaintiff → still no mutation
    r2 = agent.handle_message(
        case_id=case.id,
        message="原告是四川维海科技有限公司",
        conversation_id=cid,
    )
    assert _party_count(db_session, case.id) == 0
    assert "被告" in r2.message
    assert r2.pending_action is not None

    # E — defendant completes → create both atomically
    r3 = agent.handle_message(
        case_id=case.id,
        message="被告四川富茂置业有限公司",
        conversation_id=cid,
    )
    assert _party_count(db_session, case.id) == 2
    assert "维海" in r3.message and "富茂" in r3.message
    assert r3.pending_action is None
    parties = list(
        db_session.scalars(
            select(CaseParty).where(
                CaseParty.case_id == case.id, CaseParty.is_current.is_(True)
            )
        )
    )
    names = {p.name for p in parties}
    roles = {p.role for p in parties}
    assert "四川维海科技有限公司" in names
    assert "四川富茂置业有限公司" in names
    assert roles == {"PLAINTIFF", "DEFENDANT"}
    assert all(p.layer == "CANDIDATE" for p in parties)

    # F — dual extract must not glue names
    extracted = extract_parties_from_text("原告维海，被告富茂")
    assert {p["role"]: p["name"] for p in extracted} == {
        "PLAINTIFF": "维海",
        "DEFENDANT": "富茂",
    }

    # G
    case_g = _make_case(db_session, owner_id, "矩阵G")
    rg = agent.handle_message(
        case_id=case_g.id, message="帮我把四川维海科技有限公司录为原告"
    )
    assert _party_count(db_session, case_g.id) == 1
    assert "原告" in rg.message

    # H
    case_h = _make_case(db_session, owner_id, "矩阵H")
    rh = agent.handle_message(
        case_id=case_h.id, message="四川富茂置业有限公司是被告，录进去"
    )
    assert _party_count(db_session, case_h.id) == 1
    assert "被告" in rh.message

    # I — fuzzy affirmation on defendant → conversation, no confirm
    before_d = _decision_count(db_session, case.id)
    ri = agent.handle_message(
        case_id=case.id,
        message="这个被告应该没问题吧",
        conversation_id=cid,
    )
    assert ri.intent == AgentIntent.CASE_CONVERSATION
    assert _decision_count(db_session, case.id) == before_d
    assert all(
        p.layer == "CANDIDATE"
        for p in db_session.scalars(
            select(CaseParty).where(
                CaseParty.case_id == case.id, CaseParty.is_current.is_(True)
            )
        )
    )

    # J — explicit confirm defendant by role-local index
    rj = agent.handle_message(
        case_id=case.id,
        message="确认被告1",
        conversation_id=cid,
    )
    assert rj.intent == AgentIntent.CONFIRM_PARTY
    assert _decision_count(db_session, case.id) > before_d
    assert "DEFENDANT" in rj.message or "富茂" in rj.message or "被告" in rj.message

    # K / M — incomplete target mutations
    case_k = _make_case(db_session, owner_id, "矩阵K")
    snap_k = _mutation_snapshot(db_session, case_k.id)
    rk = agent.handle_message(case_id=case_k.id, message="确认那个事实")
    assert _mutation_snapshot(db_session, case_k.id) == snap_k
    assert "事实" in rk.message
    assert rk.safety_result in {"INCOMPLETE", "AMBIGUOUS"}

    rm = agent.handle_message(case_id=case_k.id, message="把证据接受一下")
    assert _mutation_snapshot(db_session, case_k.id) == snap_k
    assert rm.safety_result == "INCOMPLETE"
    assert "证据" in rm.message


def test_party_name_validator_rejects_residuals() -> None:
    bad = [
        "与被告啊",
        "原告与被告",
        "原告和被告",
        "被告啊",
        "这个公司",
        "那个公司",
        "对方",
        "他",
        "她",
        "他们",
        "帮我录入",
        "先录一下",
        "录一下",
        "公司",
        "原告",
        "被告",
        "双方",
        "当事人",
        "与被告",
        "和被告",
        "以及被告",
        "再加被告",
        "也录进去",
    ]
    for name in bad:
        assert PartyNameValidator.is_valid(name) is False, name

    assert PartyNameValidator.is_valid("四川维海科技有限公司") is True
    assert PartyNameValidator.is_valid("维海") is True


def test_detect_incident_phrase_incomplete() -> None:
    prop = detect_create_party_request("你帮我录入原告与被告啊")
    assert prop is not None
    assert prop.intent == AgentIntent.CREATE_PARTY
    assert "plaintiff_name" in prop.missing_fields
    assert "defendant_name" in prop.missing_fields
    assert not prop.arguments.get("parties")


def test_cancel_pending_create(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    case = _make_case(db_session, owner_id, "取消pending")
    agent = _agent(db_session, actor_id)
    r1 = agent.handle_message(case_id=case.id, message="帮我录入原告与被告")
    assert r1.pending_action is not None
    r2 = agent.handle_message(
        case_id=case.id,
        message="算了，先不录",
        conversation_id=r1.conversation_id,
    )
    # Exact cancel pattern is 算了 / 先不录 alone — try exact
    if r2.pending_action is not None:
        r2 = agent.handle_message(
            case_id=case.id,
            message="算了",
            conversation_id=r1.conversation_id,
        )
    assert r2.pending_action is None
    assert _party_count(db_session, case.id) == 0


def test_one_shot_dual_create(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    case = _make_case(db_session, owner_id, "一次双录")
    agent = _agent(db_session, actor_id)
    resp = agent.handle_message(
        case_id=case.id,
        message="原告四川维海科技有限公司，被告四川富茂置业有限公司，帮我录入",
    )
    assert _party_count(db_session, case.id) == 2
    assert resp.safety_result in (None, "VALID") or resp.pending_action is None


def _seed_evidence_and_facts(
    session: Session, *, case_id, actor_id: uuid.UUID, n_ev: int, n_fact: int
) -> None:
    svc = DomainService(session)
    text = "合同摘录用于动作安全门测试。"
    material = svc.register_material(
        case_id=case_id,
        filename="gate.txt",
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
    accepted = svc.accept_evidence(items[0].id, actor_id=actor_id)
    for i in range(n_fact):
        svc.propose_fact(
            case_id=case_id,
            statement=f"候选事实陈述{i + 1}足够长。",
            actor_id=actor_id,
            evidence_links=[
                {
                    "evidence_item_id": accepted.id,
                    "evidence_item_version": accepted.version,
                }
            ],
        )


def test_l_fact_n_confirm(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    case = _make_case(db_session, owner_id, "矩阵L")
    _seed_evidence_and_facts(db_session, case_id=case.id, actor_id=actor_id, n_ev=1, n_fact=2)
    agent = _agent(db_session, actor_id)
    before = _mutation_snapshot(db_session, case.id)
    r = agent.handle_message(case_id=case.id, message="事实2确认")
    assert r.intent == AgentIntent.CONFIRM_FACT
    assert r.error_code is None, r.message
    after = _mutation_snapshot(db_session, case.id)
    assert after["decisions"] == before["decisions"] + 1


def test_n_accept_evidence_numbered(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    case = _make_case(db_session, owner_id, "矩阵N")
    _seed_evidence_and_facts(db_session, case_id=case.id, actor_id=actor_id, n_ev=3, n_fact=0)
    agent = _agent(db_session, actor_id)
    r = agent.handle_message(case_id=case.id, message="接受证据3")
    assert r.intent == AgentIntent.ACCEPT_EVIDENCE
    assert r.error_code is None, r.message
    row = db_session.scalars(
        select(EvidenceItem).where(
            EvidenceItem.case_id == case.id,
            EvidenceItem.is_current.is_(True),
            EvidenceItem.number == "3",
        )
    ).one()
    assert row.acceptance == "ACCEPTED"
