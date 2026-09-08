"""Pleading Writer engine + deterministic stub (no fake LLM)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from backend.schemas.case_analyst import EvidenceRef
from backend.schemas.claim_direction_proposal import FactRef
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
    ) -> PleadingWriterEngineResult: ...


class DeterministicPleadingWriterStub:
    """Contract stub: renders confirmed inputs only; invents nothing."""

    def write(
        self,
        inp: PleadingWriterInput,
        *,
        parties: list[PartyView],
        facts: list[ConfirmedFactView],
        claim: ClaimDirectionView,
        evidence: list[AcceptedEvidenceView],
    ) -> PleadingWriterEngineResult:
        warnings = [
            WriterWarning(
                code="CLAIM_FACT_VERSION_PROVENANCE_GAP",
                message=(
                    "ClaimDirection.payload.supporting_fact_ids stores fact_key only; "
                    "Fact version is validated and audited at Writer Application layer "
                    "for this run, but ClaimDirection Domain does not natively pin "
                    "fact_version."
                ),
            ),
            WriterWarning(
                code="MISSING_COURT",
                message="管辖法院未由律师确认，使用占位标记。",
            ),
        ]

        parties_section, party_warnings = _render_parties(parties)
        warnings.extend(party_warnings)

        claim_lines, claims_section = _render_claims(claim.payload)
        fact_blocks, facts_section = _render_facts(facts)
        evidence_dir, evidence_section = _render_evidence_directory(evidence, facts)

        used_facts = [
            FactRef(fact_key=f.fact_key, fact_version=f.fact_version) for f in facts
        ]
        used_evidence = [
            EvidenceRef(
                evidence_item_id=e.evidence_item_id,
                evidence_item_version=e.evidence_item_version,
            )
            for e in evidence
        ]

        return PleadingWriterEngineResult(
            title="民事起诉状",
            parties_section=parties_section,
            claims=claim_lines,
            claims_section=claims_section,
            fact_blocks=fact_blocks,
            facts_and_reasons_section=facts_section,
            evidence_directory=evidence_dir,
            evidence_section=evidence_section,
            court_section="【待律师确认：管辖法院】",
            signature_section=(
                "此致\n【待律师确认：管辖法院】\n\n"
                "起诉人：【待律师补充：起诉人签署】\n"
                "日期：【待律师补充：起诉日期】"
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
    ) -> PleadingWriterEngineResult:
        _ = inp
        _ = parties
        _ = facts
        _ = claim
        _ = evidence
        return self._result.model_copy(deep=True)


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
        type_label = "（法人）" if party.party_type == "ORG" else "（自然人）"
        lines.append(f"{role_label}{type_label}：{party.name}")
        ids = party.identifiers_json or {}
        for key, label in (
            ("credit_code", "统一社会信用代码"),
            ("address", "住所地"),
            ("legal_representative", "法定代表人"),
            ("phone", "联系方式"),
        ):
            if ids.get(key):
                lines.append(f"{label}：{ids[key]}")
            else:
                lines.append(f"{label}：【待律师补充：{label}】")
                warnings.append(
                    WriterWarning(
                        code="MISSING_PARTY_FIELD",
                        message=f"{party.name} 缺少字段 {label}",
                    )
                )
        lines.append("")
    return "\n".join(lines).strip(), warnings


def _format_money(amount: float, currency: str) -> str:
    # Exact numeric form — no “约”“大约”
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
    for i, item in enumerate(claims, start=1):
        ctype = str(item.get("claim_type"))
        desc = str(item.get("description") or "").strip()
        amount = item.get("amount")
        currency = item.get("currency")
        if amount is not None and currency:
            money = _format_money(float(amount), str(currency))
            text = f"{i}. 请求判令被告向原告支付{desc}，金额为{money}。"
            if item.get("calculation_basis"):
                text += f"（计算依据：{item['calculation_basis']}）"
        else:
            text = f"{i}. {desc}"
        # Interest: only if confirmed in ClaimDirection
        if item.get("interest_start_date") or item.get("interest_rate"):
            parts = []
            if item.get("interest_start_date"):
                parts.append(f"自{item['interest_start_date']}起")
            if item.get("interest_rate"):
                parts.append(f"按{item['interest_rate']}")
            text += "；" + "".join(parts) + "计付利息。"
        lines.append(
            ClaimLine(
                claim_type=ctype,
                text=text,
                amount=float(amount) if amount is not None else None,
                currency=str(currency) if currency else None,
            )
        )
        texts.append(text)
    section = "诉讼请求：\n" + "\n".join(texts)
    return lines, section


def _render_facts(
    facts: list[ConfirmedFactView],
) -> tuple[list[FactBlock], str]:
    blocks: list[FactBlock] = []
    paragraphs: list[str] = []
    for i, fact in enumerate(facts, start=1):
        block_id = f"fact-{i:03d}"
        # Light wording polish without changing semantics: wrap confirmed statement.
        text = fact.statement.strip()
        blocks.append(
            FactBlock(
                block_id=block_id,
                text=text,
                fact_refs=[
                    FactRef(fact_key=fact.fact_key, fact_version=fact.fact_version)
                ],
                evidence_refs=list(fact.evidence_refs),
            )
        )
        paragraphs.append(text)
    body = "事实与理由：\n" + "\n".join(paragraphs)
    body += "\n\n依据相关法律规定，原告特提起诉讼，请求判如所请。"
    return blocks, body


def _render_evidence_directory(
    evidence: list[AcceptedEvidenceView],
    facts: list[ConfirmedFactView],
) -> tuple[list[EvidenceDirectoryItem], str]:
    purpose_by_eid: dict[tuple[UUID, int], str] = {}
    for fact in facts:
        for ref in fact.evidence_refs:
            key = (ref.evidence_item_id, ref.evidence_item_version)
            purpose_by_eid.setdefault(key, fact.statement[:80])

    items: list[EvidenceDirectoryItem] = []
    lines: list[str] = ["证据目录："]
    for ev in evidence:
        purpose = purpose_by_eid.get(
            (ev.evidence_item_id, ev.evidence_item_version),
            ev.summary or ev.title,
        )
        item = EvidenceDirectoryItem(
            display_number=ev.number,
            title=ev.title,
            proof_purpose=purpose,
            evidence_item_id=ev.evidence_item_id,
            evidence_item_version=ev.evidence_item_version,
        )
        items.append(item)
        lines.append(
            f"证据{ev.number}：{ev.title}；证明目的：{purpose}"
        )
    return items, "\n".join(lines)


def looks_like_fabricated_law_citation(text: str) -> bool:
    return bool(FABRICATED_LAW_RE.search(text))


def looks_like_invented_interest(text: str) -> bool:
    return bool(LPR_RE.search(text))
