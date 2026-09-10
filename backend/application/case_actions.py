"""Structured workspace actions — same mutation path as Case Agent."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from backend.agent.case_agent import CaseAgent
from backend.agent.dto import AgentResponse
from backend.models import Case


class CaseActionService:
    """Map UI action buttons to natural-language agent commands."""

    _MESSAGES: dict[str, str | None] = {
        "ACCEPT_EVIDENCE": "接受证据{target}",
        "EXCLUDE_EVIDENCE": "排除证据{target}",
        "CONFIRM_FACT": "确认事实{target}",
        "REJECT_FACT": "拒绝事实{target}",
        "CONFIRM_PARTY": "确认当事人{target}",
        "REJECT_PARTY": "拒绝当事人{target}",
        "CONFIRM_CLAIM": "确认诉讼请求1",
        "REJECT_CLAIM": "拒绝诉讼请求1",
        "APPROVE_DRAFT": "批准这份起诉状",
        "GENERATE_DRAFT": "生成起诉状",
        "CONTINUE": "继续",
    }

    def __init__(self, session: Session, *, actor_id: UUID) -> None:
        self.session = session
        self.actor_id = actor_id

    @classmethod
    def to_message(cls, action_type: str, target: str | None = None) -> str:
        template = cls._MESSAGES.get(action_type)
        if template is None:
            raise ValueError(f"unsupported action_type: {action_type}")
        if "{target}" in template:
            if not target:
                raise ValueError(f"action {action_type} requires target")
            return template.format(target=target)
        return template

    def execute(
        self,
        case_id: UUID,
        *,
        action_type: str,
        target: str | None = None,
        conversation_id: UUID | None = None,
    ) -> AgentResponse:
        case = self.session.get(Case, case_id)
        if case is None:
            raise LookupError("case not found")
        message = self.to_message(action_type, target)
        agent = CaseAgent(self.session, actor_id=self.actor_id)
        return agent.handle_message(case_id, message, conversation_id=conversation_id)
