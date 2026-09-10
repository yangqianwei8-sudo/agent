"""Pleading Writer schemas (Phase 8) — civil complaint DRAFT only."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.schemas.case_analyst import EvidenceRef
from backend.schemas.claim_direction_proposal import FactRef


class ClaimDirectionRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_direction_key: UUID
    claim_direction_version: int = Field(ge=1)


class PleadingWriterInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: UUID
    claim_direction_ref: ClaimDirectionRef
    confirmed_fact_refs: list[FactRef] = Field(min_length=1)
    accepted_evidence_refs: list[EvidenceRef] = Field(min_length=1)
    confirmed_party_keys: list[UUID] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_refs(self) -> PleadingWriterInput:
        fkeys = {(r.fact_key, r.fact_version) for r in self.confirmed_fact_refs}
        if len(fkeys) != len(self.confirmed_fact_refs):
            raise ValueError("confirmed_fact_refs must be unique")
        ekeys = {
            (r.evidence_item_id, r.evidence_item_version)
            for r in self.accepted_evidence_refs
        }
        if len(ekeys) != len(self.accepted_evidence_refs):
            raise ValueError("accepted_evidence_refs must be unique")
        return self


class FactBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    block_id: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=8000)
    fact_refs: list[FactRef] = Field(min_length=1)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class ClaimLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_type: str
    text: str = Field(min_length=1, max_length=2000)
    amount: float | None = None
    currency: str | None = None


class EvidenceDirectoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_number: str
    title: str
    proof_purpose: str
    evidence_item_id: UUID
    evidence_item_version: int
    material_id: UUID | None = None
    material_filename: str | None = None
    merged_evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class WriterWarning(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str


class PleadingWriterEngineResult(BaseModel):
    """Structured Writer output — never a free-form LLM dump alone."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="民事起诉状", min_length=1, max_length=200)
    parties_section: str = Field(min_length=1, max_length=20000)
    claims: list[ClaimLine] = Field(min_length=1, max_length=20)
    claims_section: str = Field(min_length=1, max_length=20000)
    fact_blocks: list[FactBlock] = Field(min_length=1)
    facts_and_reasons_section: str = Field(min_length=1, max_length=50000)
    evidence_directory: list[EvidenceDirectoryItem] = Field(default_factory=list)
    evidence_section: str = Field(default="", max_length=20000)
    court_section: str = Field(
        default="【待律师确认：管辖法院】", min_length=1, max_length=500
    )
    signature_section: str = Field(min_length=1, max_length=2000)
    warnings: list[WriterWarning] = Field(default_factory=list)
    used_fact_refs: list[FactRef] = Field(default_factory=list)
    used_evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    used_claim_direction_ref: ClaimDirectionRef | None = None

    @field_validator("court_section")
    @classmethod
    def court_not_guessed(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("court_section required")
        return cleaned
