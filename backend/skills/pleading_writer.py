"""Pleading Writer engine + deterministic quality renderer."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from backend.schemas.case_analyst import EvidenceRef
from backend.schemas.claim_direction_proposal import FactRef
from backend.schemas.pleading_quality import (
    MaterialEvidenceGroup,
    StructuredFactItem,
    StructuredPleadingInput,
)
from backend.schemas.pleading_writer import (
    ClaimDirectionRef,
    ClaimLine,
    EvidenceDirectoryItem,
    FactBlock,
    PleadingWriterEngineResult,
    PleadingWriterInput,
    WriterWarning,
)

FABRICATED_LAW_RE = re.compile(r"《[^》]+》第\d+条")
LPR_RE = re.compile(r"LPR|贷款市场报价利率|全国银行间同业拆借中心", re.I)

_CN_NUMS = "一二三四五六七八九十"


@dataclass
class PartyView:
    party_key: UUID
    role: str
    name: str
    party_type: str
    version: int
    identifiers_json: dict[str, Any] | None = None


@dataclass
class ConfirmedFactView:
    fact_key: UUID
    fact_version: int
    statement: str
    evidence_refs: list[EvidenceRef] = field(default_factory=list)


@dataclass
class AcceptedEvidenceView:
    evidence_item_id: UUID
    evidence_item_version: int
    number: str
    title: str
    summary: str | None
    category: str


@dataclass
class ClaimDirectionView:
    claim_direction_key: UUID
    claim_direction_version: int
    payload: dict[str, Any]
    status: str
    stale: bool


class PleadingWriterEngine(Protocol):
    def write(
        self,
        inp: PleadingWriterInput,
        *,
        parties: list[PartyView],
        facts: list[ConfirmedFactView],
        claim: ClaimDirectionView,
        evidence: list[AcceptedEvidenceView],
        structured: StructuredPleadingInput | None = None,
    ) -> PleadingWriterEngineResult: ...


class DeterministicPleadingWriterStub:
    """Quality renderer: confirmed inputs only; structured narrative order."""

    def repair(
        self,
        inp: PleadingWriterInput,
        *,
        parties: list[PartyView],
        facts: list[ConfirmedFactView],
        claim: ClaimDirectionView,
        evidence: list[AcceptedEvidenceView],
        structured: StructuredPleadingInput | None,
        errors: list[Any],
        prior: PleadingWriterEngineResult,
    ) -> PleadingWriterEngineResult:
        """One-shot repair: re-render from structured confirmed input."""
        _ = (errors, prior)
        return self.write(
            inp,
            parties=parties,
            facts=facts,
            claim=claim,
            evidence=evidence,
            structured=structured,
        )

    def write(
        self,
        inp: PleadingWriterInput,
        *,
        parties: list[PartyView],
        facts: list[ConfirmedFactView],
        claim: ClaimDirectionView,
        evidence: list[AcceptedEvidenceView],
        structured: StructuredPleadingInput | None = None,
    ) -> PleadingWriterEngineResult:
        from backend.application.pleading_structured_input import PleadingStructuredInputBuilder

        if structured is None:
            # Self-build when called without Application layer (tests)
            structured = PleadingStructuredInputBuilder.__new__(
                PleadingStructuredInputBuilder
            )
            # minimal inline build without session — use facts order fallback
            structured = _minimal_structured(parties, facts, claim, evidence)

        warnings = [
            WriterWarning(
                code="CLAIM_FACT_VERSION_PROVENANCE_GAP",
                message=(
                    "ClaimDirection.payload.supporting_fact_ids stores fact_key only; "
                    "Fact version is validated at Writer Application layer."
                ),
            ),
        ]
        court_section, court_warnings = _resolve_court_section(structured)
        warnings.extend(court_warnings)

        parties_section, party_warnings = _render_parties(parties)
        warnings.extend(party_warnings)

        plaintiff_name = next(
            (p.name for p in parties if p.role == "PLAINTIFF"),
            "【待律师确认】",
        )

        claim_lines, claims_section = _render_claims(claim.payload)
        ordered = _ordered_facts(structured)
        fact_blocks, facts_section = _render_facts_ordered(
            ordered, structured, parties=parties
        )
        evidence_dir, evidence_section = _render_evidence_directory(structured.evidence_directory)

        used_facts = [
            FactRef(fact_key=item.fact_key, fact_version=item.fact_version)
            for item in ordered
        ]
        used_evidence: list[EvidenceRef] = []
        seen_ev: set[tuple[UUID, int]] = set()
        for grp in structured.evidence_directory:
            for ref in grp.evidence_refs:
                key = (ref.evidence_item_id, ref.evidence_item_version)
                if key in seen_ev:
                    continue
                seen_ev.add(key)
                used_evidence.append(ref)

        return PleadingWriterEngineResult(
            title="民事起诉状",
            parties_section=parties_section,
            claims=claim_lines,
            claims_section=claims_section,
            fact_blocks=fact_blocks,
            facts_and_reasons_section=facts_section,
            evidence_directory=evidence_dir,
            evidence_section=evidence_section,
            court_section=court_section,
            signature_section=(
                f"具状人：{plaintiff_name}\n"
                "日期：【待律师确认】"
            ),
            warnings=warnings,
            used_fact_refs=used_facts,
            used_evidence_refs=used_evidence,
            used_claim_direction_ref=ClaimDirectionRef(
                claim_direction_key=claim.claim_direction_key,
                claim_direction_version=claim.claim_direction_version,
            ),
        )


class ScriptedPleadingWriterEngine:
    def __init__(self, result: PleadingWriterEngineResult) -> None:
        self._result = result

    def write(
        self,
        inp: PleadingWriterInput,
        *,
        parties: list[PartyView],
        facts: list[ConfirmedFactView],
        claim: ClaimDirectionView,
        evidence: list[AcceptedEvidenceView],
        structured: StructuredPleadingInput | None = None,
    ) -> PleadingWriterEngineResult:
        _ = (inp, parties, facts, claim, evidence, structured)
        return self._result.model_copy(deep=True)


def _resolve_court_section(
    structured: StructuredPleadingInput,
) -> tuple[str, list[WriterWarning]]:
    warnings: list[WriterWarning] = []
    if structured.finalized_court_name:
        return f"此致\n{structured.finalized_court_name}", warnings
    return (
        "此致\n【待确认有管辖权的人民法院】",
        [
            WriterWarning(
                code="MISSING_COURT",
                message="管辖法院未由律师确认，使用占位标记。",
            )
        ],
    )


def _cn_index(n: int) -> str:
    if 1 <= n <= 10:
        return _CN_NUMS[n - 1]
    if n < 20:
        return "十" + (_CN_NUMS[n - 11] if n > 10 else "")
    return str(n)


def _minimal_structured(
    parties: list[PartyView],
    facts: list[ConfirmedFactView],
    claim: ClaimDirectionView,
    evidence: list[AcceptedEvidenceView],
) -> StructuredPleadingInput:
    items = [
        StructuredFactItem(
            fact_key=f.fact_key,
            fact_version=f.fact_version,
            statement=f.statement,
            category="BACKGROUND",
            evidence_refs=list(f.evidence_refs),
        )
        for f in facts
    ]
    ev_dir = [
        MaterialEvidenceGroup(
            material_id=e.evidence_item_id,
            material_filename=e.title,
            display_number=e.number,
            title=e.title,
            proof_purposes=[e.summary or e.title],
            evidence_refs=[
                EvidenceRef(
                    evidence_item_id=e.evidence_item_id,
                    evidence_item_version=e.evidence_item_version,
                )
            ],
        )
        for e in evidence
    ]
    return StructuredPleadingInput(
        parties=[{"name": p.name, "role": p.role} for p in parties],
        claims_payload=dict(claim.payload or {}),
        background_facts=items,
        evidence_directory=ev_dir,
        defendant_names=[p.name for p in parties if p.role == "DEFENDANT"],
    )


def _ordered_facts(structured: StructuredPleadingInput) -> list[StructuredFactItem]:
    order = [
        structured.contract_facts,
        structured.service_term_facts,
        structured.performance_facts,
        structured.acceptance_facts,
        structured.amount_facts,
        structured.payment_history_facts,
        structured.outstanding_facts,
        structured.payment_term_facts,
        structured.due_facts,
        structured.demand_facts,
        structured.liability_facts,
        structured.background_facts,
    ]
    seen: set[tuple[UUID, int]] = set()
    out: list[StructuredFactItem] = []
    for bucket in order:
        for item in bucket:
            key = (item.fact_key, item.fact_version)
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
    return out


def _render_parties(
    parties: list[PartyView],
) -> tuple[str, list[WriterWarning]]:
    warnings: list[WriterWarning] = []
    lines: list[str] = []
    role_order = {"PLAINTIFF": 0, "DEFENDANT": 1, "THIRD_PARTY": 2, "OTHER": 3}
    ordered = sorted(parties, key=lambda p: role_order.get(p.role, 9))
    for party in ordered:
        role_label = {
            "PLAINTIFF": "原告",
            "DEFENDANT": "被告",
            "THIRD_PARTY": "第三人",
            "OTHER": "当事人",
        }.get(party.role, "当事人")
        lines.append(f"{role_label}：{party.name}")
        ids = party.identifiers_json or {}
        for key, label in (
            ("address", "住所地"),
            ("legal_representative", "法定代表人"),
            ("credit_code", "统一社会信用代码"),
            ("phone", "联系方式"),
        ):
            if ids.get(key):
                lines.append(f"{label}：{ids[key]}")
            else:
                lines.append(f"{label}：【待补充】")
                warnings.append(
                    WriterWarning(
                        code="MISSING_PARTY_FIELD",
                        message=f"{party.name} 缺少 {label}",
                    )
                )
        lines.append("")
    return "\n".join(lines).strip(), warnings


def _format_money(amount: float, currency: str) -> str:
    if float(amount).is_integer():
        num = f"{int(amount):,}"
    else:
        num = f"{amount:,.2f}"
    if currency.upper() == "CNY":
        return f"人民币{num}元"
    return f"{currency} {num}"


def _render_claims(payload: dict[str, Any]) -> tuple[list[ClaimLine], str]:
    claims = list(payload.get("claims") or [])
    lines: list[ClaimLine] = []
    texts: list[str] = []
    idx = 0
    has_payment = any(str(c.get("claim_type")) == "PAYMENT" for c in claims)

    for item in claims:
        idx += 1
        ctype = str(item.get("claim_type"))
        desc = str(item.get("description") or "").strip()
        amount = item.get("amount")
        currency = item.get("currency")
        cn = _cn_index(idx)
        if ctype == "PAYMENT" and amount is not None and currency:
            money = _format_money(float(amount), str(currency))
            text = f"{cn}、判令被告向原告支付{desc}{money}；"
        else:
            text = f"{cn}、{desc}；"
        if item.get("interest_start_date") or item.get("interest_rate"):
            parts = []
            if item.get("interest_start_date"):
                parts.append(f"自{item['interest_start_date']}起")
            if item.get("interest_rate"):
                parts.append(f"按{item['interest_rate']}")
            text += "".join(parts) + "计付利息；"
        lines.append(
            ClaimLine(
                claim_type=ctype,
                text=text.rstrip("；") + "。",
                amount=float(amount) if amount is not None else None,
                currency=str(currency) if currency else None,
            )
        )
        texts.append(lines[-1].text)

    if has_payment:
        idx += 1
        cn = _cn_index(idx)
        fee_text = f"{cn}、本案诉讼费用由被告承担。"
        lines.append(
            ClaimLine(claim_type="PROCEDURAL", text=fee_text, amount=None, currency=None)
        )
        texts.append(fee_text)

    section = "诉讼请求：\n" + "\n".join(texts)
    return lines, section


def _render_liability_bridge(structured: StructuredPleadingInput) -> str | None:
    if not structured.liability_bridge_required:
        return None
    if not structured.liability_facts:
        return None
    contract = "、".join(structured.contract_party_names) or "合同签约方"
    defendants = "、".join(structured.defendant_names) or "被告"
    bridge_stmts = "；".join(f.statement.strip() for f in structured.liability_facts)
    return (
        f"涉案合同由{contract}签订，本案起诉的被告为{defendants}。"
        f"根据已确认事实，{bridge_stmts.rstrip('。')}。"
        f"因此，{defendants}在本案中依法应承担服务费付款义务。"
    )


def _render_amount_narrative(structured: StructuredPleadingInput) -> str | None:
    """Synthesize lawyer-readable amount chain from confirmed facts."""
    rate_parts = [
        f.statement.strip()
        for f in structured.service_term_facts + structured.amount_facts
        if any(k in f.statement for k in ("%", "费率", "封顶", "上限", "8%", "比例", "计取"))
    ]
    base_parts = [
        f.statement.strip()
        for f in structured.amount_facts
        if any(k in f.statement for k in ("优化金额", "结算", "基数", "计算", "应付"))
    ]
    paid_parts = [f.statement.strip() for f in structured.payment_history_facts]
    outstanding_parts = [f.statement.strip() for f in structured.outstanding_facts]

    segments: list[str] = []
    if rate_parts:
        segments.append(rate_parts[0])
    for p in base_parts:
        if p not in segments:
            segments.append(p)
    for p in paid_parts:
        segments.append(p)
    for p in outstanding_parts:
        segments.append(p)

    if len(segments) < 2 and not (paid_parts and outstanding_parts):
        return None
    return " ".join(segments)


def _render_facts_ordered(
    ordered: list[StructuredFactItem],
    structured: StructuredPleadingInput,
    *,
    parties: list[PartyView] | None = None,
) -> tuple[list[FactBlock], str]:
    blocks: list[FactBlock] = []
    paragraphs: list[str] = []

    # Narrative sections — skip facts already woven into synthesized paragraphs
    amount_narrative = _render_amount_narrative(structured)
    amount_fact_keys = {
        (f.fact_key, f.fact_version)
        for bucket in (
            structured.amount_facts,
            structured.payment_history_facts,
            structured.outstanding_facts,
        )
        for f in bucket
    }
    liability_keys = {
        (f.fact_key, f.fact_version) for f in structured.liability_facts
    }

    bridge = _render_liability_bridge(structured)
    if bridge:
        paragraphs.append(bridge)
        block_id = "fact-bridge"
        if structured.liability_facts:
            lf = structured.liability_facts[0]
            blocks.append(
                FactBlock(
                    block_id=block_id,
                    text=bridge,
                    fact_refs=[
                        FactRef(fact_key=lf.fact_key, fact_version=lf.fact_version)
                    ],
                    evidence_refs=list(lf.evidence_refs),
                )
            )

    block_idx = len(blocks)
    for item in ordered:
        key = (item.fact_key, item.fact_version)
        if key in liability_keys and bridge:
            continue
        if amount_narrative and key in amount_fact_keys:
            continue
        stmt = item.statement.strip()
        if amount_narrative and stmt in amount_narrative:
            continue
        block_idx += 1
        block_id = f"fact-{block_idx:03d}"
        text = stmt
        blocks.append(
            FactBlock(
                block_id=block_id,
                text=text,
                fact_refs=[
                    FactRef(fact_key=item.fact_key, fact_version=item.fact_version)
                ],
                evidence_refs=list(item.evidence_refs),
            )
        )
        paragraphs.append(text)

    if amount_narrative:
        paragraphs.append(amount_narrative)
        block_idx += 1
        rep = next(
            (
                f
                for f in structured.amount_facts
                + structured.outstanding_facts
                + structured.payment_history_facts
            ),
            None,
        )
        if rep:
            blocks.append(
                FactBlock(
                    block_id=f"fact-{block_idx:03d}",
                    text=amount_narrative,
                    fact_refs=[
                        FactRef(fact_key=rep.fact_key, fact_version=rep.fact_version)
                    ],
                    evidence_refs=list(rep.evidence_refs),
                )
            )

    body = "事实与理由：\n" + "\n".join(paragraphs)
    _ = parties
    return blocks, body


def _render_evidence_directory(
    groups: list[MaterialEvidenceGroup],
) -> tuple[list[EvidenceDirectoryItem], str]:
    items: list[EvidenceDirectoryItem] = []
    lines: list[str] = ["证据目录", ""]
    for grp in groups:
        purpose = "；".join(dict.fromkeys(grp.proof_purposes))[:500]
        primary = grp.evidence_refs[0] if grp.evidence_refs else None
        item = EvidenceDirectoryItem(
            display_number=grp.display_number,
            title=grp.title,
            proof_purpose=purpose,
            evidence_item_id=primary.evidence_item_id if primary else grp.material_id,
            evidence_item_version=primary.evidence_item_version if primary else 1,
            material_id=grp.material_id,
            material_filename=grp.material_filename,
            merged_evidence_refs=grp.evidence_refs,
        )
        items.append(item)
        lines.append(f"{grp.display_number}. 《{grp.title}》")
        lines.append(f"   证明目的：{purpose}")
        lines.append(f"   来源：{grp.source_label}")
        lines.append("")
    return items, "\n".join(lines).strip()


def looks_like_fabricated_law_citation(text: str) -> bool:
    return bool(FABRICATED_LAW_RE.search(text))


def looks_like_invented_interest(text: str) -> bool:
    return bool(LPR_RE.search(text))
