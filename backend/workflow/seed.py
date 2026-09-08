"""Seed helpers for workflow templates (shared by Agent + tests)."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import WorkflowNode, WorkflowTemplate

PLEADING_NODES: list[tuple[str, str, str | None, int, bool, str | None]] = [
    ("N0_CREATE", "创建案件", None, 0, False, None),
    ("N1_PARSE", "材料解析", None, 1, False, None),
    ("N2_ORGANIZE", "证据整理", "EvidenceOrganizer", 2, False, None),
    ("N3_CONFIRM_EVIDENCE", "确认证据", None, 3, True, "EVIDENCE"),
    ("N4_ANALYZE", "案件分析", "CaseAnalyst", 4, False, None),
    ("N5_CONFIRM_PARTIES", "确认当事人", None, 5, True, "PARTY"),
    ("N6_CONFIRM_FACTS", "确认核心事实", None, 6, True, "FACT"),
    ("N7_CONFIRM_CLAIMS", "确认诉讼请求方向", None, 7, True, "CLAIM"),
    ("N8_WRITE", "起草起诉状", "PleadingWriter", 8, False, None),
    ("N9_REVIEW", "律师审核草稿", None, 9, True, "DRAFT_REVIEW"),
]


def ensure_pleading_prep_template(session: Session) -> WorkflowTemplate:
    existing = session.scalars(
        select(WorkflowTemplate).where(
            WorkflowTemplate.code == "PLEADING_PREP",
            WorkflowTemplate.version == 1,
        )
    ).first()
    if existing is not None:
        return existing

    template = WorkflowTemplate(
        id=uuid.uuid4(),
        code="PLEADING_PREP",
        name="起诉准备",
        version=1,
    )
    session.add(template)
    session.flush()
    for code, name, skill, order_index, is_gate, gate_type in PLEADING_NODES:
        session.add(
            WorkflowNode(
                template_id=template.id,
                code=code,
                name=name,
                skill_code=skill,
                order_index=order_index,
                is_human_gate=is_gate,
                gate_type=gate_type,
                on_failure_policy="RETRY",
            )
        )
    session.flush()
    return template


def node_by_code(session: Session, template_id: uuid.UUID, code: str) -> WorkflowNode:
    return session.scalars(
        select(WorkflowNode).where(
            WorkflowNode.template_id == template_id,
            WorkflowNode.code == code,
        )
    ).one()
