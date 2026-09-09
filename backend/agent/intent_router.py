"""Intent routing — deterministic V1 (no real LLM)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

from backend.agent.dto import AgentIntent, IntentResult


@dataclass
class IntentParseContext:
    """Optional workflow/case hints for LLM intent routers (ignored by deterministic)."""

    workflow_status: str | None = None
    current_node: str | None = None
    blocking_reason: str | None = None
    waiting_reason: str | None = None
    pending_human_gate: bool = False
    case_title: str | None = None
    pending_actions: list[str] = field(default_factory=list)


class IntentEngine(Protocol):
    def parse(
        self,
        message: str,
        *,
        context: IntentParseContext | None = None,
    ) -> IntentResult: ...


class DeterministicIntentRouter:
    """Keyword / pattern intent router for contract tests and V1."""

    def parse(
        self,
        message: str,
        *,
        context: IntentParseContext | None = None,
    ) -> IntentResult:
        _ = context
        text = message.strip()
        lower = text.lower()

        if _match(lower, (r"^开始(处理)?(这个)?案件", r"^启动工作流", r"start\s*case")):
            return IntentResult(intent=AgentIntent.START_CASE_WORKFLOW)

        if _match(lower, (r"^暂停$", r"^pause$")):
            return IntentResult(intent=AgentIntent.PAUSE)

        if _match(lower, (r"^恢复$", r"^resume$", r"继续这个案件")):
            return IntentResult(intent=AgentIntent.RESUME)

        if _match(lower, (r"^重试$", r"^retry$")):
            return IntentResult(intent=AgentIntent.RETRY)

        if _match(lower, (r"现在(做到哪|什么状态)", r"^状态$", r"^status$", r"查(看)?进度")):
            return IntentResult(intent=AgentIntent.STATUS)

        if m := re.search(r"接受证据\s*([0-9]+(?:\s*[,，、]\s*[0-9]+)*)", text):
            nums = re.findall(r"[0-9]+", m.group(1))
            return IntentResult(
                intent=AgentIntent.ACCEPT_EVIDENCE, targets=nums
            )

        if m := re.search(r"排除证据\s*([0-9]+(?:\s*[,，、]\s*[0-9]+)*)", text):
            nums = re.findall(r"[0-9]+", m.group(1))
            return IntentResult(
                intent=AgentIntent.EXCLUDE_EVIDENCE, targets=nums
            )

        if m := re.search(r"确认事实\s*([0-9]+)", text):
            return IntentResult(intent=AgentIntent.CONFIRM_FACT, targets=[m.group(1)])

        if m := re.search(r"事实\s*([0-9]+)\s*确认", text):
            return IntentResult(intent=AgentIntent.CONFIRM_FACT, targets=[m.group(1)])

        if m := re.search(r"拒绝事实\s*([0-9]+)", text):
            return IntentResult(intent=AgentIntent.REJECT_FACT, targets=[m.group(1)])

        if m := re.search(r"修改事实\s*([0-9]+)\s*(?:为|成)\s*(.+)$", text):
            return IntentResult(
                intent=AgentIntent.AMEND_FACT,
                targets=[m.group(1)],
                parameters={"new_statement": m.group(2).strip()},
            )

        if m := re.search(r"确认被告\s*([0-9]+)", text):
            return IntentResult(
                intent=AgentIntent.CONFIRM_PARTY,
                targets=[m.group(1)],
                parameters={"role": "DEFENDANT"},
            )

        if m := re.search(r"确认原告\s*([0-9]+)", text):
            return IntentResult(
                intent=AgentIntent.CONFIRM_PARTY,
                targets=[m.group(1)],
                parameters={"role": "PLAINTIFF"},
            )

        if m := re.search(r"确认(?:当事人|第三人)\s*([0-9]+)", text):
            return IntentResult(intent=AgentIntent.CONFIRM_PARTY, targets=[m.group(1)])

        if m := re.search(r"拒绝(?:当事人|原告|被告|第三人)\s*([0-9]+)", text):
            return IntentResult(intent=AgentIntent.REJECT_PARTY, targets=[m.group(1)])

        # Party creation — never treat residual fragments as names
        from backend.agent.action_safety import (
            detect_create_party_request,
            detect_incomplete_mutation,
        )

        party_prop = detect_create_party_request(text)
        if party_prop is not None:
            params = dict(party_prop.arguments)
            if party_prop.missing_fields:
                params["missing_fields"] = list(party_prop.missing_fields)
            return IntentResult(
                intent=AgentIntent.CREATE_PARTY,
                parameters=params,
            )

        if m := re.search(r"确认诉讼请求\s*([0-9]+)?", text):
            targets = [m.group(1)] if m.group(1) else []
            return IntentResult(
                intent=AgentIntent.CONFIRM_CLAIM_DIRECTION, targets=targets
            )

        if m := re.search(r"(?:金额改(?:为|成)|改成)\s*([0-9]+(?:\.[0-9]+)?)\s*万?元?", text):
            raw = m.group(1)
            amount = float(raw)
            if "万" in text[m.start() : m.end() + 2]:
                amount *= 10000
            return IntentResult(
                intent=AgentIntent.AMEND_CLAIM_DIRECTION,
                parameters={"amount": amount},
            )

        if _match(lower, (r"批准(这份)?起诉状", r"批准(这份)?草稿", r"^approve\s*draft")):
            return IntentResult(intent=AgentIntent.APPROVE_DRAFT)

        if _match(lower, (r"查看起诉状", r"查看草稿", r"^show\s*draft")):
            return IntentResult(intent=AgentIntent.SHOW_DRAFT)

        if _match(
            lower,
            (
                r"(能不能|能否|可以|可否).{0,8}(生成|写|起草)?起诉状",
                r"(为什么不能|还差什么|还缺什么|还缺哪些).{0,16}(生成|写)?起诉状?",
                r"^为什么不能[？?]?$",
                r"还缺哪些关键",
                r"起诉准备度",
                r"(能不能|能否|可以)起诉",
            ),
        ):
            return IntentResult(
                intent=AgentIntent.CASE_CONVERSATION,
                parameters={"user_message": text},
            )

        if _match(lower, (r"生成起诉状", r"起草起诉状", r"写起诉状", r"帮我生成起诉状")):
            return IntentResult(intent=AgentIntent.GENERATE_COMPLAINT)

        if _match(lower, (r"整理证据", r"运行组织器", r"organize")):
            return IntentResult(intent=AgentIntent.ORGANIZE_EVIDENCE)

        if _match(lower, (r"查看证据", r"^show\s*evidence")):
            return IntentResult(intent=AgentIntent.SHOW_EVIDENCE)

        if _match(lower, (r"查看事实", r"^show\s*facts")):
            return IntentResult(intent=AgentIntent.SHOW_FACTS)

        if _match(lower, (r"查看诉讼请求", r"查看请求", r"^show\s*claims")):
            return IntentResult(intent=AgentIntent.SHOW_CLAIMS)

        if _match(lower, (r"^继续$", r"^continue$", r"继续处理")):
            return IntentResult(intent=AgentIntent.CONTINUE)

        # Incomplete mutation asks — keep ACTION intent, empty slots for Safety Gate
        incomplete = detect_incomplete_mutation(text)
        if incomplete is not None:
            params = dict(incomplete.arguments)
            if incomplete.missing_fields:
                params["missing_fields"] = list(incomplete.missing_fields)
            params["routing_status"] = "INCOMPLETE"
            return IntentResult(
                intent=incomplete.intent,
                targets=list(incomplete.targets),
                parameters=params,
            )

        # Fuzzy short affirmations — NEVER map to confirm/approve
        if _match(lower, (r"^(好|好的|可以|嗯|没问题|看起来行|行|ok|okay|yes)$",)):
            return IntentResult(
                intent=AgentIntent.UNKNOWN, parameters={"reason": "ambiguous"}
            )

        # Free-form case discussion / questions → conversation channel
        if text:
            return IntentResult(intent=AgentIntent.CASE_CONVERSATION)

        return IntentResult(intent=AgentIntent.UNKNOWN)


class ScriptedIntentEngine:
    def __init__(self, result: IntentResult) -> None:
        self._result = result

    def parse(
        self,
        message: str,
        *,
        context: IntentParseContext | None = None,
    ) -> IntentResult:
        _ = message
        _ = context
        return self._result.model_copy(deep=True)


def _match(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(p, text) for p in patterns)
