"""Case Agent — natural language orchestration over Workflow + Application."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.agent.context import CaseContextService
from backend.agent.dto import AgentErrorCode, AgentIntent, AgentResponse
from backend.agent.handlers import CommandHandler, HandlerResult, build_agent_response
from backend.agent.intent_router import IntentEngine, IntentParseContext
from backend.application.case_analyst import CaseAnalystService
from backend.application.claim_direction import ClaimDirectionService
from backend.application.evidence_organizer import EvidenceOrganizerService
from backend.application.pleading_writer import PleadingWriterService
from backend.llm.errors import LLMError
from backend.models import AgentMessage


class CaseAgent:
    """Stateless per-call agent: all durable state loaded from DB."""

    def __init__(
        self,
        session: Session,
        *,
        actor_id: UUID,
        intent_engine: IntentEngine | None = None,
        organizer: EvidenceOrganizerService | None = None,
        analyst: CaseAnalystService | None = None,
        claim_svc: ClaimDirectionService | None = None,
        writer: PleadingWriterService | None = None,
    ) -> None:
        from backend.llm.factory import (
            build_analyst_engine,
            build_claim_direction_engine,
            build_intent_engine,
            build_organizer_engine,
            build_pleading_writer_engine,
        )

        self.session = session
        self.actor_id = actor_id
        self.intent_engine = intent_engine or build_intent_engine()
        self.context_svc = CaseContextService(session)
        self.handler = CommandHandler(
            session,
            actor_id=actor_id,
            organizer=organizer
            or EvidenceOrganizerService(session, engine=build_organizer_engine()),
            analyst=analyst
            or CaseAnalystService(session, engine=build_analyst_engine()),
            claim_svc=claim_svc
            or ClaimDirectionService(session, engine=build_claim_direction_engine()),
            writer=writer
            or PleadingWriterService(session, engine=build_pleading_writer_engine()),
        )

    def handle_message(
        self,
        case_id: UUID,
        message: str,
        conversation_id: UUID | None = None,
    ) -> AgentResponse:
        conversation_id = conversation_id or self._resolve_conversation_id(case_id)

        # 1) Persist user message
        user_msg = AgentMessage(
            case_id=case_id,
            instance_id=None,
            role="USER",
            content=message,
            parsed_intent_json={"conversation_id": str(conversation_id)},
        )
        self.session.add(user_msg)
        self.session.flush()

        # 2) Context from DB
        ctx = self.context_svc.load(case_id, conversation_id=conversation_id)
        if ctx.instance is not None:
            user_msg.instance_id = ctx.instance.id
            self.session.flush()

        # 3) Intent
        try:
            intent = self.intent_engine.parse(
                message,
                context=IntentParseContext(
                    workflow_status=ctx.workflow_status,
                    current_node=ctx.current_node.code if ctx.current_node else None,
                    waiting_reason=ctx.waiting_reason,
                    pending_human_gate=(ctx.workflow_status == "WAITING_USER"),
                    case_title=ctx.case.title,
                    pending_actions=[a.get("label", "") for a in ctx.available_actions],
                    blocking_reason=None,
                ),
            )
        except LLMError as exc:
            user_msg.parsed_intent_json = {
                "conversation_id": str(conversation_id),
                "intent": AgentIntent.UNKNOWN.value,
                "error_code": exc.code,
            }
            self.session.flush()
            response = build_agent_response(
                ctx=ctx,
                handler=HandlerResult(
                    message=(
                        "本次 AI 意图识别调用失败，案件状态未被自动确认或推进，请重试。"
                        f"（{exc.code}）"
                    ),
                    intent=AgentIntent.UNKNOWN,
                    error_code=AgentErrorCode.LLM_REQUEST_FAILED,
                ),
            )
            agent_msg = AgentMessage(
                case_id=case_id,
                instance_id=ctx.instance.id if ctx.instance else None,
                role="AGENT",
                content=response.message,
                parsed_intent_json={
                    "conversation_id": str(conversation_id),
                    "intent": response.intent.value,
                    "error_code": AgentErrorCode.LLM_REQUEST_FAILED.value,
                },
            )
            self.session.add(agent_msg)
            self.session.flush()
            return response

        user_msg.parsed_intent_json = {
            "conversation_id": str(conversation_id),
            "intent": intent.intent.value,
            "targets": intent.targets,
            "parameters": intent.parameters,
        }
        self.session.flush()

        # 4) Execute via handlers → Application / Runtime
        result = self.handler.dispatch(ctx=ctx, intent=intent)

        # 5) Reload context after mutations
        ctx = self.context_svc.load(case_id, conversation_id=conversation_id)
        response = build_agent_response(ctx=ctx, handler=result)

        # 6) Persist assistant message (final reply only — no chain-of-thought)
        agent_msg = AgentMessage(
            case_id=case_id,
            instance_id=ctx.instance.id if ctx.instance else None,
            role="AGENT",
            content=response.message,
            parsed_intent_json={
                "conversation_id": str(conversation_id),
                "intent": response.intent.value,
                "workflow_status": response.workflow_status,
                "current_node": response.current_node,
                "error_code": response.error_code.value if response.error_code else None,
                "command_id": str(response.command_id) if response.command_id else None,
            },
            related_command_id=response.command_id,
            related_decision_id=result.related_decision_id,
        )
        self.session.add(agent_msg)
        self.session.flush()
        return response

    def status(self, case_id: UUID, conversation_id: UUID | None = None) -> AgentResponse:
        conversation_id = conversation_id or self._resolve_conversation_id(case_id)
        ctx = self.context_svc.load(case_id, conversation_id=conversation_id)
        from backend.agent.handlers import HandlerResult

        result = HandlerResult(
            message=self.handler._status_text(ctx),  # noqa: SLF001
            intent=AgentIntent.STATUS,
        )
        return build_agent_response(ctx=ctx, handler=result)

    def _resolve_conversation_id(self, case_id: UUID) -> UUID:
        """Reuse latest conversation_id for the case, or create a new one."""
        rows = list(
            self.session.scalars(
                select(AgentMessage)
                .where(AgentMessage.case_id == case_id)
                .order_by(AgentMessage.created_at.desc())
                .limit(20)
            )
        )
        for row in rows:
            meta: dict[str, Any] = row.parsed_intent_json or {}
            cid = meta.get("conversation_id")
            if cid:
                try:
                    return UUID(str(cid))
                except ValueError:
                    continue
        return uuid4()
