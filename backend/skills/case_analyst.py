"""Case Analyst engine interface + deterministic stub (no fake LLM)."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Protocol

from backend.schemas.case_analyst import (
    AnalystEngineResult,
    AnalystInput,
    EvidenceRef,
    FactProposal,
)


@dataclass
class SpanSnippet:
    source_span_id: uuid.UUID
    quote: str
    page: int | None
    paragraph: int | None
    material_id: uuid.UUID


@dataclass
class EvidenceView:
    """Read-only evidence context for the analyst engine."""

    evidence_item_id: uuid.UUID
    evidence_item_version: int
    title: str
    summary: str | None
    category: str
    source_spans: list[SpanSnippet] = field(default_factory=list)


class CaseAnalystEngine(Protocol):
    def analyze(
        self,
        inp: AnalystInput,
        evidence: list[EvidenceView],
    ) -> AnalystEngineResult: ...


class DeterministicCaseAnalystStub:
    """Contract-test stub: one Fact Candidate per ACCEPTED EvidenceView.

    Does not pretend to be an LLM. Emits only evidence refs from the provided set.
    Legal conclusions go to legal_theories/issues channels, never facts.
    """

    def analyze(
        self,
        inp: AnalystInput,
        evidence: list[EvidenceView],
    ) -> AnalystEngineResult:
        _ = inp
        facts: list[FactProposal] = []
        for view in evidence:
            quote = ""
            if view.source_spans:
                quote = view.source_spans[0].quote.strip()
            statement = (view.summary or view.title or quote or "材料记载事项").strip()
            statement = statement[:500]
            facts.append(
                FactProposal(
                    proposal_id=uuid.uuid4(),
                    statement=statement,
                    fact_type=_guess_fact_type(view.category, statement),
                    supporting_evidence_refs=[
                        EvidenceRef(
                            evidence_item_id=view.evidence_item_id,
                            evidence_item_version=view.evidence_item_version,
                        )
                    ],
                    confidence=0.4,
                    analyst_reason=(
                        "Deterministic stub: propose each ACCEPTED EvidenceItem "
                        "as a Fact Candidate."
                    ),
                )
            )
        return AnalystEngineResult(facts=facts)


class ScriptedCaseAnalystEngine:
    """Test/fixture engine: returns a fixed AnalystEngineResult."""

    def __init__(self, result: AnalystEngineResult) -> None:
        self._result = result

    def analyze(
        self,
        inp: AnalystInput,
        evidence: list[EvidenceView],
    ) -> AnalystEngineResult:
        _ = inp
        _ = evidence
        return self._result.model_copy(deep=True)


# Phrases that must not enter the facts[] → propose_fact path (V1 structural firewall).
LEGAL_CONCLUSION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p)
    for p in (
        r"根本违约",
        r"构成违约",
        r"违约责任",
        r"合法有效",
        r"应承担",
        r"有权解除",
        r"诉讼时效",
        r"完全履行",
        r"依法应",
        r"被告违约",
        r"原告有权",
    )
)


def looks_like_legal_conclusion(statement: str) -> bool:
    text = statement.strip()
    return any(p.search(text) for p in LEGAL_CONCLUSION_PATTERNS)


def _guess_fact_type(category: str, statement: str) -> str:
    blob = f"{category} {statement}".lower()
    if any(k in blob for k in ("合同", "签订", "签署", "contract")):
        return "CONTRACT_SIGNING"
    if any(k in blob for k in ("付款", "支付", "payment", "金额")):
        return "PAYMENT"
    if any(k in blob for k in ("交付", "delivery", "成果")):
        return "DELIVERY"
    if any(k in blob for k in ("通知", "notice")):
        return "NOTICE"
    return "OTHER"
