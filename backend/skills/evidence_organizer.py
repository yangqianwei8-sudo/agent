"""Evidence Organizer engine interface + deterministic stub (no fake LLM)."""

from __future__ import annotations

import uuid
from typing import Protocol

from backend.schemas.evidence_proposal import (
    EvidenceItemProposal,
    OrganizerEngineResult,
    OrganizerInput,
)


class SpanView:
    """Read-only span context passed to the engine (already scoped & validated)."""

    __slots__ = ("span_id", "quote", "page", "paragraph", "material_id", "ec_id")

    def __init__(
        self,
        *,
        span_id: uuid.UUID,
        quote: str,
        page: int | None,
        paragraph: int | None,
        material_id: uuid.UUID,
        ec_id: uuid.UUID,
    ) -> None:
        self.span_id = span_id
        self.quote = quote
        self.page = page
        self.paragraph = paragraph
        self.material_id = material_id
        self.ec_id = ec_id


class OrganizerEngine(Protocol):
    def organize(
        self,
        inp: OrganizerInput,
        spans: list[SpanView],
    ) -> OrganizerEngineResult: ...


class DeterministicOrganizerStub:
    """Contract-test stub: one PENDING proposal per SourceSpan.

    Does not pretend to be an LLM. Emits only source_span_ids from the provided set.
    """

    def organize(
        self,
        inp: OrganizerInput,
        spans: list[SpanView],
    ) -> OrganizerEngineResult:
        _ = inp
        proposals: list[EvidenceItemProposal] = []
        for span in spans:
            quote = span.quote.strip()
            if not quote:
                continue
            title = quote[:80] + ("…" if len(quote) > 80 else "")
            category = _guess_category(quote)
            proposals.append(
                EvidenceItemProposal(
                    proposal_id=uuid.uuid4(),
                    title=title,
                    summary=quote[:500],
                    category=category,
                    source_span_ids=[span.span_id],
                    confidence=0.4,
                    organizer_reason=(
                        "Deterministic stub: propose each SourceSpan as a PENDING evidence item."
                    ),
                )
            )
        return OrganizerEngineResult(proposals=proposals)


class ScriptedOrganizerEngine:
    """Test/fixture engine: returns a fixed proposal list (may include invalid ones)."""

    def __init__(self, proposals: list[EvidenceItemProposal]) -> None:
        self._proposals = list(proposals)

    def organize(
        self,
        inp: OrganizerInput,
        spans: list[SpanView],
    ) -> OrganizerEngineResult:
        _ = inp
        _ = spans
        return OrganizerEngineResult(proposals=list(self._proposals))


def _guess_category(quote: str) -> str:
    q = quote.lower()
    if any(k in q for k in ("合同", "contract", "协议")):
        return "CONTRACT"
    if any(k in q for k in ("付款", "支付", "payment", "fee", "金额")):
        return "PAYMENT"
    if any(k in q for k in ("通知", "notice", "催告")):
        return "NOTICE"
    if any(k in q for k in ("交付", "delivery", "图纸", "design")):
        return "DELIVERY"
    return "OTHER"
