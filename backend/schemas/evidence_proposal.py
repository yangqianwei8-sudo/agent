"""Evidence Organizer proposal schemas (Phase 5)."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Soft vocabulary — free string in DB; these are suggested labels only.
EVIDENCE_CATEGORIES = frozenset(
    {
        "CONTRACT",
        "PAYMENT",
        "COMMUNICATION",
        "DELIVERY",
        "ACCEPTANCE",
        "NOTICE",
        "INVOICE",
        "COMPANY_RECORD",
        "LITIGATION_MATERIAL",
        "OTHER",
    }
)


class EvidenceItemProposal(BaseModel):
    """Organizer suggestion — never auto-ACCEPTED."""

    model_config = ConfigDict(extra="forbid")

    proposal_id: UUID
    title: str = Field(min_length=1, max_length=500)
    summary: str | None = Field(default=None, max_length=5000)
    category: str = Field(min_length=1, max_length=100)
    source_span_ids: list[UUID] = Field(min_length=1)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    organizer_reason: str = Field(min_length=1, max_length=2000)

    @field_validator("source_span_ids")
    @classmethod
    def unique_spans(cls, value: list[UUID]) -> list[UUID]:
        if len(value) != len(set(value)):
            raise ValueError("source_span_ids must be unique")
        return value

    @field_validator("category")
    @classmethod
    def category_nonempty(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("category required")
        return cleaned


class OrganizerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: UUID
    extracted_content_ids: list[UUID] = Field(min_length=1)


class OrganizerEngineResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposals: list[EvidenceItemProposal] = Field(default_factory=list)
