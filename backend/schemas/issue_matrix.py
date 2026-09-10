"""Issue Matrix V1 — read model for lawyer-facing dispute analysis."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MatrixFactRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_key: str
    fact_version: int
    statement: str
    status: str
    role: str
    link_id: str | None = None
    explanation: str | None = None


class MatrixEvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_item_id: str
    evidence_item_version: int
    title: str
    acceptance: str
    role: str
    link_id: str | None = None
    explanation: str | None = None


class GapItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str  # FACT_GAP | EVIDENCE_GAP
    issue_key: str
    issue_version: int
    related_fact_key: str | None = None
    related_fact_version: int | None = None
    description: str


class IssueMatrixItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_key: str
    issue_version: int
    statement: str
    status: str
    source_type: str
    order_index: int
    parent_issue_key: str | None = None
    supporting_facts: list[MatrixFactRef] = Field(default_factory=list)
    adverse_facts: list[MatrixFactRef] = Field(default_factory=list)
    context_facts: list[MatrixFactRef] = Field(default_factory=list)
    supporting_evidence: list[MatrixEvidenceRef] = Field(default_factory=list)
    adverse_evidence: list[MatrixEvidenceRef] = Field(default_factory=list)
    context_evidence: list[MatrixEvidenceRef] = Field(default_factory=list)
    fact_gaps: list[GapItem] = Field(default_factory=list)
    evidence_gaps: list[GapItem] = Field(default_factory=list)
    lawyer_confirmation_state: str
    stale_state: dict[str, Any] = Field(default_factory=dict)


class IssueMatrixView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    items: list[IssueMatrixItem] = Field(default_factory=list)
    aggregate_gaps: list[GapItem] = Field(default_factory=list)
    confirmed_issue_count: int = 0
    candidate_issue_count: int = 0
    legal_theories: list[dict[str, Any]] = Field(default_factory=list)
