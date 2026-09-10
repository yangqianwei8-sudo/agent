"""Pleading Draft Validator — post-generation quality contract (deterministic)."""

from __future__ import annotations

import re

from backend.schemas.pleading_quality import (
    PleadingDraftValidationResult,
    StructuredPleadingInput,
    ValidationIssue,
)
from backend.schemas.pleading_writer import PleadingWriterEngineResult
from backend.skills.pleading_writer import looks_like_fabricated_law_citation

UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.I,
)
INTERNAL_FIELD_RE = re.compile(
    r"\b(fact_key|evidence_item_id|supporting_fact_ids|claim_direction_key|"
    r"material_id|source_span_id|party_key|amount_formula)\b",
    re.I,
)
SPECULATIVE_RE = re.compile(
    r"(如原告|如被告|如果原告|可能构成|可能已经|假设|根据案件情况|"
    r"如(原告)?已(提交|交付|履行)|若已|倘若|原则上)",
)
GENERIC_LIABILITY_RE = re.compile(
    r"被告依法应(承担|负).*责任(?!.*(债务|承继|加入|实际|确认|合同|付款|义务))"
)
GENERIC_PERFORMANCE_RE = re.compile(r"原告已按约履行全部义务")
COURT_GUESS_RE = re.compile(r"[\u4e00-\u9fff]{2,10}人民法院")
_MONEY_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*(?:万元|万|元)?")

HIGH_RISK_ASSERTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("主体", re.compile(r"[\u4e00-\u9fff]{2,20}(有限公司|公司|集团)")),
    ("金额", _MONEY_RE),
    ("日期", re.compile(r"\d{4}年\d{1,2}月\d{1,2}日")),
    ("履约", re.compile(r"(已交付|已提交|已完成|签收|验收)")),
    ("付款", re.compile(r"(已付|已支付|支付了)")),
    ("余额", re.compile(r"(尚欠|未付|余额|欠付)")),
    ("到期", re.compile(r"(已到期|到期|逾期)")),
    ("责任", re.compile(r"(应承担|付款义务|债务加入|承继)")),
]

BANNED_FLUFF = (
    "综上所述",
    "为维护原告合法权益",
    "依法应予支持",
    "根据相关法律规定，原告特提起诉讼，请求判如所请",
)


class PleadingDraftValidator:
    """Validate Writer output against structured confirmed input."""

    def validate(
        self,
        result: PleadingWriterEngineResult,
        structured: StructuredPleadingInput,
        *,
        full_text: str,
    ) -> PleadingDraftValidationResult:
        issues: list[ValidationIssue] = []

        if not (result.title or "").strip():
            issues.append(self._err("TITLE_PRESENT", "缺少标题"))
        else:
            issues.append(self._ok("TITLE_PRESENT"))

        if "原告" not in result.parties_section:
            issues.append(self._err("PARTIES_PRESENT", "缺少原告信息"))
        elif "被告" not in result.parties_section:
            issues.append(self._err("PARTIES_PRESENT", "缺少被告信息"))
        else:
            issues.append(self._ok("PARTIES_PRESENT"))

        if not result.claims_section.strip():
            issues.append(self._err("CLAIMS_PRESENT", "缺少诉讼请求"))
        else:
            issues.append(self._ok("CLAIMS_PRESENT"))

        if not result.facts_and_reasons_section.strip():
            issues.append(self._err("FACTS_PRESENT", "缺少事实与理由"))
        else:
            issues.append(self._ok("FACTS_PRESENT"))

        self._check_claim_amounts(result, structured, issues)
        self._check_text_integrity(full_text, structured, issues)
        self._check_court_section(result.court_section, structured, issues)
        self._check_liability_narrative(result, structured, issues)
        self._check_performance(result, structured, issues)
        self._check_amount_narrative(result, structured, issues)
        self._check_evidence_directory(result, structured, issues)
        self._check_citations(result, structured, issues)
        self._check_unsupported_assertions(result, structured, issues)
        self._check_fluff(full_text, issues)

        passed = not any(i.severity == "ERROR" for i in issues)
        return PleadingDraftValidationResult(passed=passed, issues=issues)

    def _check_claim_amounts(
        self,
        result: PleadingWriterEngineResult,
        structured: StructuredPleadingInput,
        issues: list[ValidationIssue],
    ) -> None:
        payload_claims = list(structured.claims_payload.get("claims") or [])
        amount_ok = True
        for out, src in zip(result.claims, payload_claims, strict=False):
            src_amount = src.get("amount")
            if src_amount is None:
                continue
            if out.amount is None or abs(float(out.amount) - float(src_amount)) > 1e-9:
                amount_ok = False
                issues.append(
                    self._err(
                        "CLAIM_AMOUNT_MATCH",
                        f"诉请金额 {out.amount} 与 ClaimDirection {src_amount} 不一致",
                    )
                )
            token = str(int(src_amount)) if float(src_amount).is_integer() else str(src_amount)
            if token not in out.text.replace(",", ""):
                amount_ok = False
                issues.append(
                    self._err("CLAIM_AMOUNT_MATCH", f"诉请文本缺少金额 {token}")
                )
        if amount_ok and payload_claims:
            issues.append(self._ok("CLAIM_AMOUNT_MATCH"))

        allowed = {round(float(a), 2) for a in structured.allowed_amounts}
        extra_found = False
        for m in _MONEY_RE.finditer(
            result.facts_and_reasons_section + result.claims_section
        ):
            try:
                val = float(m.group(1))
            except ValueError:
                continue
            snippet = m.group(0)
            if "万" in snippet and "元" not in snippet.replace("万元", ""):
                val *= 10000
            val = round(val, 2)
            if allowed and val not in allowed and val >= 1000:
                if 1900 < val < 2100:
                    continue
                token = str(int(val)) if val.is_integer() else str(val)
                if any(token in (f.statement or "") for bucket in (
                    structured.contract_facts,
                    structured.service_term_facts,
                    structured.amount_facts,
                    structured.payment_history_facts,
                    structured.outstanding_facts,
                ) for f in bucket):
                    continue
                extra_found = True
                issues.append(
                    self._err("NO_EXTRA_AMOUNT", f"正文出现未确认金额 {val:g}")
                )
        if not extra_found:
            issues.append(self._ok("NO_EXTRA_AMOUNT"))

    def _check_text_integrity(
        self,
        full_text: str,
        structured: StructuredPleadingInput,
        issues: list[ValidationIssue],
    ) -> None:
        if UUID_RE.search(full_text):
            issues.append(self._err("NO_UUID_IN_BODY", "正文不得出现 UUID"))
        else:
            issues.append(self._ok("NO_UUID_IN_BODY"))

        if INTERNAL_FIELD_RE.search(full_text):
            issues.append(self._err("NO_INTERNAL_FIELD_NAMES", "正文不得出现内部字段名"))
        else:
            issues.append(self._ok("NO_INTERNAL_FIELD_NAMES"))

        if looks_like_fabricated_law_citation(full_text):
            issues.append(self._err("NO_UNKNOWN_LAW_ARTICLE", "不得编造具体法条号"))
        else:
            issues.append(self._ok("NO_UNKNOWN_LAW_ARTICLE"))

        if SPECULATIVE_RE.search(full_text):
            issues.append(
                self._err("SPECULATIVE_LANGUAGE", "正文含假设性/推测性表述")
            )

        unsupported_party = False
        for raw in re.findall(r"[\u4e00-\u9fff]{2,24}有限公司", full_text):
            name = raw
            for prefix in ("原告", "被告", "第三人", "甲方", "乙方", "丙方"):
                if name.startswith(prefix):
                    name = name[len(prefix) :]
            if any(self._overlap(name, c) for c in structured.contract_party_names):
                continue
            if structured.allowed_party_names and not any(
                self._overlap(name, p) for p in structured.allowed_party_names
            ):
                unsupported_party = True
                issues.append(
                    self._err("NO_UNSUPPORTED_PARTY", f"正文出现未确认主体：{name}")
                )
        if not unsupported_party:
            issues.append(self._ok("NO_UNSUPPORTED_PARTY"))

    def _check_court_section(
        self,
        court_section: str,
        structured: StructuredPleadingInput,
        issues: list[ValidationIssue],
    ) -> None:
        if "待确认" in court_section or "待律师" in court_section:
            issues.append(self._ok("NO_UNKNOWN_COURT"))
            return
        court_guessed = False
        for m in COURT_GUESS_RE.finditer(court_section):
            court = m.group(0)
            if any(token in court for token in ("住所地", "履行地", "标的物")):
                court_guessed = True
                issues.append(
                    self._err(
                        "NO_UNKNOWN_COURT",
                        f"不得将管辖约定条款直接作为受诉法院：{court}",
                    )
                )
                continue
            if structured.finalized_court_name and court in structured.finalized_court_name:
                continue
            court_guessed = True
            issues.append(
                self._err("NO_UNKNOWN_COURT", f"不得自行编造法院名称：{court}")
            )
        if not court_guessed:
            issues.append(self._ok("NO_UNKNOWN_COURT"))

    def _check_liability_narrative(
        self,
        result: PleadingWriterEngineResult,
        structured: StructuredPleadingInput,
        issues: list[ValidationIssue],
    ) -> None:
        if not structured.liability_bridge_required:
            issues.append(self._ok("LIABILITY_NARRATIVE_SUPPORTED"))
            return
        body = result.facts_and_reasons_section
        if not structured.liability_facts:
            issues.append(
                self._err(
                    "LIABILITY_NARRATIVE_SUPPORTED",
                    "合同主体与被告不一致，但正文未体现已确认责任桥梁",
                )
            )
            return
        bridge_ok = any(
            k in body
            for k in ("虽然", "尽管", "债务加入", "承继", "实际承担", "加入", "根据已确认事实")
        )
        if not bridge_ok:
            issues.append(
                self._err(
                    "LIABILITY_NARRATIVE_SUPPORTED",
                    "合同主体与被告不一致时，正文须明确说明责任桥梁",
                )
            )
        elif GENERIC_LIABILITY_RE.search(body):
            issues.append(
                self._err(
                    "LIABILITY_NARRATIVE_SUPPORTED",
                    "不得仅写「被告依法应承担责任」而无具体事实支撑",
                )
            )
        else:
            issues.append(self._ok("LIABILITY_NARRATIVE_SUPPORTED"))

    def _check_performance(
        self,
        result: PleadingWriterEngineResult,
        structured: StructuredPleadingInput,
        issues: list[ValidationIssue],
    ) -> None:
        has_perf = bool(structured.performance_facts or structured.acceptance_facts)
        body = result.facts_and_reasons_section
        if has_perf and re.search(r"如(原告)?已(提交|交付|履行)", body):
            issues.append(
                self._err(
                    "PERFORMANCE_NARRATIVE_SUPPORTED",
                    "已有确认履约事实，不得使用「如已提交」等假设表述",
                )
            )
        elif GENERIC_PERFORMANCE_RE.search(body):
            issues.append(
                self._err(
                    "PERFORMANCE_NARRATIVE_SUPPORTED",
                    "正文使用无细节概括替代具体履约事实",
                )
            )
        elif has_perf:
            issues.append(self._ok("PERFORMANCE_NARRATIVE_SUPPORTED"))
        elif GENERIC_PERFORMANCE_RE.search(body):
            issues.append(
                self._err(
                    "PERFORMANCE_NARRATIVE_SUPPORTED",
                    "正文声称全部履约，但无对应已确认事实",
                )
            )
        else:
            issues.append(self._ok("PERFORMANCE_NARRATIVE_SUPPORTED"))

    def _check_amount_narrative(
        self,
        result: PleadingWriterEngineResult,
        structured: StructuredPleadingInput,
        issues: list[ValidationIssue],
    ) -> None:
        claims = list(structured.claims_payload.get("claims") or [])
        is_payment = any(str(c.get("claim_type")) == "PAYMENT" for c in claims)
        if not is_payment:
            issues.append(self._ok("AMOUNT_NARRATIVE_SUPPORTED"))
            return

        body = result.facts_and_reasons_section
        has_rate = any(
            k in body for k in ("%", "费率", "8%", "比例", "计取", "封顶", "上限")
        )
        has_base = any(k in body for k in ("优化金额", "结算", "基数", "计算"))
        has_paid = any(k in body for k in ("已付", "已支付", "支付了"))
        has_outstanding = any(k in body for k in ("尚欠", "未付", "余额", "欠付"))

        if structured.amount_facts or structured.outstanding_facts:
            if not (has_rate or has_base) and structured.service_term_facts:
                has_rate = True
            if not (has_paid and has_outstanding) and not (
                has_outstanding and structured.payment_history_facts
            ):
                issues.append(
                    self._err(
                        "AMOUNT_NARRATIVE_SUPPORTED",
                        "付款型案件正文缺少完整金额链（收费规则/基数/已付/未付）",
                    )
                )
            else:
                issues.append(self._ok("AMOUNT_NARRATIVE_SUPPORTED"))
        else:
            issues.append(self._ok("AMOUNT_NARRATIVE_SUPPORTED"))

    def _check_evidence_directory(
        self,
        result: PleadingWriterEngineResult,
        structured: StructuredPleadingInput,
        issues: list[ValidationIssue],
    ) -> None:
        expected_materials = len(structured.evidence_directory)
        actual = len(result.evidence_directory)
        if expected_materials and actual > expected_materials:
            issues.append(
                self._err(
                    "EVIDENCE_DIRECTORY_GROUPED",
                    f"证据目录条目 {actual} 超过材料归并数 {expected_materials}，"
                    "同一材料不得拆成多项",
                )
            )
        else:
            issues.append(self._ok("EVIDENCE_DIRECTORY_GROUPED"))

        for item in result.evidence_directory:
            text = f"{item.title} {item.proof_purpose}"
            if UUID_RE.search(text):
                issues.append(
                    self._err("EVIDENCE_DIRECTORY_GROUPED", "证据目录不得出现 UUID")
                )

    def _check_citations(
        self,
        result: PleadingWriterEngineResult,
        structured: StructuredPleadingInput,
        issues: list[ValidationIssue],
    ) -> None:
        allowed_keys = {
            (str(f.fact_key), f.fact_version)
            for bucket in (
                structured.contract_facts,
                structured.service_term_facts,
                structured.performance_facts,
                structured.acceptance_facts,
                structured.liability_facts,
                structured.amount_facts,
                structured.payment_history_facts,
                structured.outstanding_facts,
                structured.payment_term_facts,
                structured.due_facts,
                structured.demand_facts,
                structured.jurisdiction_facts,
                structured.background_facts,
            )
            for f in bucket
        }
        if not result.fact_blocks:
            issues.append(self._err("NO_FACT_BLOCKS", "缺少事实块引用"))
            return
        candidate_leak = False
        for block in result.fact_blocks:
            if not block.fact_refs:
                issues.append(
                    self._err("NO_FACT_BLOCKS", f"块 {block.block_id} 无 fact 引用")
                )
            for fr in block.fact_refs:
                if (str(fr.fact_key), fr.fact_version) not in allowed_keys:
                    candidate_leak = True
                    issues.append(
                        self._err(
                            "NO_CANDIDATE_FACT",
                            f"块 {block.block_id} 引用非 structured input 事实",
                        )
                    )
        if not candidate_leak:
            issues.append(self._ok("NO_CANDIDATE_FACT"))

    def _check_unsupported_assertions(
        self,
        result: PleadingWriterEngineResult,
        structured: StructuredPleadingInput,
        issues: list[ValidationIssue],
    ) -> None:
        """Conservative high-risk assertion → confirmed source check."""
        source_blob = " ".join(
            f.statement
            for bucket in (
                structured.contract_facts,
                structured.service_term_facts,
                structured.performance_facts,
                structured.acceptance_facts,
                structured.liability_facts,
                structured.amount_facts,
                structured.payment_history_facts,
                structured.outstanding_facts,
                structured.payment_term_facts,
                structured.due_facts,
                structured.demand_facts,
                structured.background_facts,
            )
            for f in bucket
        )
        source_blob += " " + " ".join(structured.allowed_party_names)
        source_blob += " " + " ".join(structured.contract_party_names)
        source_blob += " " + " ".join(str(a) for a in structured.allowed_amounts)
        for party in structured.parties:
            ids = party.get("identifiers_json") or {}
            source_blob += " " + " ".join(str(v) for v in ids.values() if v)

        # Party identifiers (credit codes etc.) live in parties_section — exclude from scan.
        body = (
            result.facts_and_reasons_section
            + "\n"
            + result.claims_section
            + "\n"
            + result.court_section
            + "\n"
            + result.signature_section
        )
        unsupported = False
        for label, pat in HIGH_RISK_ASSERTION_PATTERNS:
            for m in pat.finditer(body):
                fragment = m.group(0)
                if fragment in source_blob:
                    continue
                if label == "金额":
                    digits = re.sub(r"\D", "", fragment)
                    if len(digits) >= 15:
                        continue
                    if digits and digits in re.sub(r"\D", "", source_blob):
                        continue
                    try:
                        val = float(m.group(1))
                        snippet = m.group(0)
                        if "万" in snippet:
                            val *= 10000
                        if any(abs(val - a) < 0.51 for a in structured.allowed_amounts):
                            continue
                        if val >= 100000000:
                            continue
                    except (ValueError, IndexError):
                        pass
                if label == "主体" and any(
                    k in fragment for k in ("涉案", "合同由", "根据已确认", "因此")
                ):
                    continue
                if label == "主体":
                    name_match = re.search(
                        r"([\u4e00-\u9fff]{2,24}有限公司)", fragment
                    )
                    check_name = name_match.group(1) if name_match else fragment
                    for prefix in ("原告", "被告", "甲方", "乙方", "丙方"):
                        if check_name.startswith(prefix):
                            check_name = check_name[len(prefix) :]
                    if any(
                        self._overlap(check_name, p)
                        for p in structured.allowed_party_names
                    ) or any(
                        self._overlap(check_name, p)
                        for p in structured.contract_party_names
                    ):
                        continue
                if label == "责任" and any(
                    k in source_blob for k in ("承担", "债务加入", "承继", "付款义务")
                ):
                    continue
                unsupported = True
                issues.append(
                    self._err(
                        "UNSUPPORTED_ASSERTION",
                        f"高风险断言「{fragment}」({label}) 无 confirmed 来源",
                    )
                )
                break
            if unsupported:
                break

    def _check_fluff(self, full_text: str, issues: list[ValidationIssue]) -> None:
        for phrase in BANNED_FLUFF:
            if phrase in full_text:
                issues.append(
                    ValidationIssue(
                        code="AI_FLUFF",
                        message=f"含空泛套话：{phrase}",
                        severity="WARNING",
                    )
                )

    @staticmethod
    def _overlap(a: str, b: str) -> bool:
        return a in b or b in a

    @staticmethod
    def _err(code: str, message: str) -> ValidationIssue:
        return ValidationIssue(code=code, message=message, severity="ERROR")

    @staticmethod
    def _ok(code: str) -> ValidationIssue:
        return ValidationIssue(code=code, message="ok", severity="INFO")
