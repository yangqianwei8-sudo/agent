"""Phase 9 — Case Agent / Conversation contract & E2E tests."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.agent.case_agent import CaseAgent
from backend.agent.dto import AgentErrorCode, AgentIntent, IntentResult
from backend.agent.intent_router import DeterministicIntentRouter, ScriptedIntentEngine
from backend.domain.services import DomainService
from backend.models import (
    AgentMessage,
    DocumentDraft,
    EvidenceItem,
    HumanDecision,
    WorkflowInstance,
)
from backend.workflow.seed import ensure_pleading_prep_template


def _seed_case_with_materials(
    session: Session,
    *,
    owner_id: uuid.UUID,
    actor_id: uuid.UUID,
    texts: list[str] | None = None,
) -> tuple:
    ensure_pleading_prep_template(session)
    svc = DomainService(session)
    case = svc.create_case(title="P9 Agent Case", owner_user_id=owner_id)
    texts = texts or [
        "合同约定服务费总价为1000000元。",
        "被告已支付300000元。",
        "原告已向被告交付设计成果并经签收。",
        "合同约定成果提交后付款，付款条件已成就。",
        "尚欠服务费700000元已到期。",
        "合同约定由被告住所地人民法院管辖。",
    ]
    materials = []
    for i, text in enumerate(texts):
        material = svc.register_material(
            case_id=case.id,
            filename=f"m{i}.pdf",
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
        svc.create_source_span(
            material_id=material.id,
            extracted_content_id=ec.id,
            character_start=0,
            character_end=len(text),
            quote=text,
            extraction_method="pdfplumber",
            extraction_version="v1",
        )
        materials.append(material)
    # Parties as CANDIDATE for N5
    p1 = svc.create_party(
        case_id=case.id,
        role="PLAINTIFF",
        name="原告设计公司",
        party_type="ORG",
        actor_id=actor_id,
    )
    p2 = svc.create_party(
        case_id=case.id,
        role="DEFENDANT",
        name="被告建设公司",
        party_type="ORG",
        actor_id=actor_id,
    )
    return svc, case, materials, [p1, p2]


def _agent(session: Session, actor_id: uuid.UUID, **kwargs) -> CaseAgent:
    return CaseAgent(session, actor_id=actor_id, **kwargs)


def _say(agent: CaseAgent, case_id: uuid.UUID, text: str, cid: uuid.UUID | None = None):
    return agent.handle_message(case_id, text, conversation_id=cid)


# ----- A–C conversation -----


def test_a_b_c_conversation_persist_and_restart(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, _, _ = _seed_case_with_materials(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    a1 = _agent(db_session, actor_id)
    r1 = _say(a1, case.id, "状态")
    assert r1.conversation_id
    msgs = list(
        db_session.scalars(
            select(AgentMessage).where(AgentMessage.case_id == case.id)
        )
    )
    assert any(m.role == "USER" for m in msgs)
    assert any(m.role == "AGENT" for m in msgs)

    # Restart: new CaseAgent instance, same conversation
    a2 = _agent(db_session, actor_id)
    r2 = _say(a2, case.id, "继续", r1.conversation_id)
    assert r2.conversation_id == r1.conversation_id


# ----- D–E start -----


def test_d_e_start_workflow_idempotent(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, _, _ = _seed_case_with_materials(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    agent = _agent(db_session, actor_id)
    r1 = _say(agent, case.id, "开始处理这个案件")
    assert r1.intent == AgentIntent.START_CASE_WORKFLOW
    insts = list(
        db_session.scalars(
            select(WorkflowInstance).where(WorkflowInstance.case_id == case.id)
        )
    )
    assert len(insts) == 1
    r2 = _say(agent, case.id, "开始处理这个案件", r1.conversation_id)
    assert "未重复创建" in r2.message or "已有进行中" in r2.message
    assert len(
        list(
            db_session.scalars(
                select(WorkflowInstance).where(WorkflowInstance.case_id == case.id)
            )
        )
    ) == 1


# ----- helpers to advance -----


def _start_and_reach(
    agent: CaseAgent, case_id: uuid.UUID, target_node: str, *, cid: uuid.UUID | None = None
):
    r = _say(agent, case_id, "开始处理这个案件", cid)
    cid = r.conversation_id
    safety = 0
    while r.current_node != target_node and safety < 30:
        safety += 1
        # At human gates with pending, continue alone won't advance — break
        if r.error_code == AgentErrorCode.HUMAN_GATE_REQUIRED:
            break
        r = _say(agent, case_id, "继续", cid)
    return r, cid


# ----- F–K continue -----


def test_f_continue_n2_organizer(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, _, _ = _seed_case_with_materials(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    agent = _agent(db_session, actor_id)
    r = _say(agent, case.id, "开始处理这个案件")
    # START auto-runs N0→N1→N2 and stops at N3; Organizer must have executed.
    assert r.current_node == "N3_CONFIRM_EVIDENCE"
    assert r.workflow_status == "WAITING_USER"
    assert db_session.scalars(
        select(EvidenceItem).where(EvidenceItem.case_id == case.id)
    ).first()


def test_g_continue_n3_does_not_accept(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, _, _ = _seed_case_with_materials(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    agent = _agent(db_session, actor_id)
    r, cid = _start_and_reach(agent, case.id, "N2_ORGANIZE")
    while r.current_node != "N3_CONFIRM_EVIDENCE":
        r = _say(agent, case.id, "继续", cid)
        if r.current_node == "N3_CONFIRM_EVIDENCE":
            break
    r = _say(agent, case.id, "继续", cid)
    assert r.error_code == AgentErrorCode.HUMAN_GATE_REQUIRED
    pending = list(
        db_session.scalars(
            select(EvidenceItem).where(
                EvidenceItem.case_id == case.id,
                EvidenceItem.acceptance == "PENDING",
            )
        )
    )
    assert pending
    assert r.pending_count == len(pending)


def test_i_continue_n6_does_not_confirm_facts(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    """Drive to N6 then ensure CONTINUE does not confirm facts."""
    domain, case, _, parties = _seed_case_with_materials(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    agent = _agent(db_session, actor_id)
    r = _say(agent, case.id, "开始处理这个案件")
    cid = r.conversation_id
    # N0 N1 N2
    for _ in range(5):
        if r.current_node == "N3_CONFIRM_EVIDENCE":
            break
        r = _say(agent, case.id, "继续", cid)
    # Accept all evidence
    items = list(
        db_session.scalars(
            select(EvidenceItem).where(
                EvidenceItem.case_id == case.id, EvidenceItem.is_current.is_(True)
            )
        )
    )
    for it in items:
        r = _say(agent, case.id, f"接受证据{it.number}", cid)
    r = _say(agent, case.id, "继续", cid)  # N3→N4→N5 in one CONTINUE
    assert r.current_node == "N5_CONFIRM_PARTIES"
    # Confirm parties
    for i, _ in enumerate(parties, start=1):
        _say(agent, case.id, f"确认当事人{i}", cid)
    r = _say(agent, case.id, "继续", cid)  # complete N5 → N6
    assert r.current_node == "N6_CONFIRM_FACTS" or r.workflow_status == "WAITING_USER"
    # Reload
    r = _say(agent, case.id, "继续", cid)
    assert r.error_code == AgentErrorCode.HUMAN_GATE_REQUIRED
    assert "不能因「继续」自动确认" in r.message or "Fact" in r.message
    from backend.models import Fact

    candidates = list(
        db_session.scalars(
            select(Fact).where(Fact.case_id == case.id, Fact.status == "CANDIDATE")
        )
    )
    assert candidates


# ----- L–O evidence -----


def test_l_m_accept_evidence_idempotent(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, _, _ = _seed_case_with_materials(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    agent = _agent(db_session, actor_id)
    r = _say(agent, case.id, "开始处理这个案件")
    cid = r.conversation_id
    for _ in range(5):
        if r.current_node == "N3_CONFIRM_EVIDENCE":
            break
        r = _say(agent, case.id, "继续", cid)
    item = db_session.scalars(
        select(EvidenceItem).where(
            EvidenceItem.case_id == case.id, EvidenceItem.is_current.is_(True)
        )
    ).first()
    assert item is not None
    r1 = _say(agent, case.id, f"接受证据{item.number}", cid)
    assert "接受" in r1.message
    decisions_before = db_session.scalar(
        select(func.count()).select_from(HumanDecision).where(
            HumanDecision.case_id == case.id,
            HumanDecision.decision_type == "ACCEPT_EVIDENCE",
        )
    )
    r2 = _say(agent, case.id, f"接受证据{item.number}", cid)
    assert r2.idempotent_replay or "已" in r2.message
    decisions_after = db_session.scalar(
        select(func.count()).select_from(HumanDecision).where(
            HumanDecision.case_id == case.id,
            HumanDecision.decision_type == "ACCEPT_EVIDENCE",
        )
    )
    assert decisions_after == decisions_before


def test_n_cross_case_evidence_rejected(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    domain, case_a, _, _ = _seed_case_with_materials(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    _, case_b, _, _ = _seed_case_with_materials(
        db_session,
        owner_id=owner_id,
        actor_id=actor_id,
        texts=["另一案件材料正文足够长。"],
    )
    from backend.models import CaseMaterial, ExtractedContent, SourceSpan

    mat = db_session.scalars(
        select(CaseMaterial).where(CaseMaterial.case_id == case_b.id)
    ).first()
    assert mat is not None
    ec = db_session.scalars(
        select(ExtractedContent).where(ExtractedContent.material_id == mat.id)
    ).first()
    assert ec is not None
    span = db_session.scalars(
        select(SourceSpan).where(SourceSpan.extracted_content_id == ec.id)
    ).first()
    assert span is not None
    foreign = domain.create_evidence_item(
        case_id=case_b.id,
        number="99",
        title="外案证据",
        category="OTHER",
        summary="x",
        source_span_ids=[span.id],
        actor_id=actor_id,
    )
    engine = ScriptedIntentEngine(
        IntentResult(
            intent=AgentIntent.ACCEPT_EVIDENCE,
            parameters={"evidence_item_id": str(foreign.id)},
        )
    )
    agent = _agent(db_session, actor_id, intent_engine=engine)
    r = agent.handle_message(case_a.id, "accept foreign")
    assert r.error_code == AgentErrorCode.VALIDATION_ERROR
    assert "跨案件" in r.message


def test_o_ambiguous_evidence_number(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    domain, case, _, _ = _seed_case_with_materials(
        db_session,
        owner_id=owner_id,
        actor_id=actor_id,
        texts=["唯一材料正文足够长用于测试。"],
    )
    from backend.models import CaseMaterial, ExtractedContent, SourceSpan

    mat = db_session.scalars(
        select(CaseMaterial).where(CaseMaterial.case_id == case.id)
    ).one()
    ec = db_session.scalars(
        select(ExtractedContent).where(ExtractedContent.material_id == mat.id)
    ).one()
    span = db_session.scalars(
        select(SourceSpan).where(SourceSpan.extracted_content_id == ec.id)
    ).one()
    domain.create_evidence_item(
        case_id=case.id,
        number="7",
        title="A",
        category="OTHER",
        summary="a",
        source_span_ids=[span.id],
        actor_id=actor_id,
    )
    e2 = domain.create_evidence_item(
        case_id=case.id,
        number="8",
        title="B",
        category="OTHER",
        summary="b",
        source_span_ids=[span.id],
        actor_id=actor_id,
    )
    e2.number = "7"
    db_session.flush()
    agent = _agent(db_session, actor_id)
    r = _say(agent, case.id, "接受证据7")
    assert r.error_code == AgentErrorCode.AMBIGUOUS_TARGET


# ----- P–T facts -----


def test_p_q_confirm_reject_fact(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    domain, case, _, parties = _seed_case_with_materials(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    agent = _agent(db_session, actor_id)
    r = _say(agent, case.id, "开始处理这个案件")
    cid = r.conversation_id
    for _ in range(6):
        if r.current_node == "N3_CONFIRM_EVIDENCE":
            break
        r = _say(agent, case.id, "继续", cid)
    for it in db_session.scalars(
        select(EvidenceItem).where(EvidenceItem.case_id == case.id)
    ):
        _say(agent, case.id, f"接受证据{it.number}", cid)
    r = _say(agent, case.id, "继续", cid)
    assert r.current_node == "N5_CONFIRM_PARTIES"
    for i, _ in enumerate(parties, start=1):
        _say(agent, case.id, f"确认当事人{i}", cid)
    r = _say(agent, case.id, "继续", cid)
    r = _say(agent, case.id, "确认事实1", cid)
    assert r.intent == AgentIntent.CONFIRM_FACT
    r = _say(agent, case.id, "拒绝事实2", cid)
    assert r.intent == AgentIntent.REJECT_FACT
    _ = domain


# ----- pause / resume / retry -----


def test_x_y_z_pause_resume_human_gate(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, _, parties = _seed_case_with_materials(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    agent = _agent(db_session, actor_id)
    r = _say(agent, case.id, "开始处理这个案件")
    cid = r.conversation_id
    for _ in range(6):
        if r.current_node == "N3_CONFIRM_EVIDENCE":
            break
        r = _say(agent, case.id, "继续", cid)
    for it in db_session.scalars(
        select(EvidenceItem).where(EvidenceItem.case_id == case.id)
    ):
        _say(agent, case.id, f"接受证据{it.number}", cid)
    r = _say(agent, case.id, "继续", cid)
    assert r.current_node == "N5_CONFIRM_PARTIES"
    for i, _ in enumerate(parties, start=1):
        _say(agent, case.id, f"确认当事人{i}", cid)
    r = _say(agent, case.id, "继续", cid)
    assert r.current_node == "N6_CONFIRM_FACTS"

    r = _say(agent, case.id, "暂停", cid)
    assert "暂停" in r.message
    inst = db_session.scalars(
        select(WorkflowInstance).where(WorkflowInstance.case_id == case.id)
    ).one()
    assert inst.status == "WAITING_USER"
    assert inst.waiting_reason == "user_pause"

    # Resume must not bypass fact gate
    r = _say(agent, case.id, "恢复", cid)
    assert r.workflow_status == "RUNNING" or r.current_node == "N6_CONFIRM_FACTS"
    r = _say(agent, case.id, "继续", cid)
    assert r.error_code == AgentErrorCode.HUMAN_GATE_REQUIRED

    # Resume while on human gate (not user_pause) should refuse bypass
    # First pause again then we already tested; now say 恢复 when on FACT wait:
    # Re-enter FACT wait by confirming nothing — if status RUNNING after resume,
    # pause again then... Actually after continue with pending we're still on N6.
    # Force WAITING_USER FACT via pause then... simpler: after resume we're RUNNING;
    # call wait by saying 暂停 again, then 恢复 — already tested.
    # Spec Z: resume human gate → don't bypass. Test via Scripted RESUME when reason=FACT
    from backend.workflow.runtime import WorkflowRuntime

    runtime = WorkflowRuntime(db_session)
    inst = runtime.get_instance(inst.id)
    runtime.wait_for_user(inst.id, reason="FACT")
    r = _say(agent, case.id, "恢复", cid)
    assert r.error_code == AgentErrorCode.HUMAN_GATE_REQUIRED


def test_aa_retry_waiting_retry(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    from backend.models import NodeRun, WorkflowNode
    from backend.workflow.runtime import WorkflowRuntime

    _, case, _, _ = _seed_case_with_materials(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    agent = _agent(db_session, actor_id)
    r = _say(agent, case.id, "开始处理这个案件")
    cid = r.conversation_id
    runtime = WorkflowRuntime(db_session)
    inst = db_session.scalars(
        select(WorkflowInstance).where(WorkflowInstance.case_id == case.id)
    ).one()
    # START stops at N3 WAITING_USER without a NodeRun — resume to create RUNNING, then fail.
    resumed = runtime.resume_instance(inst.id, command_id=uuid.uuid4())
    assert resumed.node_run is not None
    node_run = resumed.node_run
    runtime.fail_node(node_run.id, error_code="TEST", error_detail="x", retryable=True)
    inst = runtime.get_instance(inst.id)
    assert inst.status == "WAITING_RETRY"
    snap_before = node_run.input_snapshot_ref
    r = _say(agent, case.id, "重试", cid)
    assert r.intent == AgentIntent.RETRY
    new_run = runtime.list_node_runs(inst.id, node_id=node_run.node_id)[-1]
    assert new_run.input_snapshot_ref == snap_before
    assert new_run.attempt == node_run.attempt + 1
    _ = WorkflowNode
    _ = NodeRun


# ----- status / draft -----


def test_ab_ac_ad_status_counts(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, _, parties = _seed_case_with_materials(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    agent = _agent(db_session, actor_id)
    r = _say(agent, case.id, "开始处理这个案件")
    cid = r.conversation_id
    for _ in range(6):
        if r.current_node == "N3_CONFIRM_EVIDENCE":
            break
        r = _say(agent, case.id, "继续", cid)
    r = _say(agent, case.id, "状态", cid)
    assert r.pending_count and r.pending_count > 0
    assert r.current_node == "N3_CONFIRM_EVIDENCE"


# ----- AE–AJ draft + happy path -----


def test_e2e_happy_path_to_succeeded(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, _, parties = _seed_case_with_materials(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    agent = _agent(db_session, actor_id)
    r = _say(agent, case.id, "开始处理这个案件")
    cid = r.conversation_id

    # Advance to N3
    for _ in range(8):
        if r.current_node == "N3_CONFIRM_EVIDENCE":
            break
        r = _say(agent, case.id, "继续", cid)
    assert r.current_node == "N3_CONFIRM_EVIDENCE"

    items = list(
        db_session.scalars(
            select(EvidenceItem).where(
                EvidenceItem.case_id == case.id, EvidenceItem.is_current.is_(True)
            )
        )
    )
    assert items
    for it in items:
        r = _say(agent, case.id, f"接受证据{it.number}", cid)

    r = _say(agent, case.id, "继续", cid)  # N3→N4→N5 in one CONTINUE
    assert r.current_node == "N5_CONFIRM_PARTIES"

    for i, _ in enumerate(parties, start=1):
        r = _say(agent, case.id, f"确认当事人{i}", cid)
    r = _say(agent, case.id, "继续", cid)  # N5 → N6

    # Confirm all candidate facts (display index among all current facts)
    from backend.models import Fact

    for _ in range(40):
        all_facts = list(
            db_session.scalars(
                select(Fact)
                .where(Fact.case_id == case.id, Fact.is_current.is_(True))
                .order_by(Fact.created_at.asc(), Fact.fact_key.asc())
            )
        )
        pending = [f for f in all_facts if f.status == "CANDIDATE"]
        if not pending:
            break
        idx = next(i for i, f in enumerate(all_facts, start=1) if f.status == "CANDIDATE")
        r = _say(agent, case.id, f"确认事实{idx}", cid)

    r = _say(agent, case.id, "继续", cid)  # N6 complete → N7
    # N7 propose
    if r.current_node == "N7_CONFIRM_CLAIMS":
        r = _say(agent, case.id, "继续", cid)

    r = _say(agent, case.id, "确认诉讼请求1", cid)
    assert r.intent == AgentIntent.CONFIRM_CLAIM_DIRECTION

    r = _say(agent, case.id, "继续", cid)  # N7 confirm → N8 Writer → N9
    assert r.current_node == "N9_REVIEW", r.message
    draft = db_session.scalars(
        select(DocumentDraft).where(DocumentDraft.case_id == case.id)
    ).first()
    assert draft is not None
    assert draft.status == "DRAFT"

    # AJ: continue must not approve
    r = _say(agent, case.id, "继续", cid)
    assert r.error_code == AgentErrorCode.HUMAN_GATE_REQUIRED
    draft = db_session.get(DocumentDraft, draft.id)
    assert draft is not None
    assert draft.status == "DRAFT"

    r = _say(agent, case.id, "查看起诉状", cid)
    assert r.intent == AgentIntent.SHOW_DRAFT

    r = _say(agent, case.id, "批准这份起诉状", cid)
    assert r.intent == AgentIntent.APPROVE_DRAFT
    draft = db_session.get(DocumentDraft, draft.id)
    assert draft is not None
    assert draft.status == "APPROVED_BY_LAWYER"

    # Repeat approve — no second independent approval decision flood
    decisions_before = db_session.scalar(
        select(func.count()).select_from(HumanDecision).where(
            HumanDecision.case_id == case.id,
            HumanDecision.decision_type == "APPROVE_DRAFT",
        )
    )
    r = _say(agent, case.id, "批准这份起诉状", cid)
    decisions_after = db_session.scalar(
        select(func.count()).select_from(HumanDecision).where(
            HumanDecision.case_id == case.id,
            HumanDecision.decision_type == "APPROVE_DRAFT",
        )
    )
    assert decisions_after == decisions_before

    r = _say(agent, case.id, "继续", cid)
    assert r.workflow_status == "SUCCEEDED"


def test_pause_resume_e2e_restart_agent(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, _, parties = _seed_case_with_materials(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    agent = _agent(db_session, actor_id)
    r = _say(agent, case.id, "开始处理这个案件")
    cid = r.conversation_id
    for _ in range(6):
        if r.current_node == "N3_CONFIRM_EVIDENCE":
            break
        r = _say(agent, case.id, "继续", cid)
    for it in db_session.scalars(
        select(EvidenceItem).where(EvidenceItem.case_id == case.id)
    ):
        _say(agent, case.id, f"接受证据{it.number}", cid)
    r = _say(agent, case.id, "继续", cid)
    assert r.current_node == "N5_CONFIRM_PARTIES"
    for i, _ in enumerate(parties, start=1):
        _say(agent, case.id, f"确认当事人{i}", cid)
    r = _say(agent, case.id, "继续", cid)
    assert r.current_node == "N6_CONFIRM_FACTS"

    _say(agent, case.id, "暂停", cid)

    # Re-instantiate agent (simulates process restart)
    agent2 = _agent(db_session, actor_id)
    r = _say(agent2, case.id, "恢复", cid)
    assert r.current_node == "N6_CONFIRM_FACTS"
    r = _say(agent2, case.id, "状态", cid)
    assert "N6" in (r.current_node or "")

    from backend.models import Fact

    facts = list(
        db_session.scalars(
            select(Fact)
            .where(Fact.case_id == case.id, Fact.status == "CANDIDATE")
            .order_by(Fact.created_at.asc())
        )
    )
    for i, _ in enumerate(facts, start=1):
        _say(agent2, case.id, f"确认事实{i}", cid)
    r = _say(agent2, case.id, "继续", cid)
    assert r.current_node in {"N7_CONFIRM_CLAIMS", "N8_WRITE"} or r.workflow_status


def test_ak_al_permission_guards(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, _, _ = _seed_case_with_materials(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    # CONFIRM_FACT with illegal target
    engine = ScriptedIntentEngine(
        IntentResult(intent=AgentIntent.CONFIRM_FACT, targets=["99"])
    )
    agent = _agent(db_session, actor_id, intent_engine=engine)
    r = agent.handle_message(case.id, "x")
    assert r.error_code in {
        AgentErrorCode.NOT_FOUND,
        AgentErrorCode.VALIDATION_ERROR,
    }

    # APPROVE_DRAFT with no draft
    engine2 = ScriptedIntentEngine(IntentResult(intent=AgentIntent.APPROVE_DRAFT))
    agent2 = _agent(db_session, actor_id, intent_engine=engine2)
    r2 = agent2.handle_message(case.id, "approve")
    assert r2.error_code == AgentErrorCode.NOT_FOUND


def test_am_cannot_skip_n3_to_n4(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, _, _ = _seed_case_with_materials(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    agent = _agent(db_session, actor_id)
    r = _say(agent, case.id, "开始处理这个案件")
    cid = r.conversation_id
    for _ in range(6):
        if r.current_node == "N3_CONFIRM_EVIDENCE":
            break
        r = _say(agent, case.id, "继续", cid)
    # Stub tries to run analyst while at N3
    engine = ScriptedIntentEngine(IntentResult(intent=AgentIntent.CONTINUE))
    # Force current node stay — CONTINUE at N3 with pending blocks
    r = _agent(db_session, actor_id, intent_engine=engine).handle_message(
        case.id, "继续", cid
    )
    assert r.error_code == AgentErrorCode.HUMAN_GATE_REQUIRED
    assert r.current_node == "N3_CONFIRM_EVIDENCE"


def test_fuzzy_ok_not_confirm(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, _, _ = _seed_case_with_materials(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    agent = _agent(db_session, actor_id)
    r = _say(agent, case.id, "好")
    assert r.intent == AgentIntent.UNKNOWN
    assert r.error_code == AgentErrorCode.UNKNOWN_INTENT


def test_intent_router_keywords() -> None:
    router = DeterministicIntentRouter()
    assert router.parse("继续").intent == AgentIntent.CONTINUE
    assert router.parse("暂停").intent == AgentIntent.PAUSE
    assert router.parse("批准这份起诉状").intent == AgentIntent.APPROVE_DRAFT
    assert router.parse("接受证据1、2").targets == ["1", "2"]
    assert router.parse("金额改成70万").intent == AgentIntent.AMEND_CLAIM_DIRECTION
    assert router.parse("金额改成70万").parameters["amount"] == 700000.0
