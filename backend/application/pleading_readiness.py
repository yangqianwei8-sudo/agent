"""Pleading Readiness Gate V1 — deterministic Application rules.

READ-ONLY evaluation (no domain mutation). READY/NOT_READY is decided only by
Application hard rules; optional LLM may explain gaps elsewhere, never decide status.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.application.claim_view import ClaimViewService
from backend.application.issue_matrix import IssueMatrixService
from backend.application.material_usability import MaterialUsabilityPolicy
from backend.domain.enums import ClaimType, PartyRole
from backend.models import (
    CaseMaterial,
    CaseParty,
    ClaimDirection,
    EvidenceItem,
    ExtractedContent,
    Fact,
    FactEvidenceLink,
    SourceSpan,
    WorkflowInstance,
)
from backend.schemas.pleading_readiness import (
    PleadingNotReadyError,
    PleadingReadinessResult,
    ReadinessIssue,
    ReadinessIssueCode,
    ReadinessStatus,
)

# --- keyword banks (V1 heuristic; Application-owned, not LLM) ---

_LIABILITY_BRIDGE_KW = (
    "债务加入",
    "债务承担",
    "权利义务承继",
    "承继",
    "概括承受",
    "实际履行主体",
    "实际付款义务",
    "付款义务承担",
    "保证",
    "担保",
    "关联公司责任",
    "授权",
    "代理",
    "代为支付",
    "同意承担",
    "确认承担付款",
    "共同债务人",
)

_PERFORMANCE_STRONG_KW = (
    "已交付",
    "已提交",
    "已完成",
    "已履约",
    "实际履行",
    "成果已",
    "提交了",
    "交付了",
    "签收",
    "验收通过",
    "对方接收",
    "已接收",
    "项目采用",
    "阶段成果完成",
    "确认完成",
    "已按约完成",
    "已经完成",
)

_PERFORMANCE_WEAK_KW = ("交付", "提交")

_PERFORMANCE_NEG = (
    "应当提交",
    "应提交",
    "约定提交",
    "计划交付",
    "应于",
    "服务范围",
    "工作内容包括",
    "工作范围包括",
    "合同约定",
)

_PAYMENT_CONDITION_KW = (
    "付款条件已成就",
    "付款条件成就",
    "验收后应付款",
    "确认后应支付",
    "结算完成",
    "已催告",
    "催告付款",
    "催告",
    "应予支付",
    "应支付",
    "付款义务已产生",
    "条件成就",
    "结清余款",
)

_PAYMENT_TERM_HINT = (
    "成果提交后付款",
    "成果交付后",
    "交付后结清",
    "确认后付款",
    "结算后付款",
    "开票后付款",
    "阶段付款",
    "付款条件",
    "支付条件",
    "付款方式",
    "付款安排",
)

_AMOUNT_CHAIN_KW = (
    "优化金额",
    "优化造价",
    "服务费",
    "计算",
    "费率",
    "百分比",
    "封顶",
    "应付",
    "未付",
    "余额",
    "已付",
    "已付款",
    "结算金额",
    "欠付",
)

_DEBT_DUE_KW = (
    "已到期",
    "债务到期",
    "付款期限届满",
    "催告后仍未",
    "应于",
    "到期日",
    "逾期",
    "迟延履行",
)

_JURISDICTION_KW = (
    "管辖",
    "人民法院",
    "被告住所地",
    "合同履行地",
    "约定管辖",
)

_CONTRACT_PARTY_PATTERNS = (
    re.compile(r"甲方[：:\s]*([^\s，,；;。]{2,40})"),
    re.compile(r"合同签约[方甲乙]*[：:\s]*([^\s，,；;。]{2,40})"),
    re.compile(r"签约义务方[：:\s]*([^\s，,；;。]{2,40})"),
    re.compile(r"合同相对方[：:\s]*([^\s，,；;。]{2,40})"),
)

_MONEY_RE = re.compile(
    r"(?:人民币)?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:万元|元|万)?"
)


class PleadingReadinessService:
    """Deterministic pleading readiness evaluator (read-only)."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def evaluate(self, case_id: UUID) -> PleadingReadinessResult:
        checked_at = datetime.now(UTC)
        blockers: list[ReadinessIssue] = []
        warnings: list[ReadinessIssue] = []
        strengths: list[str] = []
        missing: list[str] = []
        next_actions: list[str] = []

        plaintiffs = self._confirmed_parties(case_id, PartyRole.PLAINTIFF)
        defendants = self._confirmed_parties(case_id, PartyRole.DEFENDANT)
        facts = self._confirmed_facts(case_id)
        claim = self._confirmed_claim(case_id)

        party_refs = [
            {"party_key": str(p.party_key), "role": p.role, "name": p.name}
            for p in [*plaintiffs, *defendants]
        ]
        fact_refs = [
            {"fact_key": str(f.fact_key), "fact_version": f.version, "statement": f.statement}
            for f in facts
        ]
        claim_ref: dict[str, Any] = {}
        if claim is not None:
            claim_ref = {
                "claim_direction_key": str(claim.claim_direction_key),
                "claim_direction_version": claim.version,
            }

        # Gate 1 — plaintiff
        if not plaintiffs:
            blockers.append(
                self._blocker(
                    ReadinessIssueCode.PLAINTIFF_NOT_CONFIRMED,
                    "尚未确认原告主体，无法生成起诉状。",
                    suggested_action="请先确认至少一名原告当事人。",
                )
            )
            missing.append("CONFIRMED plaintiff")
        else:
            strengths.append(f"已确认原告：{', '.join(p.name for p in plaintiffs)}")

        # Gate 2 — defendant
        if not defendants:
            blockers.append(
                self._blocker(
                    ReadinessIssueCode.DEFENDANT_NOT_CONFIRMED,
                    "尚未确认被告主体，无法生成起诉状。",
                    suggested_action="请先确认至少一名被告当事人。",
                )
            )
            missing.append("CONFIRMED defendant")
        else:
            strengths.append(f"已确认被告：{', '.join(p.name for p in defendants)}")

        # ClaimDirection required for payment-type gates
        has_payment_claim = False
        claim_amount: float | None = None
        if claim is None:
            blockers.append(
                self._blocker(
                    ReadinessIssueCode.CLAIM_DIRECTION_NOT_CONFIRMED,
                    "尚未确认诉讼请求方向（ClaimDirection），无法生成起诉状。",
                    suggested_action="请先确认 ClaimDirection。",
                )
            )
            missing.append("CONFIRMED ClaimDirection")
        else:
            strengths.append("已确认 ClaimDirection")
            payload = claim.payload or {}
            for item in payload.get("claims") or []:
                ctype = str(item.get("claim_type") or "")
                if ctype in {ClaimType.PAYMENT.value, ClaimType.LIQUIDATED_DAMAGES.value}:
                    has_payment_claim = True
                    if item.get("amount") is not None:
                        claim_amount = float(item["amount"])

        # Evidence provenance map for confirmed facts
        provenance_ok: dict[UUID, bool] = {}
        for f in facts:
            provenance_ok[f.id] = self._fact_has_valid_provenance(f)

        # Gate 3 — liability bridge
        if defendants:
            liab = self._check_liability_bridge(defendants, facts, provenance_ok)
            if liab is not None:
                blockers.append(liab)
                missing.append("被告责任桥梁（合同主体≠被告时）")
                next_actions.append("补充并确认被告责任依据事实（债务加入/承继/实际履行义务方等）")
            else:
                strengths.append("被告责任基础可解释（合同主体一致或已确认责任桥梁）")

        # Gate 4 — performance (payment / liquidated-damages claims)
        if has_payment_claim:
            perf = self._check_performance(facts, provenance_ok)
            if perf is not None:
                blockers.append(perf)
                missing.append("已确认的实际履约事实")
                next_actions.append("补充并确认实际交付/验收/履约事实及证据")
            else:
                strengths.append("已存在可支持履约的已确认事实")

        # Gate 5 — payment condition
        if has_payment_claim:
            pay = self._check_payment_condition(facts, provenance_ok)
            if pay is not None:
                blockers.append(pay)
                missing.append("付款条件成就或付款条件明确性")
                next_actions.append("确认付款条件内容，并确认条件是否已成就")
            else:
                strengths.append("付款条件相关事实可支持请求")

        # Gate 6 — amount chain
        if has_payment_claim:
            amt = self._check_amount_chain(facts, provenance_ok, claim_amount)
            if amt is not None:
                blockers.append(amt)
                missing.append("金额计算闭环（基数/费率/已付/余额）")
                next_actions.append("补充并确认服务费计算基数、费率、已付款与未付余额")
            else:
                strengths.append("金额请求可由已确认事实支撑")

        # Gate 7 — debt due
        if has_payment_claim:
            due = self._check_debt_due(facts, provenance_ok)
            if due is not None:
                blockers.append(due)
                missing.append("债务到期/可请求状态")
                next_actions.append("确认付款到期日、催告或条件成就日")
            else:
                strengths.append("债务到期状态可由已确认事实支撑")

        # Gate 8 — jurisdiction (warning unless court name required — V1 warning)
        jur = self._check_jurisdiction(facts)
        if jur is not None:
            warnings.append(jur)

        # Conflict gate
        conflict = self._check_amount_conflicts(case_id, has_payment_claim)
        if conflict is not None:
            blockers.append(conflict)
            missing.append("影响金额的关键事实冲突未解决")
            next_actions.append("解决已付款/金额相关冲突后再生成起诉状")

        # Key fact provenance for facts that passed thematic gates
        key_bad = self._key_facts_missing_provenance(facts, provenance_ok, has_payment_claim)
        if key_bad:
            blockers.append(
                self._blocker(
                    ReadinessIssueCode.KEY_FACT_PROVENANCE_INVALID,
                    "关键事实缺少合法证据溯源"
                    "（Fact→Link→ACCEPTED Evidence→SourceSpan→SUCCEEDED EC）。",
                    suggested_action="为关键事实绑定 ACCEPTED 证据与 SourceSpan。",
                    related_fact_keys=[str(f.fact_key) for f in key_bad],
                )
            )
            missing.append("关键事实证据 provenance")

        # Pending materials — warning
        pending_warn = self._pending_materials_warning(case_id)
        if pending_warn is not None:
            warnings.append(pending_warn)

        # Claim amount consistency soft check already in amount gate
        if claim is not None and has_payment_claim and claim_amount is not None:
            if not self._claim_amount_grounded(facts, claim_amount):
                # if amount gate didn't already block for other reasons, ensure consistency
                if not any(
                    i.code == ReadinessIssueCode.CLAIM_AMOUNT_NOT_PROVEN.value for i in blockers
                ):
                    blockers.append(
                        self._blocker(
                            ReadinessIssueCode.CLAIM_FACT_INCONSISTENT,
                            f"诉讼请求金额 {claim_amount:g} 无法由现有已确认事实推导。",
                            suggested_action="调整 ClaimDirection 金额或补充确认计算事实。",
                        )
                    )
                    missing.append("ClaimDirection 金额与事实一致")

        self._check_issue_readiness(case_id, warnings, missing, next_actions)
        self._check_claim_readiness(case_id, warnings, missing, next_actions)

        status = (
            ReadinessStatus.READY if not blockers else ReadinessStatus.NOT_READY
        )
        display = "已具备" if status == ReadinessStatus.READY else "尚未具备"

        if status == ReadinessStatus.NOT_READY and not next_actions:
            next_actions.append("根据上述缺口补充并确认关键事实后再生成起诉状")

        return PleadingReadinessResult(
            status=status,
            blocking_issues=blockers,
            warnings=warnings,
            confirmed_strengths=strengths,
            missing_information=missing,
            suggested_next_actions=next_actions,
            checked_at=checked_at,
            input_refs={
                "party_refs": party_refs,
                "fact_refs": [
                    {
                        "fact_key": r["fact_key"],
                        "fact_version": r["fact_version"],
                    }
                    for r in fact_refs
                ],
                "evidence_refs": self._evidence_refs(case_id),
                "claim_direction_ref": claim_ref,
            },
            display_status=display,
        )

    def assert_ready(self, case_id: UUID) -> PleadingReadinessResult:
        result = self.evaluate(case_id)
        if result.status != ReadinessStatus.READY:
            raise PleadingNotReadyError(result)
        return result

    def lawyer_summary(self, result: PleadingReadinessResult) -> str:
        """Natural-language explanation for Agent / Conversation (no mutation)."""
        if result.status == ReadinessStatus.READY:
            lines = [
                "当前案件已基本具备生成起诉状初稿的条件。",
                "已确认要素包括：",
            ]
            for s in result.confirmed_strengths[:8]:
                lines.append(f"• {s}")
            if result.warnings:
                lines.append("仍有提示事项：")
                for w in result.warnings[:5]:
                    lines.append(f"• {w.message}")
            return "\n".join(lines)

        n = len(result.blocking_issues)
        lines = [
            f"现在还不建议生成起诉状，因为有{n}个关键问题没有解决：",
            "",
        ]
        for i, issue in enumerate(result.blocking_issues, start=1):
            lines.append(f"{i}. {issue.message}")
        if result.suggested_next_actions:
            lines.append("")
            lines.append("建议先处理这些问题，再生成起诉状。")
            for a in result.suggested_next_actions[:5]:
                lines.append(f"• {a}")
        return "\n".join(lines)

    def _check_claim_readiness(
        self,
        case_id: UUID,
        warnings: list[ReadinessIssue],
        missing: list[str],
        next_actions: list[str],
    ) -> None:
        view = ClaimViewService(self.session).build(case_id)
        if view.candidate_count and not view.confirmed_count:
            warnings.append(
                ReadinessIssue(
                    code=ReadinessIssueCode.CLAIM_ONLY_CANDIDATE.value,
                    severity="WARNING",
                    message="存在 AI 候选诉讼请求，但尚无律师确认的诉请。",
                    suggested_action="请审阅并确认诉讼请求后再推进起诉准备。",
                )
            )
        elif not view.confirmed_count and not view.candidate_count:
            warnings.append(
                ReadinessIssue(
                    code=ReadinessIssueCode.NO_CONFIRMED_CLAIM.value,
                    severity="WARNING",
                    message="尚未建立律师确认的诉讼请求。",
                    suggested_action="确认诉讼请求及依据后再评估起诉准备度。",
                )
            )
        for item in view.items:
            if item.status != "CONFIRMED":
                continue
            if item.stale_state.get("stale"):
                warnings.append(
                    ReadinessIssue(
                        code=ReadinessIssueCode.CLAIM_STALE.value,
                        severity="WARNING",
                        message=f"已确认诉请「{item.title[:40]}」已标记 stale。",
                        suggested_action="请复核并修订 stale 诉请。",
                    )
                )
            if any("lacks BASIS" in w for w in item.warnings):
                warnings.append(
                    ReadinessIssue(
                        code=ReadinessIssueCode.CLAIM_WITHOUT_BASIS.value,
                        severity="WARNING",
                        message=f"已确认诉请「{item.title[:40]}」缺少 BASIS 争点或事实关联。",
                        suggested_action="关联已确认争点或事实作为诉请基础。",
                    )
                )

    def _check_issue_readiness(
        self,
        case_id: UUID,
        warnings: list[ReadinessIssue],
        missing: list[str],
        next_actions: list[str],
    ) -> None:
        """Issue-aware readiness hints — warnings only, conservative blockers."""
        matrix = IssueMatrixService(self.session).build(case_id)
        if matrix.candidate_issue_count and not matrix.confirmed_issue_count:
            warnings.append(
                ReadinessIssue(
                    code=ReadinessIssueCode.ISSUE_ONLY_CANDIDATE.value,
                    severity="WARNING",
                    message="存在 AI 候选争点，但尚无律师确认的争点。",
                    suggested_action="请审阅并确认关键争点后，再依赖争点分析推进起诉准备。",
                )
            )
            missing.append("CONFIRMED issue")
            next_actions.append("确认至少一个关键争点")
        elif not matrix.confirmed_issue_count and not matrix.candidate_issue_count:
            warnings.append(
                ReadinessIssue(
                    code=ReadinessIssueCode.NO_CONFIRMED_ISSUE.value,
                    severity="WARNING",
                    message="尚未建立律师确认的争点矩阵。",
                    suggested_action="完成案情分析并确认争点后再评估起诉准备度。",
                )
            )

        for item in matrix.items:
            if item.status != "CONFIRMED":
                continue
            critical = item.fact_gaps or item.evidence_gaps
            if not critical:
                continue
            warnings.append(
                ReadinessIssue(
                    code=ReadinessIssueCode.ISSUE_CRITICAL_GAP.value,
                    severity="WARNING",
                    message=(
                        f"已确认争点「{item.statement[:40]}…」存在证明缺口提示。"
                        if len(item.statement) > 40
                        else f"已确认争点「{item.statement}」存在证明缺口提示。"
                    ),
                    suggested_action="补充关联事实或证据后再推进起诉状起草。",
                    related_fact_keys=[
                        g.related_fact_key for g in critical if g.related_fact_key
                    ],
                )
            )

    # ----- loaders -----

    def _confirmed_parties(self, case_id: UUID, role: PartyRole) -> list[CaseParty]:
        rows = list(
            self.session.scalars(
                select(CaseParty).where(
                    CaseParty.case_id == case_id,
                    CaseParty.role == role.value,
                    CaseParty.layer == "CONFIRMED",
                    CaseParty.stale.is_(False),
                    CaseParty.is_current.is_(True),
                )
            )
        )
        return rows

    def _confirmed_facts(self, case_id: UUID) -> list[Fact]:
        return list(
            self.session.scalars(
                select(Fact).where(
                    Fact.case_id == case_id,
                    Fact.status == "CONFIRMED",
                    Fact.stale.is_(False),
                    Fact.is_current.is_(True),
                )
            )
        )

    def _confirmed_claim(self, case_id: UUID) -> ClaimDirection | None:
        return self.session.scalar(
            select(ClaimDirection).where(
                ClaimDirection.case_id == case_id,
                ClaimDirection.status == "CONFIRMED",
                ClaimDirection.stale.is_(False),
                ClaimDirection.is_current.is_(True),
            )
        )

    def _evidence_refs(self, case_id: UUID) -> list[dict[str, Any]]:
        rows = list(
            self.session.scalars(
                select(EvidenceItem).where(
                    EvidenceItem.case_id == case_id,
                    EvidenceItem.acceptance == "ACCEPTED",
                    EvidenceItem.is_current.is_(True),
                )
            )
        )
        return [
            {"evidence_item_id": str(e.id), "evidence_item_version": e.version}
            for e in rows
        ]

    # ----- gates -----

    def _check_liability_bridge(
        self,
        defendants: list[CaseParty],
        facts: list[Fact],
        provenance_ok: dict[UUID, bool],
    ) -> ReadinessIssue | None:
        contract_parties = self._extract_contract_parties(facts)
        def_names = [d.name.strip() for d in defendants]
        # If no contract party mentioned, treat as unknown mismatch risk only when
        # defendant never appears as obligor in facts — still require bridge OR
        # identity match when contract parties extracted.
        if contract_parties:
            matched = any(
                self._name_overlap(dn, cp) for dn in def_names for cp in contract_parties
            )
            if matched:
                return None
            # mismatch — need bridge fact
            bridge = self._find_liability_bridge_facts(def_names, facts, provenance_ok)
            if bridge:
                return None
            return self._blocker(
                ReadinessIssueCode.DEFENDANT_LIABILITY_BASIS_MISSING,
                (
                    f"当前确认被告与合同签约义务方不一致"
                    f"（合同方：{'、'.join(contract_parties)}；"
                    f"被告：{'、'.join(def_names)}），"
                    "现有已确认事实不足以说明为什么当前被告应承担付款责任。"
                ),
                suggested_action="补充并确认债务加入/承继/实际履行义务方等责任桥梁事实。",
                related_party_keys=[str(d.party_key) for d in defendants],
            )

        # No explicit contract party extracted — still require that defendant appears
        # in some contractual/liability confirmed fact with provenance for payment cases.
        # If any fact mentions defendant + (合同|付款|义务) with provenance → pass lightly.
        for f in facts:
            stmt = f.statement or ""
            if any(self._name_overlap(dn, stmt) for dn in def_names) and any(
                k in stmt for k in ("合同", "付款", "义务", "签约", "甲方", "乙方")
            ):
                if provenance_ok.get(f.id, False):
                    return None
        # Ambiguous: if we have defendants but zero contract-party signal and no
        # defendant-linked obligation fact → block liability for safety on payment-like
        # incomplete files (incident: 中梁 without bridge).
        # Only block when there EXISTS a distinct third-party name in facts that looks
        # like a counterparty (甲方 pattern already covered). Otherwise pass with warning
        # path handled elsewhere.
        third = self._possible_other_obligors(facts, def_names)
        if third:
            bridge = self._find_liability_bridge_facts(def_names, facts, provenance_ok)
            if bridge:
                return None
            return self._blocker(
                ReadinessIssueCode.DEFENDANT_LIABILITY_BASIS_MISSING,
                (
                    f"材料中出现其他签约/义务主体（{'、'.join(third)}），"
                    f"与当前被告（{'、'.join(def_names)}）不一致，"
                    "现有已确认事实不足以说明被告应承担付款责任。"
                ),
                suggested_action="补充并确认被告责任桥梁事实。",
                related_party_keys=[str(d.party_key) for d in defendants],
            )
        return None

    def _find_liability_bridge_facts(
        self,
        def_names: list[str],
        facts: list[Fact],
        provenance_ok: dict[UUID, bool],
    ) -> list[Fact]:
        out: list[Fact] = []
        for f in facts:
            stmt = f.statement or ""
            if not any(self._name_overlap(dn, stmt) for dn in def_names):
                continue
            if not any(k in stmt for k in _LIABILITY_BRIDGE_KW):
                # also accept explicit "被告系付款义务人" style
                if not any(
                    k in stmt
                    for k in ("付款义务人", "应承担付款", "承担付款责任", "共同承担")
                ):
                    continue
            if not provenance_ok.get(f.id, False):
                continue
            out.append(f)
        return out

    def _check_performance(
        self,
        facts: list[Fact],
        provenance_ok: dict[UUID, bool],
    ) -> ReadinessIssue | None:
        for f in facts:
            stmt = f.statement or ""
            has_neg = any(n in stmt for n in _PERFORMANCE_NEG)
            has_strong = any(k in stmt for k in _PERFORMANCE_STRONG_KW)
            has_weak = any(k in stmt for k in _PERFORMANCE_WEAK_KW)
            if has_neg and not has_strong:
                # 合同约定“应当提交/工作范围包括提交” ≠ 实际履约
                continue
            if has_strong or (has_weak and not has_neg):
                if provenance_ok.get(f.id, False):
                    return None
        return self._blocker(
            ReadinessIssueCode.PERFORMANCE_NOT_ESTABLISHED,
            "目前只有合同约定或计划性描述，没有已确认的实际履约/交付事实，"
            "不足以支持「原告已按约履行」的诉状表述。",
            suggested_action="补充并确认实际交付、接收、验收或阶段性完成事实。",
        )

    def _check_payment_condition(
        self,
        facts: list[Fact],
        provenance_ok: dict[UUID, bool],
    ) -> ReadinessIssue | None:
        has_term_hint = any(
            any(k in (f.statement or "") for k in _PAYMENT_TERM_HINT) for f in facts
        )
        for f in facts:
            stmt = f.statement or ""
            if any(k in stmt for k in _PAYMENT_CONDITION_KW):
                if provenance_ok.get(f.id, False):
                    return None
            # acceptance / due language doubles as condition satisfied
            if any(k in stmt for k in ("验收通过", "催告付款", "已到期应付款", "应立即支付")):
                if provenance_ok.get(f.id, False):
                    return None

        if has_term_hint:
            return self._blocker(
                ReadinessIssueCode.PAYMENT_CONDITION_NOT_ESTABLISHED,
                "合同中存在付款条件线索，但尚无已确认事实证明付款条件已经成就。",
                suggested_action="确认付款条件内容，并确认条件成就事实。",
            )
        return self._blocker(
            ReadinessIssueCode.PAYMENT_TERM_UNCLEAR,
            "付款条件本身尚不明确，无法支持付款型诉讼请求。",
            suggested_action="从合同中确认付款条件并固化为已确认事实。",
        )

    def _check_amount_chain(
        self,
        facts: list[Fact],
        provenance_ok: dict[UUID, bool],
        claim_amount: float | None,
    ) -> ReadinessIssue | None:
        stmts = [(f, f.statement or "") for f in facts]
        has_cap_only = any(
            ("封顶" in s or "上限" in s) and not any(
                k in s for k in ("未付", "欠付", "余额", "应付", "优化金额", "结算")
            )
            for _, s in stmts
        )
        has_base = any(
            any(k in s for k in ("优化金额", "优化造价", "结算金额", "计费基数", "总价", "服务费"))
            for _, s in stmts
        )
        has_rate = any(
            any(k in s for k in ("%", "百分之", "费率", "8%", "比例")) for _, s in stmts
        )
        has_paid = any(any(k in s for k in ("已付", "已付款", "支付了")) for _, s in stmts)
        has_outstanding = any(
            any(k in s for k in ("未付", "欠付", "余额", "尚欠", "应付未付", "剩余"))
            for _, s in stmts
        )

        if has_cap_only and not (has_base or has_outstanding):
            return self._blocker(
                ReadinessIssueCode.CLAIM_AMOUNT_NOT_PROVEN,
                "仅有合同封顶/上限约定，不能直接得出应付金额；缺少优化金额或最终结算等计算事实。",
                suggested_action="确认优化金额、费率、已付款与未付余额。",
            )

        arithmetic_ok = False
        if claim_amount is not None:
            arithmetic_ok = self._arithmetic_supports_claim(facts, claim_amount)

        if not (
            (has_outstanding and (has_paid or has_base or arithmetic_ok))
            or (has_base and has_rate and has_paid)
            or (has_base and has_paid and arithmetic_ok)
        ):
            return self._blocker(
                ReadinessIssueCode.CLAIM_AMOUNT_NOT_PROVEN,
                "现有已确认事实不足以构成金额计算闭环"
                "（基数×费率-已付=未付，或明确未付余额，或总价-已付可推导请求额）。",
                suggested_action="补充并确认服务费计算链与未付金额。",
            )

        amount_facts = [
            f
            for f, s in stmts
            if any(k in s for k in _AMOUNT_CHAIN_KW)
            or any(k in s for k in ("未付", "欠付", "余额", "已付", "总价", "剩余"))
        ]
        if amount_facts and not any(provenance_ok.get(f.id, False) for f in amount_facts):
            return self._blocker(
                ReadinessIssueCode.CLAIM_AMOUNT_NOT_PROVEN,
                "金额相关事实缺少合法证据溯源，不能作为起诉金额依据。",
                suggested_action="为金额事实绑定 ACCEPTED 证据。",
            )

        if claim_amount is not None and not (
            self._claim_amount_grounded([f for f, _ in stmts], claim_amount)
            or arithmetic_ok
        ):
            return self._blocker(
                ReadinessIssueCode.CLAIM_AMOUNT_NOT_PROVEN,
                f"诉讼请求金额 {claim_amount:g} 无法由已确认事实中的数字推导。",
                suggested_action="核对金额计算链与 ClaimDirection。",
            )
        return None

    def _arithmetic_supports_claim(self, facts: list[Fact], claim_amount: float) -> bool:
        vals: list[float] = []
        for f in facts:
            for m in _MONEY_RE.finditer(f.statement or ""):
                try:
                    val = float(m.group(1))
                except ValueError:
                    continue
                snippet = m.group(0)
                if "万元" in snippet or (
                    "万" in snippet and "元" not in snippet.replace("万元", "")
                ):
                    val *= 10000
                vals.append(val)
        # total - paid ≈ claim
        for i, a in enumerate(vals):
            for j, b in enumerate(vals):
                if i == j:
                    continue
                if abs(a - b - claim_amount) < 0.51:
                    return True
        return False

    def _check_debt_due(
        self,
        facts: list[Fact],
        provenance_ok: dict[UUID, bool],
    ) -> ReadinessIssue | None:
        for f in facts:
            stmt = f.statement or ""
            if any(k in stmt for k in _DEBT_DUE_KW):
                if provenance_ok.get(f.id, False):
                    return None
            if "催告" in stmt and provenance_ok.get(f.id, False):
                return None
        return self._blocker(
            ReadinessIssueCode.DEBT_DUE_STATUS_UNCLEAR,
            "无法从已确认事实确定债务已经到期或可请求，禁止生成付款型起诉状。",
            suggested_action="确认到期日、条件成就日或催告事实。",
        )

    def _check_jurisdiction(self, facts: list[Fact]) -> ReadinessIssue | None:
        for f in facts:
            if any(k in (f.statement or "") for k in _JURISDICTION_KW):
                return None
        return ReadinessIssue(
            code=ReadinessIssueCode.JURISDICTION_NOT_FINALIZED.value,
            severity="WARNING",
            message="管辖基础尚未最终明确；起诉状中法院名称请律师补充，系统不会自行编造法院。",
            suggested_action="确认合同管辖条款或被告住所地等连接点。",
        )

    def _check_amount_conflicts(
        self, case_id: UUID, has_payment_claim: bool
    ) -> ReadinessIssue | None:
        if not has_payment_claim:
            return None
        # Prefer live CONFIRMED fact contradictions over stale analyst notes
        confirmed = self._confirmed_facts(case_id)
        paid_vals: list[float] = []
        paid_pat = re.compile(
            r"(?:已付(?:款)?|支付了|已支付)\s*(?:人民币)?\s*"
            r"([0-9]+(?:\.[0-9]+)?)\s*(?:万元|元|万)?"
        )
        for f in confirmed:
            stmt = f.statement or ""
            for m in paid_pat.finditer(stmt):
                try:
                    val = float(m.group(1))
                except ValueError:
                    continue
                snippet = m.group(0)
                if "万元" in snippet or (
                    snippet.rstrip().endswith("万") and "元" not in snippet
                ):
                    val *= 10000
                paid_vals.append(self._norm_money(val))
        uniq_paid = {v for v in paid_vals}
        if len(uniq_paid) >= 2:
            return self._blocker(
                ReadinessIssueCode.MATERIAL_FACT_CONFLICT,
                "已确认事实中存在互相冲突的已付款/金额记载，必须先解决冲突再生成起诉状。",
                suggested_action="核对并确认冲突的付款/金额事实。",
            )

        instance = self.session.scalar(
            select(WorkflowInstance)
            .where(WorkflowInstance.case_id == case_id)
            .order_by(WorkflowInstance.created_at.desc())
        )
        if instance is None:
            return None
        ctx = instance.context_json or {}
        conflicts = []
        for key in ("case_analyst", "analysis", "n4_analysis"):
            block = ctx.get(key) or {}
            if isinstance(block, dict):
                conflicts.extend(block.get("conflicts") or [])
        if isinstance(ctx.get("conflicts"), list):
            conflicts.extend(ctx["conflicts"])

        # Analyst conflict only blocks if it still mirrors CONFIRMED fact text
        amount_related = []
        for c in conflicts:
            if not isinstance(c, dict):
                continue
            text = " ".join(
                str(c.get(k) or "")
                for k in ("description", "summary", "type", "message", "conflict_type")
            )
            if not any(k in text for k in ("已付", "金额", "付款", "余额", "服务费", "amount")):
                continue
            # If description cites two paid amounts, require both still in confirmed facts
            nums = {self._norm_money(float(x)) for x in re.findall(r"(\d{4,})", text)}
            paid_norm = {self._norm_money(v) for v in paid_vals}
            if nums and not (nums <= paid_norm or len(nums & uniq_paid) >= 2):
                # conflict mentioned but lawyer already rejected conflicting side
                continue
            if nums and len(nums & uniq_paid) >= 2:
                amount_related.append(c)
            elif not nums and uniq_paid and len(uniq_paid) >= 2:
                amount_related.append(c)
        if amount_related:
            return self._blocker(
                ReadinessIssueCode.MATERIAL_FACT_CONFLICT,
                "案情分析已识别直接影响请求金额的关键事实冲突，必须先解决冲突再生成起诉状。",
                suggested_action="核对并确认冲突的付款/金额事实。",
            )
        return None

    def _pending_materials_warning(self, case_id: UUID) -> ReadinessIssue | None:
        policy = MaterialUsabilityPolicy(self.session)
        pending = policy.list_unusable_materials(case_id)
        if not pending:
            return None
        names = [getattr(m, "filename", None) or str(m.id) for m in pending[:20]]
        return ReadinessIssue(
            code=ReadinessIssueCode.PENDING_MATERIAL_DISCLOSED.value,
            severity="WARNING",
            message="以下文件未参与本轮 readiness 判断：" + "；".join(names),
            suggested_action="如其中含付款凭证/结算确认/履约证明，请先完成解析并纳入可用材料池。",
        )

    def _key_facts_missing_provenance(
        self,
        facts: list[Fact],
        provenance_ok: dict[UUID, bool],
        has_payment_claim: bool,
    ) -> list[Fact]:
        """Facts that look key for gates but lack provenance."""
        bad: list[Fact] = []
        for f in facts:
            stmt = f.statement or ""
            is_key = False
            if any(k in stmt for k in _LIABILITY_BRIDGE_KW):
                is_key = True
            if any(k in stmt for k in _PERFORMANCE_STRONG_KW) or any(
                k in stmt for k in _PERFORMANCE_WEAK_KW
            ):
                is_key = True
            if has_payment_claim and any(
                k in stmt for k in ("已付", "未付", "应付", "优化金额", "到期", "催告")
            ):
                is_key = True
            if is_key and not provenance_ok.get(f.id, False):
                bad.append(f)
        return bad

    # ----- provenance -----

    def _fact_has_valid_provenance(self, fact: Fact) -> bool:
        links = list(
            self.session.scalars(
                select(FactEvidenceLink).where(
                    FactEvidenceLink.fact_id == fact.id,
                    FactEvidenceLink.status == "ACTIVE",
                )
            )
        )
        if not links:
            return False
        for link in links:
            ev = self.session.scalar(
                select(EvidenceItem).where(
                    EvidenceItem.id == link.evidence_item_id,
                    EvidenceItem.version == link.evidence_item_version,
                )
            )
            if ev is None or ev.acceptance != "ACCEPTED":
                continue
            span_ids: list[UUID] = []
            if link.source_span_id is not None:
                span_ids.append(link.source_span_id)
            else:
                from backend.models import EvidenceItemSpan

                for eis in self.session.scalars(
                    select(EvidenceItemSpan).where(
                        EvidenceItemSpan.evidence_item_id == ev.id,
                        EvidenceItemSpan.evidence_item_version == ev.version,
                    )
                ):
                    span_ids.append(eis.source_span_id)
            for span_id in span_ids:
                span = self.session.get(SourceSpan, span_id)
                if span is None:
                    continue
                ec = self.session.get(ExtractedContent, span.extracted_content_id)
                if ec is None or ec.status != "SUCCEEDED":
                    continue
                material = self.session.get(CaseMaterial, span.material_id)
                if material is None or material.life_status == "VOID":
                    continue
                return True
        return False

    # ----- helpers -----

    def _extract_contract_parties(self, facts: list[Fact]) -> list[str]:
        names: list[str] = []
        for f in facts:
            stmt = f.statement or ""
            for pat in _CONTRACT_PARTY_PATTERNS:
                for m in pat.finditer(stmt):
                    name = m.group(1).strip().rstrip("。；;")
                    if len(name) >= 2 and name not in names:
                        names.append(name)
            # also "合同签约甲方：X"
            if "甲方" in stmt and "：|" in stmt.replace(":", "："):
                pass
        return names

    def _possible_other_obligors(
        self, facts: list[str] | list[Fact], def_names: list[str]
    ) -> list[str]:
        # Collect org-like names from facts that are not defendant
        org_re = re.compile(r"([\u4e00-\u9fff]{2,20}(?:有限公司|公司|集团|置业))")
        found: list[str] = []
        for f in facts:
            stmt = f.statement if isinstance(f, Fact) else str(f)
            for m in org_re.finditer(stmt):
                name = m.group(1)
                if any(self._name_overlap(dn, name) for dn in def_names):
                    continue
                if name not in found:
                    found.append(name)
        return found

    def _claim_amount_grounded(self, facts: list[Fact], amount: float) -> bool:
        """True if amount (or yuan/wan variants) appears in confirmed facts."""
        targets = {self._norm_money(amount)}
        # also wan
        if amount >= 10000:
            targets.add(self._norm_money(amount / 10000))
        targets.add(self._norm_money(amount * 10000))  # if claim stored as wan
        for f in facts:
            for m in _MONEY_RE.finditer(f.statement or ""):
                raw = m.group(1)
                try:
                    val = float(raw)
                except ValueError:
                    continue
                snippet = m.group(0)
                if "万" in snippet and "万元" not in snippet:
                    # ambiguous; treat as wan if followed by 万
                    pass
                if "万元" in snippet or (snippet.endswith("万") and "元" not in snippet):
                    val = val * 10000
                if self._norm_money(val) in targets:
                    return True
                # allow close match for float
                if abs(val - amount) < 0.01:
                    return True
        return False

    @staticmethod
    def _norm_money(v: float) -> float:
        return round(float(v), 2)

    @staticmethod
    def _name_overlap(a: str, b: str) -> bool:
        a = (a or "").strip()
        b = (b or "").strip()
        if not a or not b:
            return False
        return a in b or b in a

    @staticmethod
    def _blocker(
        code: ReadinessIssueCode,
        message: str,
        *,
        suggested_action: str | None = None,
        related_fact_keys: list[str] | None = None,
        related_party_keys: list[str] | None = None,
    ) -> ReadinessIssue:
        return ReadinessIssue(
            code=code.value,
            severity="BLOCKER",
            message=message,
            suggested_action=suggested_action,
            related_fact_keys=related_fact_keys or [],
            related_party_keys=related_party_keys or [],
        )


__all__ = [
    "PleadingReadinessService",
    "PleadingNotReadyError",
    "PleadingReadinessResult",
    "ReadinessStatus",
]
