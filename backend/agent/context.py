"""Case context snapshot — rebuilt from DB every turn (no in-memory state)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import (
    Case,
    CaseParty,
    ClaimDirection,
    DocumentDraft,
    EvidenceItem,
    Fact,
    WorkflowInstance,
    WorkflowNode,
)
from backend.workflow.runtime import WorkflowRuntime


@dataclass
class CaseContext:
    case: Case
    conversation_id: UUID
    instance: WorkflowInstance | None = None
    current_node: WorkflowNode | None = None
    workflow_status: str | None = None
    waiting_reason: str | None = None
    pending_evidence: list[EvidenceItem] = field(default_factory=list)
    pending_parties: list[CaseParty] = field(default_factory=list)
    pending_facts: list[Fact] = field(default_factory=list)
    pending_claims: list[ClaimDirection] = field(default_factory=list)
    latest_draft: DocumentDraft | None = None
    available_actions: list[dict[str, str]] = field(default_factory=list)
    context_json: dict[str, Any] = field(default_factory=dict)
    focus_issue_key: UUID | None = None
    focus_issue_version: int | None = None
    focus_object_type: str | None = None
    focus_object_ref: str | None = None


class CaseContextService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.runtime = WorkflowRuntime(session)

    def load(self, case_id: UUID, *, conversation_id: UUID) -> CaseContext:
        case = self.session.get(Case, case_id)
        if case is None:
            raise ValueError(f"case not found: {case_id}")

        instance = self._active_instance(case_id)
        node = None
        if instance and instance.current_node_id:
            node = self.session.get(WorkflowNode, instance.current_node_id)

        ctx = CaseContext(
            case=case,
            conversation_id=conversation_id,
            instance=instance,
            current_node=node,
            workflow_status=instance.status if instance else None,
            waiting_reason=instance.waiting_reason if instance else None,
            context_json=dict(instance.context_json or {}) if instance else {},
        )
        ctx.pending_evidence = self._pending_evidence(case_id)
        ctx.pending_parties = self._pending_parties(case_id)
        ctx.pending_facts = self._pending_facts(case_id)
        ctx.pending_claims = self._pending_claims(case_id)
        ctx.latest_draft = self._latest_draft(case_id)
        ctx.available_actions = self._actions(ctx)
        return ctx

    def _active_instance(self, case_id: UUID) -> WorkflowInstance | None:
        rows = list(
            self.session.scalars(
                select(WorkflowInstance).where(
                    WorkflowInstance.case_id == case_id,
                    WorkflowInstance.status.notin_(
                        ["SUCCEEDED", "FAILED", "CANCELLED"]
                    ),
                )
            )
        )
        if len(rows) > 1:
            raise ValueError("multiple active workflow instances for case")
        if rows:
            return rows[0]
        # Fall back to latest terminal instance for status / post-success context
        return self.session.scalars(
            select(WorkflowInstance)
            .where(WorkflowInstance.case_id == case_id)
            .order_by(WorkflowInstance.created_at.desc())
        ).first()

    def _pending_evidence(self, case_id: UUID) -> list[EvidenceItem]:
        return list(
            self.session.scalars(
                select(EvidenceItem).where(
                    EvidenceItem.case_id == case_id,
                    EvidenceItem.is_current.is_(True),
                    EvidenceItem.acceptance == "PENDING",
                )
            )
        )

    def _pending_parties(self, case_id: UUID) -> list[CaseParty]:
        return list(
            self.session.scalars(
                select(CaseParty).where(
                    CaseParty.case_id == case_id,
                    CaseParty.is_current.is_(True),
                    CaseParty.layer == "CANDIDATE",
                )
            )
        )

    def _pending_facts(self, case_id: UUID) -> list[Fact]:
        return list(
            self.session.scalars(
                select(Fact).where(
                    Fact.case_id == case_id,
                    Fact.is_current.is_(True),
                    Fact.status == "CANDIDATE",
                )
            )
        )

    def _pending_claims(self, case_id: UUID) -> list[ClaimDirection]:
        return list(
            self.session.scalars(
                select(ClaimDirection).where(
                    ClaimDirection.case_id == case_id,
                    ClaimDirection.is_current.is_(True),
                    ClaimDirection.status == "CANDIDATE",
                )
            )
        )

    def _latest_draft(self, case_id: UUID) -> DocumentDraft | None:
        return self.session.scalars(
            select(DocumentDraft)
            .where(
                DocumentDraft.case_id == case_id,
                DocumentDraft.doc_type == "CIVIL_COMPLAINT",
            )
            .order_by(DocumentDraft.version.desc())
        ).first()

    def _actions(self, ctx: CaseContext) -> list[dict[str, str]]:
        actions: list[dict[str, str]] = [
            {"type": "STATUS", "label": "查看状态"},
            {"type": "CONTINUE", "label": "继续"},
        ]
        code = ctx.current_node.code if ctx.current_node else None
        if code == "N3_CONFIRM_EVIDENCE":
            actions.append({"type": "ACCEPT_EVIDENCE", "label": "接受证据"})
            actions.append({"type": "EXCLUDE_EVIDENCE", "label": "排除证据"})
        if code == "N5_CONFIRM_PARTIES":
            actions.append({"type": "CREATE_PARTY", "label": "录入当事人"})
            actions.append({"type": "CONFIRM_PARTY", "label": "确认当事人"})
            actions.append({"type": "REJECT_PARTY", "label": "拒绝当事人"})
        if code == "N6_CONFIRM_FACTS":
            actions.append({"type": "CONFIRM_FACT", "label": "确认事实"})
            actions.append({"type": "REJECT_FACT", "label": "拒绝事实"})
        if code == "N7_CONFIRM_CLAIMS":
            actions.append(
                {"type": "CONFIRM_CLAIM_DIRECTION", "label": "确认诉讼请求"}
            )
        if code in {"N8_WRITE", "N9_REVIEW"}:
            actions.append({"type": "SHOW_DRAFT", "label": "查看起诉状"})
        if code == "N9_REVIEW":
            actions.append({"type": "APPROVE_DRAFT", "label": "批准起诉状"})
        if ctx.workflow_status == "WAITING_USER" and ctx.waiting_reason == "user_pause":
            actions.append({"type": "RESUME", "label": "恢复"})
        if ctx.workflow_status == "WAITING_RETRY":
            actions.append({"type": "RETRY", "label": "重试"})
        return actions
