"""LLM Intent Router — implements IntentEngine; TargetResolver still resolves IDs."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.agent.dto import AgentIntent, IntentResult
from backend.agent.intent_router import IntentParseContext
from backend.llm.client import LLMClient
from backend.llm.errors import LLMError, LLMSchemaValidationError
from backend.llm.prompts import INTENT_ROUTER_SYSTEM, PROMPT_VERSION

_AMBIGUOUS = re.compile(
    r"^(好|好的|可以|嗯|没问题|看起来行|行|ok|okay|yes)$",
    re.IGNORECASE,
)

_HIGH_RISK = frozenset(
    {
        AgentIntent.ACCEPT_EVIDENCE,
        AgentIntent.EXCLUDE_EVIDENCE,
        AgentIntent.CONFIRM_PARTY,
        AgentIntent.AMEND_PARTY,
        AgentIntent.CONFIRM_FACT,
        AgentIntent.REJECT_FACT,
        AgentIntent.AMEND_FACT,
        AgentIntent.CONFIRM_CLAIM_DIRECTION,
        AgentIntent.REJECT_CLAIM_DIRECTION,
        AgentIntent.AMEND_CLAIM_DIRECTION,
        AgentIntent.APPROVE_DRAFT,
    }
)

_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


class _IntentLLMOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    intent: str
    target_text: str = ""
    arguments: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 1.0
    reason: str = ""


class LLMIntentRouter:
    """Real LLM intent classification. Never resolves DB UUIDs itself."""

    def __init__(self, client: LLMClient) -> None:
        self.client = client
        self.prompt_version = PROMPT_VERSION

    def parse(
        self,
        message: str,
        *,
        context: IntentParseContext | None = None,
    ) -> IntentResult:
        ctx = context or IntentParseContext()
        text = message.strip()

        # Hard safety before/after LLM: ambiguous affirmations never confirm
        if _AMBIGUOUS.match(text) and (
            ctx.pending_human_gate
            or (ctx.workflow_status or "") == "WAITING_USER"
            or bool(ctx.waiting_reason)
        ):
            return IntentResult(
                intent=AgentIntent.UNKNOWN,
                parameters={"reason": "ambiguous_affirmation_at_human_gate"},
            )

        user_prompt = _build_user_prompt(text, ctx)
        try:
            result = self.client.complete_json(
                system_prompt=INTENT_ROUTER_SYSTEM,
                user_prompt=user_prompt,
                schema_name="intent_router_v1",
            )
            payload = result.parsed_json
            if not isinstance(payload, dict):
                raise LLMSchemaValidationError("intent root must be object")
            out = _IntentLLMOut.model_validate(payload)
        except LLMError:
            raise
        except ValidationError as exc:
            raise LLMSchemaValidationError("intent schema invalid") from exc

        try:
            intent = AgentIntent(out.intent)
        except ValueError:
            return IntentResult(
                intent=AgentIntent.UNKNOWN,
                parameters={"reason": "invalid_intent_enum", "raw": out.intent[:80]},
            )

        # Strip any UUIDs from LLM — TargetResolver owns resolution
        target_text = _strip_uuids(out.target_text or "")
        args = {
            k: v
            for k, v in (out.arguments or {}).items()
            if k not in {"evidence_item_id", "fact_id", "party_id", "draft_id", "id"}
            and not (isinstance(v, str) and _UUID_RE.fullmatch(v))
        }

        if intent in _HIGH_RISK and _AMBIGUOUS.match(text):
            return IntentResult(
                intent=AgentIntent.UNKNOWN,
                parameters={"reason": "ambiguous_cannot_confirm"},
            )

        targets = _targets_from_text(target_text, intent, text)
        if intent in _HIGH_RISK and intent not in {
            AgentIntent.APPROVE_DRAFT,
            AgentIntent.CONFIRM_CLAIM_DIRECTION,
            AgentIntent.REJECT_CLAIM_DIRECTION,
            AgentIntent.AMEND_CLAIM_DIRECTION,
        }:
            if not targets and intent != AgentIntent.APPROVE_DRAFT:
                # require explicit target for evidence/fact/party
                if intent in {
                    AgentIntent.ACCEPT_EVIDENCE,
                    AgentIntent.EXCLUDE_EVIDENCE,
                    AgentIntent.CONFIRM_FACT,
                    AgentIntent.REJECT_FACT,
                    AgentIntent.AMEND_FACT,
                    AgentIntent.CONFIRM_PARTY,
                    AgentIntent.AMEND_PARTY,
                }:
                    fallback = _deterministic_fallback(text, ctx)
                    if fallback is not None:
                        return fallback
                    return IntentResult(
                        intent=AgentIntent.UNKNOWN,
                        parameters={
                            "reason": "missing_explicit_target",
                            "target_text": target_text,
                        },
                    )

        if out.reason:
            args.setdefault("llm_reason", out.reason[:200])
        args.setdefault("prompt_version", PROMPT_VERSION)
        args.setdefault("confidence", out.confidence)
        if target_text:
            args.setdefault("target_text", target_text)

        # Never pass through UUID targets from LLM
        safe_targets = [t for t in targets if not _UUID_RE.fullmatch(t)]
        result = IntentResult(intent=intent, targets=safe_targets, parameters=args)

        # Explicit numbered confirms: if LLM is fuzzy/misses target, fall back to
        # deterministic parse (never for ambiguous affirmations).
        if result.intent == AgentIntent.UNKNOWN or (
            result.intent in _HIGH_RISK
            and not result.targets
            and result.intent not in {AgentIntent.APPROVE_DRAFT}
        ):
            fallback = _deterministic_fallback(text, ctx)
            if fallback is not None:
                return fallback
        return result


def _deterministic_fallback(
    text: str, ctx: IntentParseContext
) -> IntentResult | None:
    if _AMBIGUOUS.match(text):
        return None
    from backend.agent.intent_router import DeterministicIntentRouter

    det = DeterministicIntentRouter().parse(text, context=ctx)
    if det.intent == AgentIntent.UNKNOWN:
        return None
    if (
        det.intent in _HIGH_RISK
        and not det.targets
        and det.intent not in {AgentIntent.APPROVE_DRAFT}
    ):
        return None
    return det


def _build_user_prompt(message: str, ctx: IntentParseContext) -> str:
    return (
        f"用户消息：{message}\n"
        f"案件标题：{ctx.case_title or ''}\n"
        f"workflow_status：{ctx.workflow_status or ''}\n"
        f"current_node：{ctx.current_node or ''}\n"
        f"waiting_reason：{ctx.waiting_reason or ''}\n"
        f"blocking_reason：{ctx.blocking_reason or ''}\n"
        f"pending_human_gate：{ctx.pending_human_gate}\n"
        f"pending_actions：{', '.join(ctx.pending_actions) if ctx.pending_actions else ''}\n"
        "请输出 JSON。"
    )


def _strip_uuids(text: str) -> str:
    return _UUID_RE.sub("", text).strip()


def _targets_from_text(
    target_text: str, intent: AgentIntent, raw_message: str
) -> list[str]:
    blob = f"{target_text} {raw_message}"
    # Prefer explicit numbered targets
    if intent in {AgentIntent.ACCEPT_EVIDENCE, AgentIntent.EXCLUDE_EVIDENCE}:
        m = re.search(r"证据\s*([0-9]+(?:\s*[,，、]\s*[0-9]+)*)", blob)
        if m:
            return re.findall(r"[0-9]+", m.group(1))
        nums = re.findall(r"[0-9]+", target_text)
        return nums
    if intent in {
        AgentIntent.CONFIRM_FACT,
        AgentIntent.REJECT_FACT,
        AgentIntent.AMEND_FACT,
    }:
        m = re.search(r"事实\s*([0-9]+)", blob)
        if m:
            return [m.group(1)]
        nums = re.findall(r"[0-9]+", target_text)
        return nums[:1]
    if intent in {AgentIntent.CONFIRM_PARTY, AgentIntent.AMEND_PARTY}:
        m = re.search(r"当事人\s*([0-9]+)", blob)
        if m:
            return [m.group(1)]
        nums = re.findall(r"[0-9]+", target_text)
        return nums[:1]
    if intent == AgentIntent.CONFIRM_CLAIM_DIRECTION:
        m = re.search(r"诉讼请求\s*([0-9]+)", blob)
        if m:
            return [m.group(1)]
    return []
