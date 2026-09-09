"""Case conversation engines — READ ONLY. Never mutate Domain/Workflow."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.agent.conversation_context import CaseConversationContext
from backend.llm.client import LLMClient
from backend.llm.errors import LLMError, LLMSchemaValidationError
from backend.llm.prompts import CASE_CONVERSATION_PROMPT_VERSION, CASE_CONVERSATION_SYSTEM


@dataclass
class ConversationCitation:
    type: str
    display_number: str
    label: str | None = None


@dataclass
class ConversationAnswer:
    answer: str
    citations: list[ConversationCitation] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)
    missing_information: list[str] = field(default_factory=list)
    suggested_actions: list[str] = field(default_factory=list)
    engine: str = "deterministic"


class CaseConversationEngine(Protocol):
    def reply(
        self,
        *,
        user_message: str,
        context: CaseConversationContext,
    ) -> ConversationAnswer: ...


class _LLMConversationOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    answer: str = Field(min_length=1)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    suggested_actions: list[str] = Field(default_factory=list)


class LLMCaseConversationEngine:
    def __init__(self, client: LLMClient) -> None:
        self.client = client
        self.prompt_version = CASE_CONVERSATION_PROMPT_VERSION

    def reply(
        self,
        *,
        user_message: str,
        context: CaseConversationContext,
    ) -> ConversationAnswer:
        user_prompt = _build_user_prompt(user_message, context)
        try:
            result = self.client.complete_json(
                system_prompt=CASE_CONVERSATION_SYSTEM,
                user_prompt=user_prompt,
                schema_name="case_conversation_v1",
            )
            payload = result.parsed_json
            if not isinstance(payload, dict):
                raise LLMSchemaValidationError("conversation root must be object")
            out = _LLMConversationOut.model_validate(payload)
        except LLMError:
            raise
        except ValidationError as exc:
            raise LLMSchemaValidationError("conversation schema invalid") from exc

        citations = []
        for c in out.citations:
            if not isinstance(c, dict):
                continue
            ctype = str(c.get("type") or "evidence")
            num = str(c.get("display_number") or "").strip()
            if not num:
                continue
            citations.append(
                ConversationCitation(
                    type=ctype,
                    display_number=num,
                    label=(str(c.get("label")) if c.get("label") else None),
                )
            )
        answer = out.answer.strip()
        # Never leak UUIDs to lawyer-facing text
        answer = _strip_uuids(answer)
        return ConversationAnswer(
            answer=answer,
            citations=citations,
            uncertainties=[_strip_uuids(x) for x in out.uncertainties if x],
            missing_information=[
                _strip_uuids(x) for x in out.missing_information if x
            ],
            suggested_actions=[_strip_uuids(x) for x in out.suggested_actions if x],
            engine="llm",
        )


class DeterministicCaseConversationEngine:
    """Heuristic read-only answers for tests / offline mode — no mutations."""

    def reply(
        self,
        *,
        user_message: str,
        context: CaseConversationContext,
    ) -> ConversationAnswer:
        q = user_message.strip()
        citations: list[ConversationCitation] = []
        uncertainties: list[str] = []
        missing: list[str] = []
        suggestions: list[str] = []
        parts: list[str] = []

        pending = context.material_pool.get("pending_count") or 0
        pending_names = context.material_pool.get("pending_filenames") or []
        disclosure = (context.material_pool.get("disclosure") or {}).get("summary")

        if re.search(r"全部材料|看完|所有材料|所有文件", q):
            if pending:
                names = "、".join(pending_names) if pending_names else "若干文件"
                parts.append(
                    f"**没有。** 目前有效案件材料池中有 "
                    f"{context.material_pool.get('usable_count', 0)} 份可读取材料；"
                    f"另有 {pending} 份文件未能读取，因此**未参与**本次分析：{names}。"
                )
            else:
                parts.append(
                    f"当前已上传且可读取的材料共 "
                    f"{context.material_pool.get('usable_count', 0)} 份，"
                    "均已进入有效案件材料池。"
                )

        if re.search(r"讲一下|概述|案情|这个案子", q) and not parts:
            parts.append(self._case_overview(context))

        if re.search(r"风险|最大的问题|问题是什么", q):
            parts.append(self._risks(context, citations, missing, suggestions))

        if re.search(r"为什么.*被告|被告.*为什么|主体|起诉谁|中梁|富茂", q):
            parts.append(self._party_why(context, citations, uncertainties, missing))

        if re.search(r"只起诉|两种方案|哪个.*风险|如果只", q):
            parts.append(self._compare_parties(context, suggestions))

        if re.search(r"服务费|怎么算|30\s*万|封顶|固定总价|第六条|付款条件", q):
            parts.append(
                self._fee_and_clause(context, citations, uncertainties, missing, q)
            )

        if re.search(r"证据\s*([0-9]+).*证明|能证明什么|证据\s*([0-9]+)", q):
            parts.append(self._evidence_proof(context, citations, q))

        if re.search(r"缺什么|还缺|缺口|missing", q):
            parts.append(self._gaps(context, missing, suggestions))

        if re.search(r"被告律师|抗辩|反方", q):
            parts.append(self._defense(context, suggestions))
            uncertainties.append(
                "这是基于现有材料的诉讼策略分析，不是已确认案件事实。"
            )

        if re.search(r"理解错|重新看|纠正", q):
            parts.append(self._reread(context, citations, q))

        if re.search(r"应该没问题|看起来.*吧|是不是基本|差不多就这样", q):
            parts.append(
                "这听起来像在征求意见，但我**不会**据此自动确认任何事实、证据或当事人。"
                "请明确说「确认事实N / 接受证据N」等指令后，我才会写入案件。"
            )

        if re.search(r"履约|交付|完成服务", q):
            parts.append(self._performance(context, missing, citations))

        if not parts:
            parts.append(self._generic(context, missing, suggestions))

        if pending and disclosure and not any("未能读取" in p for p in parts):
            parts.append(f"\n注意：{disclosure}")

        answer = "\n\n".join(p for p in parts if p).strip()
        answer = _strip_uuids(answer)
        return ConversationAnswer(
            answer=answer,
            citations=citations,
            uncertainties=uncertainties,
            missing_information=missing,
            suggested_actions=suggestions,
            engine="deterministic",
        )

    def _case_overview(self, ctx: CaseConversationContext) -> str:
        lines = [f"## 案件概述：{ctx.case_title}"]
        if ctx.parties:
            lines.append("### 当事人")
            for p in ctx.parties:
                lines.append(
                    f"- 【当事人{p['display_number']}】{p['role_label']} "
                    f"{p['name']}（{p['layer_note']}）"
                )
        conf_facts = [f for f in ctx.facts if f["status"] == "CONFIRMED"]
        cand_facts = [f for f in ctx.facts if f["status"] == "CANDIDATE"]
        if conf_facts:
            lines.append("### 目前已确认的事实")
            for f in conf_facts[:6]:
                lines.append(f"- 【事实{f['display_number']}】{f['statement']}")
        if cand_facts:
            lines.append("### AI 事实候选（尚未由律师确认）")
            for f in cand_facts[:6]:
                lines.append(f"- 【事实{f['display_number']}】{f['statement']}")
        if not conf_facts and not cand_facts:
            lines.append("目前还没有整理出的案件事实。")
        return "\n".join(lines)

    def _risks(
        self,
        ctx: CaseConversationContext,
        citations: list[ConversationCitation],
        missing: list[str],
        suggestions: list[str],
    ) -> str:
        risks: list[str] = []
        plaintiffs = [p for p in ctx.parties if p["role"] == "PLAINTIFF"]
        defendants = [p for p in ctx.parties if p["role"] == "DEFENDANT"]
        # Subject mismatch heuristic from excerpts / facts
        blob = " ".join(
            [e.quote for e in ctx.source_excerpts]
            + [f["statement"] for f in ctx.facts]
            + [e.get("summary") or "" for e in ctx.evidence]
        )
        for d in defendants:
            if d["name"] and d["name"] not in blob and blob:
                risks.append(
                    f"合同/材料表述中的主体与当前被告「{d['name']}」可能不一致，"
                    "直接起诉存在主体不适格风险。"
                )
                if d["layer"] == "CONFIRMED":
                    citations.append(
                        ConversationCitation(
                            type="party",
                            display_number=d["display_number"],
                            label=d["name"],
                        )
                    )
        for c in ctx.conflicts:
            risks.append(f"证据/事实冲突：{c.get('description') or c}")
        for m in ctx.missing_evidence:
            desc = m.get("description") if isinstance(m, dict) else str(m)
            if desc:
                missing.append(str(desc))
                risks.append(f"证据缺口：{desc}")
        if ctx.material_pool.get("pending_count"):
            risks.append(
                f"另有 {ctx.material_pool['pending_count']} 份文件未成功读取，"
                "可能影响事实与金额判断。"
            )
        if not risks:
            risks.append(
                "基于当前已确认信息，尚未看到压倒性单一风险点；"
                "但仍需继续核验主体、履约与金额依据。"
            )
            suggestions.append("建议继续确认关键事实与证据")
        _ = plaintiffs
        return "## 当前主要风险\n" + "\n".join(f"{i}. {r}" for i, r in enumerate(risks, 1))

    def _party_why(
        self,
        ctx: CaseConversationContext,
        citations: list[ConversationCitation],
        uncertainties: list[str],
        missing: list[str],
    ) -> str:
        defendants = [p for p in ctx.parties if p["role"] == "DEFENDANT"]
        lines = ["## 关于被告主体"]
        if not defendants:
            lines.append("目前案件中还没有登记被告当事人。")
            missing.append("被告当事人尚未登记/确认")
            return "\n".join(lines)
        for d in defendants:
            lines.append(
                f"- 当前被告：【当事人{d['display_number']}】{d['name']}"
                f"（{d['layer_note']}）"
            )
            citations.append(
                ConversationCitation(
                    type="party", display_number=d["display_number"], label=d["name"]
                )
            )
        # Search contract party names in excerpts/facts
        contract_names = set()
        for ex in ctx.source_excerpts:
            for m in re.findall(
                r"([\u4e00-\u9fff]{2,}(?:有限公司|公司|置业|地产))", ex.quote
            ):
                contract_names.add(m)
        for f in ctx.facts:
            for m in re.findall(
                r"([\u4e00-\u9fff]{2,}(?:有限公司|公司|置业|地产))", f["statement"]
            ):
                contract_names.add(m)
        def_names = {d["name"] for d in defendants}
        extra = [n for n in contract_names if n not in def_names]
        if extra:
            lines.append(
                "\n**当前存在主体不一致风险：**\n"
                f"- 材料/事实中出现的主体：{'、'.join(sorted(extra))}\n"
                f"- 当前被告：{'、'.join(sorted(def_names))}\n"
                "我目前没有找到足够的**已确认事实**证明非签约主体必然承担该合同付款义务。"
                "因此直接以当前被告作为合同付款义务被告，需要额外责任桥梁证据。"
            )
            uncertainties.append(
                "主体责任桥梁尚不充分，以上为分析判断，尚未由律师确认。"
            )
            missing.append("证明被告承担合同付款义务的责任桥梁证据/事实")
        else:
            lines.append(
                "就目前上下文，尚未自动检测到明显的「合同主体 vs 被告」名称冲突；"
                "仍建议人工核对签约主体。"
            )
        return "\n".join(lines)

    def _compare_parties(
        self, ctx: CaseConversationContext, suggestions: list[str]
    ) -> str:
        defendants = [p for p in ctx.parties if p["role"] == "DEFENDANT"]
        suggestions.append("建议明确最终被告方案并补充主体责任依据")
        return (
            "## 诉讼主体方案比较（策略分析，非已确认事实）\n"
            "1. **起诉合同签约甲方**：通常更贴近合同相对性，主体适格风险相对更低，"
            "但仍需证明履约与应付款金额。\n"
            "2. **起诉当前登记被告**"
            + (
                f"（{defendants[0]['name']}）"
                if defendants
                else ""
            )
            + "：若缺乏责任桥梁，主体不适格抗辩风险更高。\n"
            "3. **同时起诉两者**：可能扩大求偿面，但需分别证明各被告责任基础，"
            "否则易被法院要求释明或承担举证不利。\n"
            "就「哪个风险更小」：在缺少责任桥梁时，"
            "**仅起诉合同甲方**通常主体风险更小；这是诉讼策略分析，不是已确认案件事实。"
        )

    def _fee_and_clause(
        self,
        ctx: CaseConversationContext,
        citations: list[ConversationCitation],
        uncertainties: list[str],
        missing: list[str],
        q: str,
    ) -> str:
        lines = ["## 服务费 / 合同条款"]
        hit = False
        for ex in ctx.source_excerpts:
            quote = ex.quote
            if re.search(r"服务费|8%|300,?000|三十万|封顶|第六条|支付", quote):
                hit = True
                lines.append(
                    f"依据 {ex.display_evidence or '材料原文'} 摘录：\n> {quote}"
                )
                if ex.display_evidence and ex.display_evidence.startswith("证据"):
                    citations.append(
                        ConversationCitation(
                            type="evidence",
                            display_number=ex.display_evidence.replace("证据", ""),
                        )
                    )
        for f in ctx.facts:
            if re.search(r"服务费|8%|300000|三十万|封顶", f["statement"]):
                hit = True
                tag = "目前已确认" if f["status"] == "CONFIRMED" else "目前材料提示（尚未确认）"
                lines.append(
                    f"- {tag}【事实{f['display_number']}】{f['statement']}"
                )
                citations.append(
                    ConversationCitation(
                        type="fact", display_number=f["display_number"]
                    )
                )
        if re.search(r"封顶|固定总价|30", q):
            if hit:
                lines.append(
                    "\n因此，若条款写明按比例计算并**封顶**30万元，"
                    "则 30 万元更准确的理解是**封顶金额**，不是当然固定总价。"
                    "最终应付金额仍取决于优化总成本等基础事实是否已确认。"
                )
            uncertainties.append("最终应付金额可能仍取决于优化总成本等缺失信息")
            missing.append("最终优化总成本金额的确认材料")
        if re.search(r"付款条件", q) and not any(
            "付款" in ex.quote for ex in ctx.source_excerpts
        ):
            missing.append("明确付款条件/付款节点的条款或凭证")
            lines.append("现有已提供摘录中，付款条件信息可能不完整。")
        if not hit:
            lines.append(
                "我目前的已确认事实/摘录不足以完整回答该问题。"
                "我可以在有 SourceSpan 原文时再分析；请确认相关证据已被整理并接受。"
            )
            missing.append("合同计费条款的成功解析原文")
        return "\n".join(lines)

    def _evidence_proof(
        self,
        ctx: CaseConversationContext,
        citations: list[ConversationCitation],
        q: str,
    ) -> str:
        m = re.search(r"证据\s*([0-9]+)", q)
        if not m:
            return "请指定证据编号，例如「证据2能证明什么？」"
        num = m.group(1)
        item = next((e for e in ctx.evidence if str(e["display_number"]) == num), None)
        if item is None:
            return f"未找到【证据{num}】。"
        citations.append(
            ConversationCitation(type="evidence", display_number=num, label=item["title"])
        )
        lines = [
            f"## 【证据{num}】{item['title']}",
            f"- 状态：{item['acceptance_note']}（version={item['version']}）",
            f"- 摘要：{item.get('summary') or '（无摘要）'}",
        ]
        if item.get("source_quote_previews"):
            lines.append("- 原文摘录：")
            for qtext in item["source_quote_previews"]:
                lines.append(f"  > {qtext}")
        for ex in ctx.source_excerpts:
            if ex.display_evidence == f"证据{num}":
                lines.append(f"> {ex.quote}")
        lines.append(
            "以上证明内容取决于摘录本身；是否足以支持某事实，仍需结合全案其他证据判断。"
        )
        return "\n".join(lines)

    def _gaps(
        self,
        ctx: CaseConversationContext,
        missing: list[str],
        suggestions: list[str],
    ) -> str:
        gaps: list[str] = []
        for m in ctx.missing_evidence:
            desc = m.get("description") if isinstance(m, dict) else str(m)
            if desc:
                gaps.append(str(desc))
        if ctx.material_pool.get("pending_count"):
            gaps.append(
                f"待处理未读取文件 {ctx.material_pool['pending_count']} 份："
                + "、".join(ctx.material_pool.get("pending_filenames") or [])
            )
        # Heuristic gaps
        defendants = [p for p in ctx.parties if p["role"] == "DEFENDANT"]
        if defendants:
            gaps.append("核实被告与合同签约主体是否一致及责任桥梁")
        gaps.append("履约/成果交付证据是否充分")
        gaps.append("最终应付金额与已付款/未付款是否有确认材料")
        # unique preserve order
        seen: set[str] = set()
        uniq = []
        for g in gaps:
            if g not in seen:
                seen.add(g)
                uniq.append(g)
        missing.extend(uniq[:6])
        suggestions.append("建议按缺口逐项补证并确认事实")
        return "## 目前关键案件缺口\n" + "\n".join(
            f"{i}. {g}" for i, g in enumerate(uniq[:8], 1)
        )

    def _defense(
        self, ctx: CaseConversationContext, suggestions: list[str]
    ) -> str:
        suggestions.append("建议针对可能抗辩提前补强举证")
        return (
            "## 若我是被告律师，可能的抗辩方向\n"
            "（这是基于现有材料的诉讼策略分析，不是已确认案件事实。）\n"
            "1. **主体不适格**：主张被告并非合同相对方，或缺乏责任承担依据。\n"
            "2. **履约未完成/未验收**：质疑原告未证明服务成果交付与质量。\n"
            "3. **付款条件未成就**：主张付款节点未触发。\n"
            "4. **金额不确定**：主张比例计费基数不明，30万仅为封顶而非确定债务。\n"
            "5. **已付款或债务抵销**：若存在付款凭证冲突则扩大争议。\n"
        )

    def _reread(
        self,
        ctx: CaseConversationContext,
        citations: list[ConversationCitation],
        q: str,
    ) -> str:
        lines = [
            "你指出需要重新核对原文。以下是我根据当前可用 SourceSpan 重新阅读的结果："
        ]
        found = False
        for ex in ctx.source_excerpts:
            found = True
            lines.append(f"- {ex.display_evidence or '材料'}：> {ex.quote}")
            if ex.display_evidence and ex.display_evidence.startswith("证据"):
                citations.append(
                    ConversationCitation(
                        type="evidence",
                        display_number=ex.display_evidence.replace("证据", ""),
                    )
                )
        if not found:
            lines.append(
                "我目前没有足够的成功解析原文摘录来重新核验你指出的条款。"
            )
        else:
            lines.append(
                "如果此前表述与原文不一致，以本次原文摘录为准。"
                "当前已确认事实不会因本次对话自动修改；"
                "如需修改，请明确提出修改建议并确认后我才能写入案件。"
            )
        _ = q
        return "\n".join(lines)

    def _performance(
        self,
        ctx: CaseConversationContext,
        missing: list[str],
        citations: list[ConversationCitation],
    ) -> str:
        hits = [
            f
            for f in ctx.facts
            if re.search(r"交付|履约|验收|完成|成果", f["statement"])
        ]
        if not hits:
            missing.append("服务成果交付/验收证据")
            return (
                "就目前上下文，**现有材料不足以判断**原告已完成全部合同义务。"
                "建议补充交付、验收或成果确认材料。"
            )
        lines = ["关于履约证明："]
        for f in hits:
            tag = "已确认" if f["status"] == "CONFIRMED" else "材料提示（未确认）"
            lines.append(f"- {tag}【事实{f['display_number']}】{f['statement']}")
            citations.append(
                ConversationCitation(type="fact", display_number=f["display_number"])
            )
        return "\n".join(lines)

    def _generic(
        self,
        ctx: CaseConversationContext,
        missing: list[str],
        suggestions: list[str],
    ) -> str:
        suggestions.append("可以直接问风险、证据、事实或诉讼策略；明确操作请用确认/接受指令")
        return (
            f"我已加载案件「{ctx.case_title}」的只读上下文"
            f"（当事人 {len(ctx.parties)}、证据 {len(ctx.evidence)}、"
            f"事实 {len(ctx.facts)}）。\n"
            "请继续追问具体问题，例如风险、被告主体、合同条款、证据证明力或缺口。"
            "我不会因为聊天而修改案件状态。"
        )


def _build_user_prompt(message: str, context: CaseConversationContext) -> str:
    payload = context.to_prompt_dict()
    # Bound prompt size
    raw = json.dumps(payload, ensure_ascii=False, default=str)
    if len(raw) > 24000:
        raw = raw[:24000] + "…(truncated)"
    return (
        f"律师问题：{message}\n\n"
        f"案件只读上下文 JSON：\n{raw}\n\n"
        "请输出 conversation JSON。记住：不得要求或暗示系统已自动确认任何内容。"
    )


_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def _strip_uuids(text: str) -> str:
    return _UUID_RE.sub("[已隐藏内部编号]", text)
