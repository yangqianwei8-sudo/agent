"""Case Agent — natural language orchestration over Workflow + Application."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.agent.action_safety import (
    ActionSafetyGate,
    SafetyVerdict,
    is_cancel_pending,
    looks_like_pending_slot_fill,
)
from backend.agent.clarification import extract_entity_mentions, resolve_unique_focus
from backend.agent.context import CaseContextService
from backend.agent.dto import AgentErrorCode, AgentIntent, AgentResponse, IntentResult
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
        conversation_engine=None,
    ) -> None:
        from backend.llm.factory import (
            build_analyst_engine,
            build_claim_direction_engine,
            build_conversation_engine,
            build_intent_engine,
            build_organizer_engine,
            build_pleading_writer_engine,
        )

        self.session = session
        self.actor_id = actor_id
        self.intent_engine = intent_engine or build_intent_engine()
        self.context_svc = CaseContextService(session)
        self.safety_gate = ActionSafetyGate()
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
            conversation_engine=conversation_engine or build_conversation_engine(),
        )

    def handle_message(
        self,
        case_id: UUID,
        message: str,
        conversation_id: UUID | None = None,
        *,
        current_issue_key: UUID | None = None,
        current_issue_version: int | None = None,
        current_object_type: str | None = None,
        current_object_ref: str | None = None,
    ) -> AgentResponse:
        conversation_id = conversation_id or self._resolve_conversation_id(case_id)
        pending = self._load_pending_action(case_id, conversation_id)
        recent_mentions, recent_focus = self._load_recent_focus(
            case_id, conversation_id
        )
        # Merge current user message mentions for next-turn focus building
        turn_mentions = extract_entity_mentions(message)
        for m in turn_mentions:
            if m not in recent_mentions:
                recent_mentions.append(m)

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
        ctx.focus_issue_key = current_issue_key
        ctx.focus_issue_version = current_issue_version
        ctx.focus_object_type = current_object_type
        ctx.focus_object_ref = current_object_ref
        if ctx.instance is not None:
            user_msg.instance_id = ctx.instance.id
            self.session.flush()

        # Cancel pending action
        if pending and is_cancel_pending(message):
            result = HandlerResult(
                message="好的，已取消该操作。",
                intent=AgentIntent.CASE_CONVERSATION,
                pending_action=None,
                safety_result="CANCELLED",
                routing_status="CONVERSATION",
            )
            return self._finalize(
                case_id=case_id,
                conversation_id=conversation_id,
                ctx=ctx,
                user_msg=user_msg,
                result=result,
                intent_meta={
                    "intent": AgentIntent.CASE_CONVERSATION.value,
                    "cancelled_pending": True,
                },
                recent_focus=recent_focus,
                recent_mentions=recent_mentions,
            )

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
                    routing_status="UNKNOWN",
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

        # Pending slot fill (short answers / names / statements)
        from_pending = False
        if pending and looks_like_pending_slot_fill(pending, message, intent.intent):
            merged = self.safety_gate.merge_pending_with_message(pending, message)
            intent = IntentResult(
                intent=merged.intent,
                targets=list(merged.targets),
                parameters={
                    **dict(merged.arguments),
                    **(
                        {"missing_fields": list(merged.missing_fields)}
                        if merged.missing_fields
                        else {}
                    ),
                },
            )
            from_pending = True

        user_msg.parsed_intent_json = {
            "conversation_id": str(conversation_id),
            "intent": intent.intent.value,
            "targets": intent.targets,
            "parameters": intent.parameters,
            "from_pending": from_pending,
        }
        self.session.flush()

        # Inject raw user message for conversation engine (read-only path)
        if intent.intent == AgentIntent.CASE_CONVERSATION:
            params = dict(intent.parameters or {})
            params["user_message"] = message
            intent = intent.model_copy(update={"parameters": params})

        resolution = self.handler._build_resolution(ctx)  # noqa: SLF001
        resolution["recent_mentions"] = recent_mentions
        resolution["recent_focus"] = recent_focus
        # Role-scoped unique for confirm defendant phrases
        role = (intent.parameters or {}).get("role")
        if role and isinstance(resolution.get("role_unique_parties"), dict):
            resolution["unique_candidate_party"] = resolution[
                "role_unique_parties"
            ].get(role)

        # 4) Execute via handlers → Application / Runtime
        result = self.handler.dispatch(
            ctx=ctx,
            intent=intent,
            raw_message=message,
            resolution=resolution,
        )

        # Preserve pending across clarifying conversation turns (e.g.「哪一条？」)
        if pending and result.pending_action is None:
            from backend.agent.action_safety import MUTATION_INTENTS

            executed = (
                result.routing_status == "READY"
                or result.safety_result == SafetyVerdict.VALID.value
            )
            cancelled = result.safety_result == "CANCELLED"
            if not executed and not cancelled:
                if intent.intent not in MUTATION_INTENTS or from_pending:
                    result.pending_action = pending
                elif result.safety_result in {
                    SafetyVerdict.INCOMPLETE.value,
                    SafetyVerdict.AMBIGUOUS.value,
                }:
                    # mutation incomplete should already set pending; fallback keep
                    result.pending_action = result.pending_action or pending

        # Derive focus for next turn from this turn's citations / targets
        next_focus = recent_focus
        if result.references:
            cite_mentions = []
            for ref in result.references:
                et = str(ref.get("type") or "").upper()
                num = ref.get("display_number")
                if et in {"EVIDENCE", "FACT", "PARTY", "CLAIM"} and num:
                    cite_mentions.append(
                        {"entity_type": et, "display_number": str(num)}
                    )
            uniq = resolve_unique_focus(cite_mentions)
            if uniq:
                next_focus = uniq
        elif intent.targets and intent.intent in {
            AgentIntent.ACCEPT_EVIDENCE,
            AgentIntent.EXCLUDE_EVIDENCE,
            AgentIntent.CONFIRM_FACT,
            AgentIntent.REJECT_FACT,
            AgentIntent.AMEND_FACT,
            AgentIntent.CONFIRM_PARTY,
        }:
            type_map = {
                AgentIntent.ACCEPT_EVIDENCE: "EVIDENCE",
                AgentIntent.EXCLUDE_EVIDENCE: "EVIDENCE",
                AgentIntent.CONFIRM_FACT: "FACT",
                AgentIntent.REJECT_FACT: "FACT",
                AgentIntent.AMEND_FACT: "FACT",
                AgentIntent.CONFIRM_PARTY: "PARTY",
            }
            next_focus = {
                "entity_type": type_map[intent.intent],
                "display_number": str(intent.targets[0]),
            }
        elif turn_mentions:
            uniq = resolve_unique_focus(turn_mentions)
            if uniq:
                next_focus = uniq

        result.recent_focus = next_focus

        return self._finalize(
            case_id=case_id,
            conversation_id=conversation_id,
            ctx=ctx,
            user_msg=user_msg,
            result=result,
            intent_meta={
                "intent": intent.intent.value,
                "targets": intent.targets,
                "parameters": intent.parameters,
                "from_pending": from_pending,
            },
            recent_focus=next_focus,
            recent_mentions=recent_mentions,
        )

    def _finalize(
        self,
        *,
        case_id: UUID,
        conversation_id: UUID,
        ctx,
        user_msg: AgentMessage,
        result: HandlerResult,
        intent_meta: dict[str, Any],
        recent_focus: dict[str, Any] | None = None,
        recent_mentions: list[dict[str, Any]] | None = None,
    ) -> AgentResponse:
        _ = user_msg
        ctx = self.context_svc.load(case_id, conversation_id=conversation_id)
        response = build_agent_response(ctx=ctx, handler=result)

        turn_seq = self._next_turn_seq(case_id, conversation_id)
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
                "safety_result": response.safety_result,
                "missing_fields": response.missing_fields,
                "pending_action": response.pending_action,
                "routing_status": response.routing_status,
                "arguments": (intent_meta.get("parameters") or {}),
                "turn_seq": turn_seq,
                "recent_focus": recent_focus,
                "recent_mentions": recent_mentions or [],
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

    def _next_turn_seq(self, case_id: UUID, conversation_id: UUID) -> int:
        rows = list(
            self.session.scalars(
                select(AgentMessage)
                .where(
                    AgentMessage.case_id == case_id,
                    AgentMessage.role == "AGENT",
                )
                .order_by(AgentMessage.created_at.desc())
                .limit(40)
            )
        )
        best = 0
        for row in rows:
            meta: dict[str, Any] = row.parsed_intent_json or {}
            if str(meta.get("conversation_id") or "") != str(conversation_id):
                continue
            try:
                best = max(best, int(meta.get("turn_seq") or 0))
            except (TypeError, ValueError):
                continue
        return best + 1

    def _load_pending_action(
        self, case_id: UUID, conversation_id: UUID
    ) -> dict[str, Any] | None:
        """Load pending slots from the latest AGENT turn (turn_seq)."""
        meta = self._latest_agent_meta(case_id, conversation_id)
        if not meta:
            return None
        pending = meta.get("pending_action")
        if isinstance(pending, dict) and pending.get("pending_action"):
            return pending
        return None

    def _load_recent_focus(
        self, case_id: UUID, conversation_id: UUID
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        """Recover recent entity mentions / focus from recent turns."""
        rows = list(
            self.session.scalars(
                select(AgentMessage)
                .where(AgentMessage.case_id == case_id)
                .order_by(AgentMessage.created_at.desc())
                .limit(12)
            )
        )
        mentions: list[dict[str, Any]] = []
        focus: dict[str, Any] | None = None
        for row in reversed(rows):
            meta: dict[str, Any] = row.parsed_intent_json or {}
            if str(meta.get("conversation_id") or "") != str(conversation_id):
                continue
            if row.role == "USER":
                mentions.extend(extract_entity_mentions(row.content or ""))
            else:
                stored = meta.get("recent_mentions") or []
                if isinstance(stored, list):
                    mentions.extend(
                        [m for m in stored if isinstance(m, dict)]
                    )
                rf = meta.get("recent_focus")
                if isinstance(rf, dict) and rf.get("display_number"):
                    focus = rf
                # citations in arguments/references not always stored; use focus
        # Keep last few unique mentions (order preserved)
        seen: set[tuple[str, str]] = set()
        uniq: list[dict[str, Any]] = []
        for m in mentions:
            key = (str(m.get("entity_type")), str(m.get("display_number")))
            if key in seen or not key[0] or not key[1]:
                continue
            seen.add(key)
            uniq.append(
                {
                    "entity_type": key[0],
                    "display_number": key[1],
                }
            )
        # Prefer focus from last agent; else unique among recent mentions
        if focus is None:
            focus = resolve_unique_focus(uniq[-3:])
        return uniq[-8:], focus

    def _latest_agent_meta(
        self, case_id: UUID, conversation_id: UUID
    ) -> dict[str, Any] | None:
        rows = list(
            self.session.scalars(
                select(AgentMessage)
                .where(
                    AgentMessage.case_id == case_id,
                    AgentMessage.role == "AGENT",
                )
                .order_by(AgentMessage.created_at.desc())
                .limit(40)
            )
        )
        best_meta: dict[str, Any] | None = None
        best_seq = -1
        for row in rows:
            meta: dict[str, Any] = row.parsed_intent_json or {}
            if str(meta.get("conversation_id") or "") != str(conversation_id):
                continue
            try:
                seq = int(meta.get("turn_seq") or 0)
            except (TypeError, ValueError):
                seq = 0
            if seq >= best_seq:
                best_seq = seq
                best_meta = meta
        return best_meta

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
