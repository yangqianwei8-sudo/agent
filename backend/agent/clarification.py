"""Natural-language clarification for incomplete / ambiguous actions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.agent.dto import AgentIntent


@dataclass
class ClarificationContext:
    evidence_numbers: list[str] = field(default_factory=list)
    fact_indices: list[int] = field(default_factory=list)
    party_labels: list[str] = field(default_factory=list)
    claim_count: int = 0
    amend_target: str | None = None
    role_hint: str | None = None


class ActionClarificationRenderer:
    """Render lawyer-facing Chinese prompts for incomplete mutations."""

    ROLE_ZH = {
        "PLAINTIFF": "原告",
        "DEFENDANT": "被告",
        "THIRD_PARTY": "第三人",
    }

    def render(
        self,
        *,
        intent: AgentIntent,
        missing_fields: list[str],
        verdict: str = "INCOMPLETE",
        ctx: ClarificationContext | None = None,
        ambiguous_hint: str | None = None,
    ) -> str:
        ctx = ctx or ClarificationContext()
        if verdict == "AMBIGUOUS":
            return ambiguous_hint or self._ambiguous_default(intent)
        if verdict == "INVALID":
            return ambiguous_hint or "当前无法执行该操作，请换一种明确说法。"

        missing = set(missing_fields or [])
        if intent == AgentIntent.ACCEPT_EVIDENCE:
            msg = "可以。你想接受哪一份证据？"
            if ctx.evidence_numbers:
                listed = "、".join(f"证据{n}" for n in ctx.evidence_numbers[:8])
                msg += f"\n例如：{listed}"
            return msg
        if intent == AgentIntent.EXCLUDE_EVIDENCE:
            msg = "可以。你想排除哪一份证据？"
            if ctx.evidence_numbers:
                listed = "、".join(f"证据{n}" for n in ctx.evidence_numbers[:8])
                msg += f"\n例如：{listed}"
            return msg
        if intent == AgentIntent.CONFIRM_FACT:
            msg = "可以。你想确认哪一条事实？"
            if ctx.fact_indices:
                listed = "、".join(f"事实{i}" for i in ctx.fact_indices[:8])
                msg += f"\n例如：{listed}"
            return msg
        if intent == AgentIntent.REJECT_FACT:
            msg = "可以。你指的是哪一条事实？"
            if ctx.fact_indices:
                listed = "、".join(f"事实{i}" for i in ctx.fact_indices[:8])
                msg += f"\n例如：{listed}"
            return msg
        if intent == AgentIntent.AMEND_FACT:
            if "new_statement" in missing and "target" not in missing:
                t = ctx.amend_target or "该事实"
                return f"可以。你希望把{t}修改成什么内容？"
            if "target" in missing:
                return "可以。请说明要修改哪一条事实，以及修改后的内容。"
            return "可以。请说明要修改的事实编号和新的表述。"
        if intent == AgentIntent.CREATE_PARTY:
            role = ctx.role_hint
            if role == "DEFENDANT" or missing == {"defendant_name"}:
                return "可以。请告诉我被告的具体名称。"
            if role == "PLAINTIFF" or missing == {"plaintiff_name"}:
                return "可以。请告诉我原告的具体名称。"
            if role == "THIRD_PARTY" or missing == {"third_party_name"}:
                return "可以。请告诉我第三人的具体名称。"
            if {"plaintiff_name", "defendant_name"} <= missing:
                return (
                    "可以。请告诉我原告和被告的具体名称。\n\n"
                    "例如：\n"
                    "原告：四川维海科技有限公司\n"
                    "被告：四川富茂置业有限公司"
                )
            return "可以。请告诉我要录入的当事人角色和具体名称。"
        if intent == AgentIntent.CONFIRM_PARTY:
            if ctx.party_labels:
                listed = "、".join(ctx.party_labels[:8])
                return f"可以。你想确认哪一位当事人？\n例如：{listed}"
            return "可以。你想确认哪一位当事人？请说明编号，例如「确认当事人1」。"
        if intent == AgentIntent.REJECT_PARTY:
            return "可以。你想拒绝哪一位当事人？请说明编号。"
        if intent == AgentIntent.CONFIRM_CLAIM_DIRECTION:
            if ctx.claim_count > 1:
                return (
                    f"当前有 {ctx.claim_count} 条诉讼请求候选。"
                    "请说明要确认哪一条，例如「确认诉讼请求1」。"
                )
            return "可以。请确认要操作的诉讼请求编号，例如「确认诉讼请求1」。"
        if intent == AgentIntent.REJECT_CLAIM_DIRECTION:
            return "可以。请说明要拒绝哪一条诉讼请求。"
        return "可以。请补充完成操作所需的具体对象或内容。"

    def _ambiguous_default(self, intent: AgentIntent) -> str:
        if intent in {AgentIntent.ACCEPT_EVIDENCE, AgentIntent.EXCLUDE_EVIDENCE}:
            return "刚才提到多份证据。你想接受/排除哪一份？请说明编号。"
        if intent in {AgentIntent.CONFIRM_FACT, AgentIntent.REJECT_FACT}:
            return "刚才提到多条事实。你想操作哪一条？请说明编号。"
        if intent in {AgentIntent.CONFIRM_PARTY, AgentIntent.REJECT_PARTY}:
            return "当前有多位当事人。你想操作哪一位？请说明编号。"
        return "对象不够明确。请说明具体编号后再执行。"


def extract_entity_mentions(text: str) -> list[dict[str, Any]]:
    """Extract display mentions like 证据3 / 事实1 from text."""
    import re

    out: list[dict[str, Any]] = []
    for m in re.finditer(r"证据\s*([0-9]+)", text or ""):
        out.append({"entity_type": "EVIDENCE", "display_number": m.group(1)})
    for m in re.finditer(r"事实\s*([0-9]+)", text or ""):
        out.append({"entity_type": "FACT", "display_number": m.group(1)})
    for m in re.finditer(r"当事人\s*([0-9]+)", text or ""):
        out.append({"entity_type": "PARTY", "display_number": m.group(1)})
    for m in re.finditer(r"诉讼请求\s*([0-9]+)", text or ""):
        out.append({"entity_type": "CLAIM", "display_number": m.group(1)})
    # de-dupe preserving order
    seen: set[tuple[str, str]] = set()
    uniq: list[dict[str, Any]] = []
    for item in out:
        key = (item["entity_type"], item["display_number"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(item)
    return uniq


def resolve_unique_focus(
    mentions: list[dict[str, Any]],
    *,
    prefer_type: str | None = None,
) -> dict[str, Any] | None:
    filtered = mentions
    if prefer_type:
        filtered = [m for m in mentions if m.get("entity_type") == prefer_type]
    if len(filtered) == 1:
        return dict(filtered[0])
    return None
