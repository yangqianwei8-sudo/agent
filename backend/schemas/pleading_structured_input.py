"""V2-P4 — PleadingStructuredInput production read model."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.schemas.pleading_quality import StructuredPleadingInput


class SourceSpanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_span_id: str
    material_id: str
    page: int | None = None
    paragraph: int | None = None
    character_start: int | None = None
    character_end: int | None = None
    quote: str
    quote_hash: str | None = None


class PartyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    party_key: str
    version: int
    role: str
    name: str
    party_type: str


class ClaimCentricView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    basis_issue_refs: list[str] = Field(default_factory=list)
    limitation_issue_refs: list[str] = Field(default_factory=list)
    context_issue_refs: list[str] = Field(default_factory=list)
    basis_fact_refs: list[str] = Field(default_factory=list)
    amount_basis_fact_refs: list[str] = Field(default_factory=list)
    limitation_fact_refs: list[str] = Field(default_factory=list)
    supporting_evidence_refs: list[str] = Field(default_factory=list)


class ClaimInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_key: str
    claim_version: int
    claim_type: str
    title: str
    statement: str
    amount: float | None = None
    currency: str | None = None
    source_type: str
    confirm_decision_id: str | None = None
    claim_centric: ClaimCentricView = Field(default_factory=ClaimCentricView)


class IssueInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_key: str
    issue_version: int
    statement: str
    source_type: str
    lawyer_confirmation_state: str


class FactInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_key: str
    fact_version: int
    statement: str
    importance: str | None = None


class EvidenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_item_id: str
    evidence_item_version: int
    number: str
    title: str
    category: str
    source_spans: list[SourceSpanInput] = Field(default_factory=list)


class ClaimIssueRelation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_key: str
    claim_version: int
    issue_key: str
    issue_version: int
    role: str
    status: str


class ClaimFactRelation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_key: str
    claim_version: int
    fact_key: str
    fact_version: int
    role: str
    status: str


class IssueFactRelation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_key: str
    issue_version: int
    fact_key: str
    fact_version: int
    role: str
    status: str


class FactEvidenceRelation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_key: str
    fact_version: int
    evidence_item_id: str
    evidence_item_version: int
    role: str
    status: str
    source_span_id: str | None = None


class SnapshotMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generated_at: datetime
    party_refs: list[str] = Field(default_factory=list)
    claim_refs: list[str] = Field(default_factory=list)
    issue_refs: list[str] = Field(default_factory=list)
    fact_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    confirmation_set_hash: str
    claim_source: Literal["CLAIM_DOMAIN", "LEGACY_CLAIM_DIRECTION"] = "CLAIM_DOMAIN"
    legacy_claim_direction_ref: dict[str, Any] | None = None


class PleadingStructuredInput(BaseModel):
    """Production SSOT for Pleading Writer — explicit versions only."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    parties: list[PartyInput] = Field(default_factory=list)
    claims: list[ClaimInput] = Field(default_factory=list)
    issues: list[IssueInput] = Field(default_factory=list)
    facts: list[FactInput] = Field(default_factory=list)
    evidence: list[EvidenceInput] = Field(default_factory=list)
    claim_issue_relations: list[ClaimIssueRelation] = Field(default_factory=list)
    claim_fact_relations: list[ClaimFactRelation] = Field(default_factory=list)
    issue_fact_relations: list[IssueFactRelation] = Field(default_factory=list)
    fact_evidence_relations: list[FactEvidenceRelation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    snapshot_meta: SnapshotMeta
    writer_structured: StructuredPleadingInput
