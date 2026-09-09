"""Read-only workspace query for lawyer MVP UI (no state mutations)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.application.material_usability import (
    MaterialUsabilityPolicy,
    MaterialUsabilityView,
)
from backend.application.pleading_readiness import PleadingReadinessService
from backend.llm.factory import ai_mode_label
from backend.models import (
    AgentMessage,
    Case,
    CaseParty,
    ClaimDirection,
    DocumentDraft,
    EvidenceItem,
    Fact,
    WorkflowInstance,
    WorkflowNode,
)

NODE_LABELS_ZH: dict[str, str] = {
    "N0_CREATE": "N0 创建案件",
    "N1_PARSE": "N1 解析材料",
    "N2_ORGANIZE": "N2 整理证据",
    "N3_CONFIRM_EVIDENCE": "N3 律师确认材料",
    "N4_ANALYZE": "N4 案情分析",
    "N5_CONFIRM_PARTIES": "N5 确认当事人",
    "N6_CONFIRM_FACTS": "N6 确认案件事实",
    "N7_CONFIRM_CLAIMS": "N7 确认诉讼请求",
    "N8_WRITE": "N8 生成起诉状",
    "N9_REVIEW": "N9 律师审核",
}


class WorkspaceQueryService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_cases(self) -> list[dict[str, Any]]:
        cases = list(
            self.session.scalars(select(Case).order_by(Case.updated_at.desc()))
        )
        return [self._case_summary(c) for c in cases]

    def get_workspace(self, case_id: UUID) -> dict[str, Any]:
        case = self.session.get(Case, case_id)
        if case is None:
            raise LookupError("case not found")
        workflow = self._workflow_block(case_id)
        policy = MaterialUsabilityPolicy(self.session)
        usable = [
            self._material_row(policy.describe_material(m), pool="usable")
            for m in policy.list_usable_materials(case_id)
        ]
        pending = [
            self._material_row(policy.describe_material(m), pool="pending")
            for m in policy.list_unusable_materials(case_id)
        ]
        voided = [
            self._material_row(policy.describe_material(m), pool="void")
            for m in policy.list_void_materials(case_id)
        ]
        pool = policy.pool_summary(case_id)
        return {
            "case": self._case_detail(case),
            "workflow": workflow,
            # Lawyer "案件材料池" — SUCCEEDED only (never FAILED / pending / void)
            "materials": usable,
            "usable_materials": usable,
            "pending_materials": pending,
            "void_materials": voided,
            "material_pool": pool,
            "analysis_disclosure": pool["disclosure"],
            "evidence": self._evidence(case_id),
            "parties": self._parties(case_id),
            "facts": self._facts(case_id),
            "claim_direction": self._claim(case_id),
            "draft": self._draft(case_id),
            "pleading_readiness": self._pleading_readiness(case_id),
            "conversation": self._conversation(case_id),
            "node_labels": NODE_LABELS_ZH,
            "ai": ai_mode_label(),
        }

    def _pleading_readiness(self, case_id: UUID) -> dict[str, Any]:
        result = PleadingReadinessService(self.session).evaluate(case_id)
        return result.model_dump(mode="json")

    def _case_summary(self, case: Case) -> dict[str, Any]:
        parties = self._parties(case.id)
        plaintiff = next((p["name"] for p in parties if p["role"] == "PLAINTIFF"), None)
        defendant = next((p["name"] for p in parties if p["role"] == "DEFENDANT"), None)
        wf = self._workflow_block(case.id)
        return {
            "id": str(case.id),
            "title": case.title,
            "case_no_internal": case.case_no_internal,
            "status": case.status,
            "plaintiff": plaintiff,
            "defendant": defendant,
            "workflow_status": wf.get("status"),
            "current_node": wf.get("current_node"),
            "current_node_label": wf.get("current_node_label"),
            "updated_at": case.updated_at.isoformat() if case.updated_at else None,
        }

    def _case_detail(self, case: Case) -> dict[str, Any]:
        return {
            "id": str(case.id),
            "title": case.title,
            "case_no_internal": case.case_no_internal,
            "status": case.status,
            "goal_summary": case.goal_summary,
            "owner_user_id": str(case.owner_user_id),
            "created_at": case.created_at.isoformat() if case.created_at else None,
            "updated_at": case.updated_at.isoformat() if case.updated_at else None,
        }

    def _workflow_block(self, case_id: UUID) -> dict[str, Any]:
        active = list(
            self.session.scalars(
                select(WorkflowInstance).where(
                    WorkflowInstance.case_id == case_id,
                    WorkflowInstance.status.notin_(
                        ["SUCCEEDED", "FAILED", "CANCELLED"]
                    ),
                )
            )
        )
        if len(active) > 1:
            raise ValueError("multiple active workflow instances")
        inst = active[0] if active else self.session.scalars(
            select(WorkflowInstance)
            .where(WorkflowInstance.case_id == case_id)
            .order_by(WorkflowInstance.created_at.desc())
        ).first()
        if inst is None:
            return {
                "status": None,
                "current_node": None,
                "current_node_label": None,
                "waiting_reason": None,
                "pending_count": None,
                "blocking_reason": None,
                "instance_id": None,
            }
        node = (
            self.session.get(WorkflowNode, inst.current_node_id)
            if inst.current_node_id
            else None
        )
        code = node.code if node else None
        pending = 0
        blocking = None
        if code == "N3_CONFIRM_EVIDENCE":
            pending = len(
                [
                    e
                    for e in self._evidence(case_id)
                    if e["acceptance"] == "PENDING"
                ]
            )
            if pending:
                blocking = f"{pending} 条证据待律师确认"
        elif code == "N5_CONFIRM_PARTIES":
            pending = len([p for p in self._parties(case_id) if p["layer"] == "CANDIDATE"])
            if pending:
                blocking = f"{pending} 个当事人待确认"
        elif code == "N6_CONFIRM_FACTS":
            pending = len([f for f in self._facts(case_id) if f["status"] == "CANDIDATE"])
            if pending:
                blocking = f"{pending} 个事实候选待处理"
        elif code == "N7_CONFIRM_CLAIMS":
            pending = len(
                [
                    c
                    for c in [self._claim(case_id)]
                    if c and c.get("status") == "CANDIDATE"
                ]
            )
            if pending:
                blocking = "诉讼请求待确认"
        elif code == "N9_REVIEW":
            draft = self._draft(case_id)
            if draft and draft.get("status") in {"DRAFT", "IN_REVIEW"}:
                blocking = f"草稿 v{draft['version']} 待审核"
        return {
            "status": inst.status,
            "current_node": code,
            "current_node_label": NODE_LABELS_ZH.get(code or "", code),
            "waiting_reason": inst.waiting_reason,
            "pending_count": pending or None,
            "blocking_reason": blocking,
            "instance_id": str(inst.id),
        }

    def _material_row(self, view: MaterialUsabilityView, *, pool: str) -> dict[str, Any]:
        m = view.material
        # Prefer the usable SUCCEEDED EC when present; else latest for diagnostics only.
        succeeded = list(view.succeeded_ecs)
        display_ec = (
            succeeded[-1] if succeeded else (view.all_ecs[-1] if view.all_ecs else None)
        )
        return {
            "id": str(m.id),
            "filename": m.filename,
            "mime": m.mime,
            "byte_size": m.byte_size,
            "parse_status": m.parse_status,
            "life_status": m.life_status,
            "life_status_label": (
                "已作废"
                if m.life_status == "VOID"
                else ("可读取" if view.usable else "待处理")
            ),
            "pool": pool,
            "usable": view.usable,
            "status_label": view.status_label,
            "pending_reason": view.pending_reason,
            "analysis_participation": view.analysis_participation,
            "void_reason": m.void_reason,
            "created_at": m.created_at.isoformat() if m.created_at else None,
            "page_count": display_ec.page_count if display_ec else None,
            "extraction_method": display_ec.extraction_method if display_ec else None,
            "extraction_status": display_ec.status if display_ec else None,
            "extraction_error": display_ec.error_detail if display_ec else None,
            "succeeded_extracted_content_id": (
                str(succeeded[-1].id) if len(succeeded) == 1 else None
            ),
            "succeeded_ec_count": len(succeeded),
        }

    def _evidence(self, case_id: UUID) -> list[dict[str, Any]]:
        rows = list(
            self.session.scalars(
                select(EvidenceItem)
                .where(
                    EvidenceItem.case_id == case_id,
                    EvidenceItem.is_current.is_(True),
                )
                .order_by(EvidenceItem.number.asc())
            )
        )
        return [
            {
                "id": str(e.id),
                "number": e.number,
                "title": e.title,
                "summary": e.summary,
                "acceptance": e.acceptance,
                "version": e.version,
                "category": e.category,
            }
            for e in rows
        ]

    def _parties(self, case_id: UUID) -> list[dict[str, Any]]:
        rows = list(
            self.session.scalars(
                select(CaseParty)
                .where(
                    CaseParty.case_id == case_id,
                    CaseParty.is_current.is_(True),
                )
                .order_by(CaseParty.created_at.asc(), CaseParty.party_key.asc())
            )
        )
        role_labels = {
            "PLAINTIFF": "原告",
            "DEFENDANT": "被告",
            "THIRD_PARTY": "第三人",
            "OTHER": "其他",
        }
        status_labels = {
            "CANDIDATE": "待确认",
            "CONFIRMED": "已确认",
            "REJECTED": "已拒绝",
            "SUPERSEDED": "已替代",
        }
        return [
            {
                "party_key": str(p.party_key),
                "role": p.role,
                "role_label": role_labels.get(p.role, p.role),
                "name": p.name,
                "party_type": p.party_type,
                "layer": p.layer,
                "status_label": status_labels.get(p.layer, p.layer),
                "version": p.version,
                "display_index": i,
            }
            for i, p in enumerate(rows, start=1)
        ]

    def _facts(self, case_id: UUID) -> list[dict[str, Any]]:
        rows = list(
            self.session.scalars(
                select(Fact)
                .where(Fact.case_id == case_id, Fact.is_current.is_(True))
                .order_by(Fact.created_at.asc(), Fact.fact_key.asc())
            )
        )
        return [
            {
                "fact_key": str(f.fact_key),
                "statement": f.statement,
                "status": f.status,
                "version": f.version,
                "stale": f.stale,
                "display_index": i,
            }
            for i, f in enumerate(rows, start=1)
        ]

    def _claim(self, case_id: UUID) -> dict[str, Any] | None:
        row = self.session.scalars(
            select(ClaimDirection)
            .where(
                ClaimDirection.case_id == case_id,
                ClaimDirection.is_current.is_(True),
            )
            .order_by(ClaimDirection.created_at.desc())
        ).first()
        if row is None:
            return None
        payload = row.payload or {}
        claims = payload.get("claims") or []
        first = claims[0] if claims else {}
        return {
            "claim_direction_key": str(row.claim_direction_key),
            "status": row.status,
            "version": row.version,
            "stale": row.stale,
            "overall_strategy": payload.get("overall_strategy"),
            "claim_type": first.get("claim_type"),
            "description": first.get("description"),
            "amount": first.get("amount"),
            "currency": first.get("currency"),
            "calculation_basis": first.get("calculation_basis"),
            "claims": claims,
        }

    def _draft(self, case_id: UUID) -> dict[str, Any] | None:
        row = self.session.scalars(
            select(DocumentDraft)
            .where(DocumentDraft.case_id == case_id)
            .order_by(DocumentDraft.version.desc())
        ).first()
        if row is None:
            return None
        return {
            "id": str(row.id),
            "doc_type": row.doc_type,
            "version": row.version,
            "status": row.status,
            "stale_reason": row.stale_reason,
            "body_structured_json": row.body_structured_json,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }

    def _conversation(self, case_id: UUID) -> list[dict[str, Any]]:
        rows = list(
            self.session.scalars(
                select(AgentMessage)
                .where(
                    AgentMessage.case_id == case_id,
                    AgentMessage.role.in_(["USER", "AGENT"]),
                )
                .order_by(AgentMessage.created_at.asc())
                .limit(200)
            )
        )
        return [
            {
                "id": str(m.id),
                "role": m.role,
                "content": m.content,
                "created_at": m.created_at.isoformat() if m.created_at else None,
                "parsed_intent_json": m.parsed_intent_json,
            }
            for m in rows
        ]
