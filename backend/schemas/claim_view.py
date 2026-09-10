"""Claim read model DTOs — V2-P3 relief domain."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ClaimIssueRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_key: str
    issue_version: int
    statement: str
    status: str
    role: str
    link_id: str | None = None


class ClaimFactRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_key: str
    fact_version: int
    statement: str
    status: str
    role: str
    link_id: str | None = None


class ClaimViewItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_key: str
    claim_version: int
    claim_type: str
    title: str
    statement: str
    amount: float | None = None
    currency: str | None = None
    amount_is_suggested: bool = False
    status: str
    source_type: str
    lawyer_confirmation_state: str
    stale_state: dict[str, Any] = Field(default_factory=dict)
    basis_issues: list[ClaimIssueRef] = Field(default_factory=list)
    limitation_issues: list[ClaimIssueRef] = Field(default_factory=list)
    context_issues: list[ClaimIssueRef] = Field(default_factory=list)
    basis_facts: list[ClaimFactRef] = Field(default_factory=list)
    amount_basis_facts: list[ClaimFactRef] = Field(default_factory=list)
    limitations: list[ClaimFactRef] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ClaimsView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    items: list[ClaimViewItem] = Field(default_factory=list)
    confirmed_count: int = 0
    candidate_count: int = 0
