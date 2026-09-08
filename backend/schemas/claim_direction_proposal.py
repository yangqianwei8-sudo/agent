"""ClaimDirection Analyst proposal schemas (Phase 7).

Domain payload still uses supporting_fact_ids = fact_key (Phase 2 freeze).
Application DTOs carry fact_key + fact_version for explicit input validation.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.domain.enums import ClaimType


class FactRef(BaseModel):
    """Explicit Fact identity — version mandatory at Application boundary."""

    model_config = ConfigDict(extra="forbid")

    fact_key: UUID
    fact_version: int = Field(ge=1)


class PartyRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    party_key: UUID


class ClaimProposalItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_type: ClaimType
    description: str = Field(min_length=1, max_length=2000)
    amount: float | None = None
    currency: str | None = None
    calculation_basis: str | None = Field(default=None, max_length=2000)
    interest_start_date: date | None = None
    interest_rate: str | None = Field(default=None, max_length=200)
    supporting_fact_refs: list[FactRef] = Field(min_length=1)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    uncertainties: list[str] = Field(default_factory=list)

    @field_validator("currency")
    @classmethod
    def currency_iso(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if len(value) != 3 or not value.isalpha():
            raise ValueError("currency must be ISO 4217 three-letter code")
        return value.upper()

    @model_validator(mode="after")
    def amount_rules(self) -> ClaimProposalItem:
        money_types = {ClaimType.PAYMENT, ClaimType.LIQUIDATED_DAMAGES}
        if self.claim_type in money_types:
            if self.amount is None:
                raise ValueError("amount is required for PAYMENT / LIQUIDATED_DAMAGES")
            if self.amount < 0:
                raise ValueError("amount must be >= 0")
            if not self.currency:
                raise ValueError("currency is required when amount is set")
        elif self.amount is not None and self.amount < 0:
            raise ValueError("amount must be >= 0")
        if self.amount is not None and not self.currency:
            raise ValueError("currency is required when amount is set")
        keys = {(r.fact_key, r.fact_version) for r in self.supporting_fact_refs}
        if len(keys) != len(self.supporting_fact_refs):
            raise ValueError("supporting_fact_refs must be unique")
        return self


class ClaimDirectionProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: UUID
    overall_strategy: str = Field(min_length=1, max_length=2000)
    claims: list[ClaimProposalItem] = Field(min_length=1, max_length=20)
    risks: list[str] = Field(default_factory=list)
    missing_confirmations: list[str] = Field(default_factory=list)


class ClaimDirectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: UUID
    confirmed_fact_refs: list[FactRef] = Field(min_length=1)
    confirmed_party_keys: list[UUID] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_refs(self) -> ClaimDirectionInput:
        fkeys = {(r.fact_key, r.fact_version) for r in self.confirmed_fact_refs}
        if len(fkeys) != len(self.confirmed_fact_refs):
            raise ValueError("confirmed_fact_refs must be unique")
        if len(set(self.confirmed_party_keys)) != len(self.confirmed_party_keys):
            raise ValueError("confirmed_party_keys must be unique")
        return self


class ClaimDirectionEngineResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposals: list[ClaimDirectionProposal] = Field(default_factory=list)
