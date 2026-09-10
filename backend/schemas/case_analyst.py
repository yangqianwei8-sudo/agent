"""Case Analyst proposal schemas (Phase 6)."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

FACT_TYPES = frozenset(
    {
        "PARTY_IDENTITY",
        "CONTRACT_SIGNING",
        "CONTRACT_TERM",
        "PAYMENT_OBLIGATION",
        "PAYMENT",
        "DELIVERY",
        "ACCEPTANCE",
        "NOTICE",
        "TERMINATION_NOTICE",
        "COMMUNICATION",
        "INVOICE",
        "PROJECT_EVENT",
        "LITIGATION_EVENT",
        "OTHER",
    }
)

TIME_PRECISIONS = frozenset({"YEAR", "MONTH", "DAY", "DATETIME", "UNKNOWN"})


class EvidenceRef(BaseModel):
    """Pinned EvidenceItem identity — version is mandatory."""

    model_config = ConfigDict(extra="forbid")

    evidence_item_id: UUID
    evidence_item_version: int = Field(ge=1)


class FactProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: UUID
    fact_key: UUID | None = None
    statement: str = Field(min_length=1, max_length=5000)
    fact_type: str = Field(default="OTHER", min_length=1, max_length=64)
    occurred_at: date | datetime | None = None
    precision: str | None = Field(default=None, max_length=32)
    supporting_evidence_refs: list[EvidenceRef] = Field(min_length=1)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    analyst_reason: str = Field(min_length=1, max_length=2000)
    uncertainties: list[str] = Field(default_factory=list)

    @field_validator("fact_type")
    @classmethod
    def normalize_fact_type(cls, value: str) -> str:
        cleaned = value.strip().upper()
        if not cleaned:
            raise ValueError("fact_type required")
        return cleaned

    @field_validator("precision")
    @classmethod
    def normalize_precision(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().upper()
        if cleaned and cleaned not in TIME_PRECISIONS:
            raise ValueError(f"invalid precision: {cleaned}")
        return cleaned or None

    @field_validator("supporting_evidence_refs")
    @classmethod
    def unique_refs(cls, value: list[EvidenceRef]) -> list[EvidenceRef]:
        keys = {(r.evidence_item_id, r.evidence_item_version) for r in value}
        if len(keys) != len(value):
            raise ValueError("supporting_evidence_refs must be unique")
        return value


class IssueLinkFactProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_key: UUID
    fact_version: int = Field(ge=1)
    role: str = Field(pattern=r"^(SUPPORT|ADVERSE|CONTEXT)$")


class IssueLinkEvidenceProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_item_id: UUID
    evidence_item_version: int = Field(ge=1)
    role: str = Field(pattern=r"^(SUPPORT|ADVERSE|CONTEXT)$")
    explanation: str | None = Field(default=None, max_length=2000)


class IssueProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: UUID
    statement: str = Field(min_length=1, max_length=5000)
    related_evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    fact_link_proposals: list[IssueLinkFactProposal] = Field(default_factory=list)
    evidence_link_proposals: list[IssueLinkEvidenceProposal] = Field(default_factory=list)
    analyst_reason: str = Field(default="", max_length=2000)


class LegalTheoryFactRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_key: UUID
    fact_version: int = Field(ge=1)


class LegalTheoryProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: UUID
    theory_summary: str = Field(min_length=1, max_length=5000)
    related_evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    supporting_fact_refs: list[LegalTheoryFactRef] = Field(default_factory=list)
    analyst_reason: str = Field(default="", max_length=2000)


class ConflictItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(default="EVIDENCE_CONFLICT", min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=5000)
    evidence_refs: list[EvidenceRef] = Field(min_length=1)


class MissingEvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, max_length=5000)
    reason: str = Field(min_length=1, max_length=5000)
    related_evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class AnalystInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: UUID
    accepted_evidence_refs: list[EvidenceRef] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_input_refs(self) -> AnalystInput:
        keys = {
            (r.evidence_item_id, r.evidence_item_version)
            for r in self.accepted_evidence_refs
        }
        if len(keys) != len(self.accepted_evidence_refs):
            raise ValueError("accepted_evidence_refs must be unique")
        return self


class ClaimLinkIssueProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_key: UUID
    issue_version: int = Field(ge=1)
    role: str = Field(pattern=r"^(BASIS|LIMITATION|CONTEXT)$")


class ClaimLinkFactProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_key: UUID
    fact_version: int = Field(ge=1)
    role: str = Field(pattern=r"^(BASIS|AMOUNT_BASIS|LIMITATION|CONTEXT)$")


class ClaimProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: UUID
    claim_type: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=500)
    statement: str = Field(min_length=1, max_length=5000)
    amount: float | None = None
    currency: str | None = None
    issue_link_proposals: list[ClaimLinkIssueProposal] = Field(default_factory=list)
    fact_link_proposals: list[ClaimLinkFactProposal] = Field(default_factory=list)
    analyst_reason: str = Field(default="", max_length=2000)


class AnalystEngineResult(BaseModel):
    """Structured analyst channels — only facts[] may enter propose_fact."""

    model_config = ConfigDict(extra="forbid")

    facts: list[FactProposal] = Field(default_factory=list)
    issues: list[IssueProposal] = Field(default_factory=list)
    claims: list[ClaimProposal] = Field(default_factory=list)
    legal_theories: list[LegalTheoryProposal] = Field(default_factory=list)
    conflicts: list[ConflictItem] = Field(default_factory=list)
    missing_evidence: list[MissingEvidenceItem] = Field(default_factory=list)
