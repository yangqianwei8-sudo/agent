"""Pleading Quality V1 — structured writer input & validation DTOs."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from backend.schemas.case_analyst import EvidenceRef


class StructuredFactItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_key: UUID
    fact_version: int
    statement: str
    category: str
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)


class MaterialEvidenceGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    material_id: UUID
    material_filename: str
    display_number: str
    title: str
    proof_purposes: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    source_label: str = "原件/扫描件"


class StructuredPleadingInput(BaseModel):
    """Semantic partitions for Writer — not a flat fact dump."""

    model_config = ConfigDict(extra="forbid")

    parties: list[dict[str, Any]] = Field(default_factory=list)
    claims_payload: dict[str, Any] = Field(default_factory=dict)
    contract_facts: list[StructuredFactItem] = Field(default_factory=list)
    service_term_facts: list[StructuredFactItem] = Field(default_factory=list)
    performance_facts: list[StructuredFactItem] = Field(default_factory=list)
    acceptance_facts: list[StructuredFactItem] = Field(default_factory=list)
    liability_facts: list[StructuredFactItem] = Field(default_factory=list)
    amount_facts: list[StructuredFactItem] = Field(default_factory=list)
    payment_history_facts: list[StructuredFactItem] = Field(default_factory=list)
    outstanding_facts: list[StructuredFactItem] = Field(default_factory=list)
    payment_term_facts: list[StructuredFactItem] = Field(default_factory=list)
    due_facts: list[StructuredFactItem] = Field(default_factory=list)
    demand_facts: list[StructuredFactItem] = Field(default_factory=list)
    jurisdiction_facts: list[StructuredFactItem] = Field(default_factory=list)
    background_facts: list[StructuredFactItem] = Field(default_factory=list)
    evidence_directory: list[MaterialEvidenceGroup] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    contract_party_names: list[str] = Field(default_factory=list)
    defendant_names: list[str] = Field(default_factory=list)
    liability_bridge_required: bool = False
    allowed_amounts: list[float] = Field(default_factory=list)
    allowed_party_names: list[str] = Field(default_factory=list)
    allowed_numbers: list[str] = Field(default_factory=list)
    finalized_court_name: str | None = None


class ValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    severity: str = "ERROR"  # ERROR | WARNING


class PleadingDraftValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    issues: list[ValidationIssue] = Field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "ERROR"]


class PleadingDraftValidationError(Exception):
    """Raised when draft fails quality validation after optional repair."""

    def __init__(self, result: PleadingDraftValidationResult) -> None:
        self.result = result
        msg = "; ".join(i.message for i in result.errors[:5])
        super().__init__(msg or "起诉状质量校验未通过")
        self.message = str(self)
        self.code = "PLEADING_DRAFT_VALIDATION_FAILED"
