"""Claim Direction engine interface + deterministic stub (no fake LLM)."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Protocol

from backend.domain.enums import ClaimType
from backend.schemas.claim_direction_proposal import (
    ClaimDirectionEngineResult,
    ClaimDirectionInput,
    ClaimDirectionProposal,
    ClaimProposalItem,
    FactRef,
)


@dataclass
class PartyView:
    party_key: uuid.UUID
    role: str
    name: str
    party_type: str
    version: int


@dataclass
class FactView:
    fact_key: uuid.UUID
    fact_version: int
    statement: str
    status: str
    stale: bool
    amounts_mentioned: list[float] = field(default_factory=list)


class ClaimDirectionEngine(Protocol):
    def propose(
        self,
        inp: ClaimDirectionInput,
        facts: list[FactView],
        parties: list[PartyView],
    ) -> ClaimDirectionEngineResult: ...


class DeterministicClaimDirectionStub:
    """Contract stub: one PAYMENT (or DECLARATORY) proposal from confirmed facts.

    Amount is only set when numeric values appear in Fact statements and a
    reproducible remainder can be computed; otherwise amount is omitted via
    DECLARATORY + missing_confirmations (never invents money).
    """

    def propose(
        self,
        inp: ClaimDirectionInput,
        facts: list[FactView],
        parties: list[PartyView],
    ) -> ClaimDirectionEngineResult:
        _ = parties
        refs = [
            FactRef(fact_key=f.fact_key, fact_version=f.fact_version) for f in facts
        ]
        amounts = sorted({a for f in facts for a in f.amounts_mentioned})
        missing: list[str] = []
        risks: list[str] = []

        if len(amounts) >= 2:
            total = max(amounts)
            paid = min(amounts)
            remainder = total - paid
            claim = ClaimProposalItem(
                claim_type=ClaimType.PAYMENT,
                description="请求被告支付剩余服务费",
                amount=remainder,
                currency="CNY",
                calculation_basis=(
                    f"合同总价 {total:g} - 已支付 {paid:g} = 剩余 {remainder:g}"
                ),
                interest_start_date=None,
                interest_rate=None,
                supporting_fact_refs=refs,
                confidence=0.5,
                uncertainties=[],
            )
            strategy = (
                "请求被告支付拖欠服务费；利息起算与利率待律师确认，本 stub 不主张。"
            )
        elif len(amounts) == 1:
            only = amounts[0]
            claim = ClaimProposalItem(
                claim_type=ClaimType.PAYMENT,
                description="请求被告支付合同载明款项",
                amount=only,
                currency="CNY",
                calculation_basis=f"CONFIRMED Fact 记载金额 {only:g}",
                supporting_fact_refs=refs,
                confidence=0.4,
                uncertainties=["仅单一金额记载，未分解已付/应付"],
            )
            strategy = "基于已确认事实中的金额提出付款请求方向。"
            missing.append("缺少已付款或合同总价对照事实，金额分解待律师确认")
        else:
            claim = ClaimProposalItem(
                claim_type=ClaimType.DECLARATORY,
                description="请求确认合同关系及履行相关权利义务（金额待律师确认）",
                amount=None,
                currency=None,
                calculation_basis=None,
                supporting_fact_refs=refs,
                confidence=0.3,
                uncertainties=["CONFIRMED Facts 中无可复现金额"],
            )
            strategy = "现有已确认事实不足以支持具体金额请求，先提出确认之诉方向。"
            missing.append("缺少能够支持金额主张的 CONFIRMED Fact")
            risks.append("金额依据不足，不得伪造具体金额")

        return ClaimDirectionEngineResult(
            proposals=[
                ClaimDirectionProposal(
                    proposal_id=uuid.uuid4(),
                    overall_strategy=strategy,
                    claims=[claim],
                    risks=risks,
                    missing_confirmations=missing,
                )
            ]
        )


class ScriptedClaimDirectionEngine:
    def __init__(self, result: ClaimDirectionEngineResult) -> None:
        self._result = result

    def propose(
        self,
        inp: ClaimDirectionInput,
        facts: list[FactView],
        parties: list[PartyView],
    ) -> ClaimDirectionEngineResult:
        _ = inp
        _ = facts
        _ = parties
        return self._result.model_copy(deep=True)


_AMOUNT_RE = re.compile(
    r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>万|万元)?"
)


def extract_amounts_from_text(text: str) -> list[float]:
    """Extract Arabic / 万-scale amounts from confirmed fact statements."""
    found: list[float] = []
    for match in _AMOUNT_RE.finditer(text.replace(",", "")):
        num = float(match.group("num"))
        unit = match.group("unit")
        if unit:
            num *= 10000.0
        found.append(num)
    return found
