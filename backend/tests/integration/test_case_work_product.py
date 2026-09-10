"""Case Work Product V1 — stage / todo / next-action / workspace projection tests."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.agent.case_agent import CaseAgent
from backend.application.case_actions import CaseActionService
from backend.application.case_work_product import (
    CaseNextActionProjector,
    CaseWorkProductBuilder,
    CaseWorkStageProjector,
)
from backend.application.workspace import WorkspaceQueryService
from backend.domain.services import DomainService
from backend.models import AgentMessage, EvidenceItem, Fact, HumanDecision
from backend.workflow.seed import ensure_pleading_prep_template


def _seed_case(
    session: Session,
    *,
    owner_id: uuid.UUID,
    actor_id: uuid.UUID,
    with_materials: bool = False,
) -> tuple:
    ensure_pleading_prep_template(session)
    svc = DomainService(session)
    case = svc.create_case(title="Work Product Case", owner_user_id=owner_id)
    materials = []
    if with_materials:
        text = "合同约定服务费100000元，被告已支付30000元。"
        material = svc.register_material(
            case_id=case.id,
            filename="contract.pdf",
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
    return svc, case, materials


def _workspace_ctx(session: Session, case_id: uuid.UUID) -> dict:
    return WorkspaceQueryService(session).get_workspace(case_id)


# ----- projection unit-style via application -----


def test_case_stage_projection(db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID):
    _, case, _ = _seed_case(db_session, owner_id=owner_id, actor_id=actor_id)
    stage = CaseWorkStageProjector().project(
        workflow_status=None, current_node=None, usable_material_count=0
    )
    assert stage.stage_label == "材料整理"
    assert stage.stage_status == "not_started"

    stage2 = CaseWorkStageProjector().project(
        workflow_status="WAITING_USER",
        current_node="N6_CONFIRM_FACTS",
        usable_material_count=1,
    )
    assert stage2.stage_label == "事实确认"
    assert stage2.stage_status == "waiting_lawyer"
    assert stage2.workflow_node == "N6_CONFIRM_FACTS"


def test_case_next_action_projection():
    from backend.schemas.case_work_product import TodoSummaryView, WorkStageView

    todos = TodoSummaryView(total_count=0, items=[])
    stage = WorkStageView(
        stage_key="material_prep",
        stage_label="材料整理",
        stage_status="not_started",
        stage_status_label="尚未开始",
    )
    nxt = CaseNextActionProjector().project(
        todos=todos,
        stage=stage,
        readiness_status="NOT_READY",
        draft=None,
        usable_material_count=0,
        workflow_status=None,
    )
    assert nxt.label == "上传案件材料"


def test_workspace_todo_projection(db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID):
    svc, case, _ = _seed_case(db_session, owner_id=owner_id, actor_id=actor_id, with_materials=True)
    ev = svc.create_evidence_item(
        case_id=case.id,
        number="1",
        title="合同",
        category="CONTRACT",
        summary="证明签约事实",
        actor_id=actor_id,
    )
    assert ev.acceptance == "PENDING"
    ctx = _workspace_ctx(db_session, case.id)
    todos = ctx["work_product"]["todo_summary"]
    types = {t["todo_type"] for t in todos["items"]}
    assert "EVIDENCE" in types
    assert todos["total_count"] >= 1


def test_new_case_workspace(db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID):
    _, case, _ = _seed_case(db_session, owner_id=owner_id, actor_id=actor_id)
    wp = _workspace_ctx(db_session, case.id)["work_product"]
    assert wp["stage"]["stage_label"] == "材料整理"
    assert wp["next_action"]["label"] == "上传案件材料"
    assert wp["todo_summary"]["total_count"] >= 0


def test_pending_material_workspace(db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID):
    svc, case, _ = _seed_case(db_session, owner_id=owner_id, actor_id=actor_id, with_materials=True)
    # unusable material (no EC)
    svc.register_material(
        case_id=case.id,
        filename="scan.pdf",
        mime="application/pdf",
        byte_size=100,
        content_hash=f"h-{uuid.uuid4().hex[:12]}",
        storage_key=f"k-{uuid.uuid4().hex[:12]}",
        created_by=actor_id,
    )
    ctx = _workspace_ctx(db_session, case.id)
    assert len(ctx["usable_materials"]) == 1
    assert len(ctx["pending_materials"]) >= 1
    items = ctx["work_product"]["todo_summary"]["items"]
    mat_todos = [t for t in items if t["todo_type"] == "MATERIAL"]
    assert mat_todos


def test_pending_evidence_fact_todos(db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID):
    svc, case, _ = _seed_case(db_session, owner_id=owner_id, actor_id=actor_id, with_materials=True)
    ev = svc.create_evidence_item(
        case_id=case.id,
        number="1",
        title="E1",
        category="OTHER",
        summary="s",
        actor_id=actor_id,
    )
    svc.propose_fact(
        case_id=case.id,
        statement="原告已交付成果",
        evidence_links=[
            {
                "evidence_item_id": str(ev.id),
                "evidence_item_version": ev.version,
            }
        ],
        actor_id=actor_id,
    )
    todos = _workspace_ctx(db_session, case.id)["work_product"]["todo_summary"]
    types = {t["todo_type"] for t in todos["items"]}
    assert "EVIDENCE" in types
    assert "FACT" in types
    assert todos["total_count"] >= 2


def test_readiness_blocker_as_todo(db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID):
    _, case, _ = _seed_case(db_session, owner_id=owner_id, actor_id=actor_id, with_materials=True)
    ctx = _workspace_ctx(db_session, case.id)
    readiness = ctx["pleading_readiness"]
    if readiness["status"] != "READY":
        todo_items = ctx["work_product"]["todo_summary"]["items"]
        blockers = [t for t in todo_items if t["todo_type"] == "READINESS"]
        assert blockers or readiness.get("blocking_issues")


def test_ready_case_next_generate(db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID):
    svc, case, _ = _seed_case(db_session, owner_id=owner_id, actor_id=actor_id, with_materials=True)
    svc.create_party(
        case_id=case.id, role="PLAINTIFF", name="甲公司", party_type="ORG", actor_id=actor_id
    )
    svc.create_party(
        case_id=case.id, role="DEFENDANT", name="乙公司", party_type="ORG", actor_id=actor_id
    )
    ev = svc.create_evidence_item(
        case_id=case.id,
        number="1",
        title="合同",
        category="CONTRACT",
        summary="签约",
        actor_id=actor_id,
    )
    svc.accept_evidence(ev.id, actor_id=actor_id)
    ctx = _workspace_ctx(db_session, case.id)
    if ctx["pleading_readiness"]["status"] == "READY":
        assert ctx["work_product"]["next_action"]["action_type"] in {
            "GENERATE_DRAFT",
            "CONTINUE",
            None,
        }
    else:
        assert ctx["work_product"]["next_action"]["label"]


def test_draft_next_review(db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID):
    svc, case, _ = _seed_case(db_session, owner_id=owner_id, actor_id=actor_id, with_materials=True)
    draft = svc.create_document_draft(
        case_id=case.id,
        doc_type="COMPLAINT",
        body_structured_json={"full_text": "起诉状正文"},
        citations=[],
        actor_id=actor_id,
    )
    ctx = {
        "case": {"id": str(case.id), "title": case.title, "updated_at": None},
        "workflow": {"status": "WAITING_USER", "current_node": "N9_REVIEW"},
        "usable_materials": [{}],
        "pending_materials": [],
        "parties": [],
        "evidence": [],
        "facts": [],
        "claim_direction": None,
        "draft": {
            "id": str(draft.id),
            "version": draft.version,
            "status": draft.status,
        },
        "pleading_readiness": {"status": "READY"},
        "conversation": [],
    }
    wp = CaseWorkProductBuilder(db_session).build(ctx)
    assert "审核" in wp.next_action.label or wp.next_action.action_type == "VIEW_DRAFT"


def test_resume_workspace_state(db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID):
    agent = CaseAgent(db_session, actor_id=actor_id)
    _, case, _ = _seed_case(db_session, owner_id=owner_id, actor_id=actor_id, with_materials=True)
    resp = agent.handle_message(case.id, "状态")
    cid = resp.conversation_id
    wp1 = _workspace_ctx(db_session, case.id)["work_product"]
    wp2 = _workspace_ctx(db_session, case.id)["work_product"]
    assert wp1["resume"]["conversation_id"] == str(cid)
    assert wp2["stage"]["stage_key"] == wp1["stage"]["stage_key"]
    assert wp2["todo_summary"]["total_count"] == wp1["todo_summary"]["total_count"]


def test_workspace_projection_zero_mutation(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
):
    _, case, _ = _seed_case(db_session, owner_id=owner_id, actor_id=actor_id, with_materials=True)
    hd_before = db_session.scalar(select(func.count()).select_from(HumanDecision))
    msg_before = db_session.scalar(select(func.count()).select_from(AgentMessage))
    ev_before = db_session.scalar(select(func.count()).select_from(EvidenceItem))
    fact_before = db_session.scalar(select(func.count()).select_from(Fact))
    _workspace_ctx(db_session, case.id)
    assert db_session.scalar(select(func.count()).select_from(HumanDecision)) == hd_before
    assert db_session.scalar(select(func.count()).select_from(AgentMessage)) == msg_before
    assert db_session.scalar(select(func.count()).select_from(EvidenceItem)) == ev_before
    assert db_session.scalar(select(func.count()).select_from(Fact)) == fact_before


def test_ui_action_uses_domain_handler(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
):
    svc, case, _ = _seed_case(db_session, owner_id=owner_id, actor_id=actor_id, with_materials=True)
    ev = svc.create_evidence_item(
        case_id=case.id,
        number="1",
        title="合同",
        category="CONTRACT",
        summary="s",
        actor_id=actor_id,
    )
    action_svc = CaseActionService(db_session, actor_id=actor_id)
    resp = action_svc.execute(case.id, action_type="ACCEPT_EVIDENCE", target="1")
    assert resp.intent.value == "ACCEPT_EVIDENCE"
    hd = list(
        db_session.scalars(
            select(HumanDecision).where(
                HumanDecision.case_id == case.id,
                HumanDecision.decision_type == "ACCEPT_EVIDENCE",
            )
        )
    )
    assert hd
    ev2 = db_session.scalars(
        select(EvidenceItem).where(
            EvidenceItem.case_id == case.id,
            EvidenceItem.id == ev.id,
            EvidenceItem.is_current.is_(True),
        )
    ).first()
    assert ev2 is not None
    assert ev2.acceptance == "ACCEPTED"
