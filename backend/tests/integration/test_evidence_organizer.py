"""Phase 5 — Evidence Organizer anti-examples and N2/N3 gate."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.application.evidence_organizer import EvidenceOrganizerService
from backend.domain.errors import ValidationError
from backend.domain.services import DomainService
from backend.models import (
    AuditLog,
    EvidenceItem,
    EvidenceItemSpan,
    HumanDecision,
    SkillExecution,
)
from backend.schemas.evidence_proposal import EvidenceItemProposal
from backend.skills.evidence_organizer import ScriptedOrganizerEngine
from backend.tests.workflow.helpers import ensure_pleading_prep_template, node_by_code
from backend.workflow.runtime import WorkflowRuntime


def _seed_case_with_span(
    session: Session,
    *,
    owner_id: uuid.UUID,
    actor_id: uuid.UUID,
    text: str = "甲方应支付设计服务费人民币十万元整。",
    filename: str = "合同.pdf",
) -> tuple:
    svc = DomainService(session)
    case = svc.create_case(title="P5 Case", owner_user_id=owner_id)
    material = svc.register_material(
        case_id=case.id,
        filename=filename,
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
    return svc, case, material, ec, span


def _proposal(
    *,
    span_ids: list[uuid.UUID],
    title: str = "服务费条款",
    summary: str = "约定服务费",
    category: str = "PAYMENT",
) -> EvidenceItemProposal:
    return EvidenceItemProposal(
        proposal_id=uuid.uuid4(),
        title=title,
        summary=summary,
        category=category,
        source_span_ids=span_ids,
        confidence=0.6,
        organizer_reason="test proposal",
    )


# ----- A–E: span hard constraints -----


def test_a_filename_quote_without_span_ids_rejected(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, _, ec, _ = _seed_case_with_span(db_session, owner_id=owner_id, actor_id=actor_id)
    org = EvidenceOrganizerService(db_session)
    result = org.organize(
        case_id=case.id,
        extracted_content_ids=[ec.id],
        actor_id=actor_id,
        raw_proposals=[
            {
                "proposal_id": str(uuid.uuid4()),
                "title": "合同关系",
                "summary": "甲方应支付服务费",
                "category": "CONTRACT",
                "source": "合同.pdf",
                "quote": "甲方应支付服务费...",
                "confidence": 0.9,
                "organizer_reason": "filename-only",
            }
        ],
    )
    assert result.created == []
    assert result.failed_proposals
    assert "source_span_ids" in result.failed_proposals[0]["error"]
    assert db_session.scalar(select(func.count()).select_from(EvidenceItem)) == 0


def test_b_nonexistent_span_rejected(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, _, ec, _ = _seed_case_with_span(db_session, owner_id=owner_id, actor_id=actor_id)
    fake = uuid.uuid4()
    org = EvidenceOrganizerService(
        db_session, engine=ScriptedOrganizerEngine([_proposal(span_ids=[fake])])
    )
    result = org.organize(
        case_id=case.id, extracted_content_ids=[ec.id], actor_id=actor_id
    )
    assert result.created == []
    assert any("not found" in f["error"] for f in result.failed_proposals)


def test_c_cross_case_span_rejected(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case_a, _, ec_a, _ = _seed_case_with_span(
        db_session, owner_id=owner_id, actor_id=actor_id, text="案件A合同正文足够长。"
    )
    _, _, _, _, span_b = _seed_case_with_span(
        db_session, owner_id=owner_id, actor_id=actor_id, text="案件B付款通知金额明细。"
    )
    org = EvidenceOrganizerService(
        db_session, engine=ScriptedOrganizerEngine([_proposal(span_ids=[span_b.id])])
    )
    result = org.organize(
        case_id=case_a.id, extracted_content_ids=[ec_a.id], actor_id=actor_id
    )
    assert result.created == []
    assert any("cross-case" in f["error"] for f in result.failed_proposals)


def test_d_failed_ec_span_rejected(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, _, ec, span = _seed_case_with_span(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    # Simulate orphaned span under FAILED EC (bypass Domain create_source_span guard).
    ec.status = "FAILED"
    ec.full_text = None
    db_session.flush()

    org = EvidenceOrganizerService(
        db_session, engine=ScriptedOrganizerEngine([_proposal(span_ids=[span.id])])
    )
    with pytest.raises(ValidationError, match="not SUCCEEDED"):
        org.organize(
            case_id=case.id, extracted_content_ids=[ec.id], actor_id=actor_id
        )


def test_d_failed_ec_span_via_raw_after_trusted_scope(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    """Span exists but EC later FAILED — proposal referencing it must be rejected."""
    svc, case, material, ec_ok, span_ok = _seed_case_with_span(
        db_session, owner_id=owner_id, actor_id=actor_id, text="可信正文内容足够长。"
    )
    text_bad = "曾成功解析后被标记失败的正文。"
    ec_bad = svc.create_extracted_content(
        material_id=material.id,
        extraction_method="pdfplumber",
        extraction_version="v2",
        full_text=text_bad,
        actor_id=actor_id,
    )
    span_bad = svc.create_source_span(
        material_id=material.id,
        extracted_content_id=ec_bad.id,
        character_start=0,
        character_end=len(text_bad),
        quote=text_bad,
        extraction_method="pdfplumber",
        extraction_version="v2",
    )
    ec_bad.status = "FAILED"
    ec_bad.full_text = None
    db_session.flush()

    org = EvidenceOrganizerService(
        db_session,
        engine=ScriptedOrganizerEngine([_proposal(span_ids=[span_bad.id])]),
    )
    result = org.organize(
        case_id=case.id, extracted_content_ids=[ec_ok.id], actor_id=actor_id
    )
    assert result.created == []
    assert any(
        "FAILED" in f["error"] or "outside organizer scope" in f["error"]
        for f in result.failed_proposals
    )
    _ = span_ok


def test_e_empty_span_set_rejected(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, _, ec, _ = _seed_case_with_span(db_session, owner_id=owner_id, actor_id=actor_id)
    empty = EvidenceItemProposal.model_construct(
        proposal_id=uuid.uuid4(),
        title="空来源",
        summary="x",
        category="OTHER",
        source_span_ids=[],
        confidence=0.1,
        organizer_reason="empty spans",
    )
    org = EvidenceOrganizerService(
        db_session, engine=ScriptedOrganizerEngine([empty])
    )
    result = org.organize(
        case_id=case.id, extracted_content_ids=[ec.id], actor_id=actor_id
    )
    assert result.created == []
    assert any("missing source_span_ids" in f["error"] for f in result.failed_proposals)


# ----- F–H: acceptance -----


def test_f_organizer_creates_pending_only(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, _, ec, span = _seed_case_with_span(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    org = EvidenceOrganizerService(
        db_session, engine=ScriptedOrganizerEngine([_proposal(span_ids=[span.id])])
    )
    result = org.organize(
        case_id=case.id, extracted_content_ids=[ec.id], actor_id=actor_id
    )
    assert len(result.created) == 1
    assert result.created[0].acceptance == "PENDING"


def test_g_accept_no_version_bump_with_decision_and_audit(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    svc, case, _, ec, span = _seed_case_with_span(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    org = EvidenceOrganizerService(
        db_session, engine=ScriptedOrganizerEngine([_proposal(span_ids=[span.id])])
    )
    created = org.organize(
        case_id=case.id, extracted_content_ids=[ec.id], actor_id=actor_id
    ).created[0]
    accepted = svc.accept_evidence(created.id, actor_id=actor_id)
    assert accepted.version == 1
    assert accepted.acceptance == "ACCEPTED"
    decisions = db_session.scalars(
        select(HumanDecision).where(HumanDecision.case_id == case.id)
    ).all()
    assert any(d.decision_type == "ACCEPT_EVIDENCE" for d in decisions)
    audits = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "accept_evidence")
    ).all()
    assert audits


def test_h_exclude_keeps_history(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    svc, case, _, ec, span = _seed_case_with_span(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    org = EvidenceOrganizerService(
        db_session, engine=ScriptedOrganizerEngine([_proposal(span_ids=[span.id])])
    )
    created = org.organize(
        case_id=case.id, extracted_content_ids=[ec.id], actor_id=actor_id
    ).created[0]
    eid = created.id
    excluded = svc.exclude_evidence(created.id, actor_id=actor_id)
    assert excluded.version == 1
    assert excluded.acceptance == "EXCLUDED"
    still = db_session.scalars(
        select(EvidenceItem).where(EvidenceItem.id == eid, EvidenceItem.is_current.is_(True))
    ).one()
    assert still.acceptance == "EXCLUDED"


# ----- I–M: version rules -----


def test_i_j_k_l_content_changes_bump_version(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    svc, case, _, _, span = _seed_case_with_span(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    text2 = "补充协议约定付款方式为银行转账。"
    material2 = svc.register_material(
        case_id=case.id,
        filename="补充.pdf",
        mime="application/pdf",
        byte_size=len(text2),
        content_hash=f"h2-{uuid.uuid4().hex[:8]}",
        storage_key=f"k2-{uuid.uuid4().hex[:8]}",
        created_by=actor_id,
    )
    ec2 = svc.create_extracted_content(
        material_id=material2.id,
        extraction_method="pdfplumber",
        extraction_version="v1",
        full_text=text2,
        actor_id=actor_id,
    )
    span2 = svc.create_source_span(
        material_id=material2.id,
        extracted_content_id=ec2.id,
        character_start=0,
        character_end=len(text2),
        quote=text2,
        extraction_method="pdfplumber",
        extraction_version="v1",
    )
    item = svc.create_evidence_item(
        case_id=case.id,
        number="1",
        title="原标题",
        category="OTHER",
        summary="原摘要",
        source_span_ids=[span.id],
        actor_id=actor_id,
    )
    v = item.version
    assert svc.amend_evidence_item(item.id, actor_id=actor_id, title="新标题").version == v + 1
    v += 1
    assert (
        svc.amend_evidence_item(item.id, actor_id=actor_id, summary="新摘要").version == v + 1
    )
    v += 1
    assert (
        svc.amend_evidence_item(item.id, actor_id=actor_id, category="CONTRACT").version
        == v + 1
    )
    v += 1
    amended = svc.amend_evidence_item(
        item.id, actor_id=actor_id, source_span_ids=[span.id, span2.id]
    )
    assert amended.version == v + 1
    links = db_session.scalars(
        select(EvidenceItemSpan).where(
            EvidenceItemSpan.evidence_item_id == item.id,
            EvidenceItemSpan.evidence_item_version == amended.version,
        )
    ).all()
    assert {x.source_span_id for x in links} == {span.id, span2.id}


def test_m_number_only_no_version_bump(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    svc, case, _, _, span = _seed_case_with_span(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    item = svc.create_evidence_item(
        case_id=case.id,
        number="1",
        title="T",
        category="OTHER",
        source_span_ids=[span.id],
        actor_id=actor_id,
    )
    renumbered = svc.amend_evidence_item(item.id, actor_id=actor_id, number="E-01")
    assert renumbered.version == 1
    assert renumbered.number == "E-01"


# ----- N–P: duplicate / multi-span / EC scope -----


def test_n_duplicate_organizer_run_skips(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, _, ec, span = _seed_case_with_span(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    prop = _proposal(span_ids=[span.id])
    org = EvidenceOrganizerService(
        db_session, engine=ScriptedOrganizerEngine([prop])
    )
    first = org.organize(
        case_id=case.id, extracted_content_ids=[ec.id], actor_id=actor_id
    )
    assert len(first.created) == 1
    stable_id = first.created[0].id

    org2 = EvidenceOrganizerService(
        db_session,
        engine=ScriptedOrganizerEngine(
            [
                _proposal(
                    span_ids=[span.id],
                    title=prop.title,
                    summary=prop.summary or "",
                    category=prop.category,
                )
            ]
        ),
    )
    second = org2.organize(
        case_id=case.id, extracted_content_ids=[ec.id], actor_id=actor_id
    )
    assert second.created == []
    assert second.skipped
    assert second.evidence_item_ids == [stable_id]
    count = db_session.scalar(
        select(func.count())
        .select_from(EvidenceItem)
        .where(EvidenceItem.case_id == case.id, EvidenceItem.is_current.is_(True))
    )
    assert count == 1


def test_n2_same_spans_content_change_amends(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, _, ec, span = _seed_case_with_span(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    org = EvidenceOrganizerService(
        db_session,
        engine=ScriptedOrganizerEngine(
            [_proposal(span_ids=[span.id], title="旧标题", category="OTHER")]
        ),
    )
    first = org.organize(
        case_id=case.id, extracted_content_ids=[ec.id], actor_id=actor_id
    ).created[0]
    org2 = EvidenceOrganizerService(
        db_session,
        engine=ScriptedOrganizerEngine(
            [_proposal(span_ids=[span.id], title="新标题", category="CONTRACT")]
        ),
    )
    second = org2.organize(
        case_id=case.id, extracted_content_ids=[ec.id], actor_id=actor_id
    )
    assert second.created == []
    assert len(second.amended) == 1
    assert second.amended[0].id == first.id
    assert second.amended[0].version == 2
    assert second.amended[0].title == "新标题"


def test_o_multi_span_evidence_item(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    svc, case, _, _, _ = _seed_case_with_span(
        db_session, owner_id=owner_id, actor_id=actor_id, text="占位材料正文足够长。"
    )
    t_a = "合同第3条：甲方应支付服务费。"
    t_b = "补充协议第2条：付款期限延长三十日。"
    m_a = svc.register_material(
        case_id=case.id,
        filename="a.pdf",
        mime="application/pdf",
        byte_size=10,
        content_hash=f"ha-{uuid.uuid4().hex[:8]}",
        storage_key=f"ka-{uuid.uuid4().hex[:8]}",
        created_by=actor_id,
    )
    m_b = svc.register_material(
        case_id=case.id,
        filename="b.pdf",
        mime="application/pdf",
        byte_size=10,
        content_hash=f"hb-{uuid.uuid4().hex[:8]}",
        storage_key=f"kb-{uuid.uuid4().hex[:8]}",
        created_by=actor_id,
    )
    ec_a = svc.create_extracted_content(
        material_id=m_a.id,
        extraction_method="pdfplumber",
        extraction_version="v1",
        full_text=t_a,
        actor_id=actor_id,
    )
    ec_b = svc.create_extracted_content(
        material_id=m_b.id,
        extraction_method="pdfplumber",
        extraction_version="v1",
        full_text=t_b,
        actor_id=actor_id,
    )
    s_a = svc.create_source_span(
        material_id=m_a.id,
        extracted_content_id=ec_a.id,
        character_start=0,
        character_end=len(t_a),
        quote=t_a,
        extraction_method="pdfplumber",
        extraction_version="v1",
    )
    s_b = svc.create_source_span(
        material_id=m_b.id,
        extracted_content_id=ec_b.id,
        character_start=0,
        character_end=len(t_b),
        quote=t_b,
        extraction_method="pdfplumber",
        extraction_version="v1",
    )
    org = EvidenceOrganizerService(
        db_session,
        engine=ScriptedOrganizerEngine(
            [_proposal(span_ids=[s_a.id, s_b.id], title="付款义务组合")]
        ),
    )
    result = org.organize(
        case_id=case.id,
        extracted_content_ids=[ec_a.id, ec_b.id],
        actor_id=actor_id,
    )
    assert len(result.created) == 1
    links = db_session.scalars(
        select(EvidenceItemSpan).where(
            EvidenceItemSpan.evidence_item_id == result.created[0].id,
            EvidenceItemSpan.evidence_item_version == 1,
        )
    ).all()
    assert {x.source_span_id for x in links} == {s_a.id, s_b.id}


def test_p_multiple_ec_without_explicit_ids_rejected(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    svc, case, material, _, _ = _seed_case_with_span(
        db_session, owner_id=owner_id, actor_id=actor_id, text="第一次解析正文足够长。"
    )
    svc.create_extracted_content(
        material_id=material.id,
        extraction_method="pdfplumber",
        extraction_version="v2",
        full_text="第二次解析正文也足够长。",
        actor_id=actor_id,
    )
    org = EvidenceOrganizerService(db_session)
    with pytest.raises(ValidationError, match="multiple ExtractedContent"):
        org.resolve_extracted_content_ids(case_id=case.id, material_ids=[material.id])
    with pytest.raises(ValidationError, match="no implicit"):
        org.resolve_extracted_content_ids(case_id=case.id)


# ----- Q: N2 / N3 workflow -----


def _advance_to_n2(runtime: WorkflowRuntime, case_id: uuid.UUID):
    inst = runtime.create_instance(case_id=case_id)
    r = runtime.start_instance(inst.id)
    n2 = node_by_code(runtime.session, inst.template_id, "N2_ORGANIZE")
    safety = 0
    while r.node_run is not None and r.node_run.node_id != n2.id:
        safety += 1
        if safety > 10:
            raise AssertionError("did not reach N2_ORGANIZE")
        r = runtime.complete_node(r.node_run.id)
        if r.instance.status == "WAITING_USER":
            raise AssertionError(f"unexpected WAITING_USER before N2: {r.instance.waiting_reason}")
    assert r.node_run is not None
    assert r.node_run.node_id == n2.id
    return inst, r.node_run


def test_q_n3_human_gate_pending_blocks_complete(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    ensure_pleading_prep_template(db_session)
    svc, case, _, ec, span = _seed_case_with_span(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    runtime = WorkflowRuntime(db_session)
    inst, n2_run = _advance_to_n2(runtime, case.id)

    org = EvidenceOrganizerService(
        db_session,
        engine=ScriptedOrganizerEngine([_proposal(span_ids=[span.id])]),
    )
    result = org.run_n2_organize(
        instance_id=inst.id,
        node_run_id=n2_run.id,
        extracted_content_ids=[ec.id],
        actor_id=actor_id,
    )
    assert result.created
    assert result.created[0].acceptance == "PENDING"

    inst = runtime.get_instance(inst.id)
    assert inst.status == "WAITING_USER"
    assert inst.waiting_reason == "EVIDENCE"

    skill = db_session.scalars(
        select(SkillExecution).where(SkillExecution.node_run_id == n2_run.id)
    ).one()
    assert skill.skill_code == "EvidenceOrganizerSkill"
    assert skill.status == "SUCCEEDED"
    assert skill.metrics_json["input"]["case_id"] == str(case.id)

    with pytest.raises(ValidationError, match="PENDING"):
        org.assert_n3_ready_to_complete(inst.id)

    # Lawyer accepts
    for eid in result.evidence_item_ids:
        svc.accept_evidence(eid, actor_id=actor_id)

    org.assert_n3_ready_to_complete(inst.id)

    resumed = runtime.resume_instance(inst.id, command_id=uuid.uuid4())
    assert resumed.node_run is not None
    n3 = node_by_code(db_session, inst.template_id, "N3_CONFIRM_EVIDENCE")
    assert resumed.node_run.node_id == n3.id

    done = org.complete_n3_confirm_evidence(
        instance_id=inst.id,
        node_run_id=resumed.node_run.id,
        auto_advance=False,
    )
    assert done.node_run is not None
    assert done.node_run.status == "SUCCEEDED"
    # Phase 5: must not auto-start N4
    assert done.instance.status == "RUNNING" or done.instance.current_node_id == n3.id
    n4 = node_by_code(db_session, inst.template_id, "N4_ANALYZE")
    n4_runs = runtime.list_node_runs(inst.id, node_id=n4.id)
    assert n4_runs == []


def test_skill_execution_records_failed_proposals(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    ensure_pleading_prep_template(db_session)
    _, case, _, ec, _ = _seed_case_with_span(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    runtime = WorkflowRuntime(db_session)
    inst, n2_run = _advance_to_n2(runtime, case.id)
    org = EvidenceOrganizerService(
        db_session,
        engine=ScriptedOrganizerEngine([_proposal(span_ids=[uuid.uuid4()])]),
    )
    result = org.run_n2_organize(
        instance_id=inst.id,
        node_run_id=n2_run.id,
        extracted_content_ids=[ec.id],
        actor_id=actor_id,
        auto_complete=False,
    )
    assert result.failed_proposals
    skill = db_session.get(SkillExecution, result.skill_execution_id)
    assert skill is not None
    assert skill.status == "FAILED"
    assert skill.metrics_json["output"]["failed_proposals"]
