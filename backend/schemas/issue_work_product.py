"""Issue Work Product — canonical read model for issue-centered workspace."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PositionView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position_key: str
    version: int
    side: str
    position_type: str
    source_type: str
    status: str
    statement: str
    display_label: str
    opponent_material_ref: str | None = None


class ProofTaskFactView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_key: str
    fact_version: int
    statement: str
    status: str
    role: str
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class StructuralWarningView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    description: str
    related_fact_key: str | None = None
    related_fact_version: int | None = None


class ProofTaskView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proof_task_key: str
    version: int
    description: str
    status: str
    source_type: str
    display_status: str
    support_facts: list[ProofTaskFactView] = Field(default_factory=list)
    adverse_facts: list[ProofTaskFactView] = Field(default_factory=list)
    context_facts: list[ProofTaskFactView] = Field(default_factory=list)
    structural_warnings: list[StructuralWarningView] = Field(default_factory=list)
    proof_gaps: list[dict[str, Any]] = Field(default_factory=list)


class ConflictView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conflict_id: str
    conflict_key: str
    description: str
    status: str
    source_type: str
    display_status: str
    resolution_note: str | None = None
    facts: list[dict[str, Any]] = Field(default_factory=list)


class ProofGapView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gap_id: str
    gap_key: str
    gap_type: str
    status: str
    source_type: str
    description: str
    what_exists: str | None = None
    what_is_missing: str | None = None
    why_it_matters: str | None = None
    suggested_material_types: list[str] = Field(default_factory=list)
    display_status: str
    proof_task_key: str | None = None


class LegalAnalysisView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    legal_theories: list[dict[str, Any]] = Field(default_factory=list)
    favorable_factors: list[str] = Field(default_factory=list)
    adverse_factors: list[str] = Field(default_factory=list)
    unknown_factors: list[str] = Field(default_factory=list)


class LawyerAssessmentView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assessment_key: str
    version: int
    content: str
    status: str
    display_status: str
    is_current: bool


class EvolutionEventView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: str
    timestamp: str | None = None
    summary: str
    actor_hint: str | None = None


class IssueWorkProduct(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_key: str
    issue_version: int
    statement: str
    status: str
    source_type: str
    display_status: str
    positions: dict[str, list[PositionView]] = Field(default_factory=dict)
    proof_tasks: list[ProofTaskView] = Field(default_factory=list)
    conflicts: list[ConflictView] = Field(default_factory=list)
    legal_analysis: LegalAnalysisView = Field(default_factory=LegalAnalysisView)
    lawyer_assessment: LawyerAssessmentView | None = None
    proof_state: str
    proof_state_label: str
    lawyer_judgment_state: str
    lawyer_judgment_state_label: str
    next_action: str | None = None
    evolution: list[EvolutionEventView] = Field(default_factory=list)
    proof_gaps: list[ProofGapView] = Field(default_factory=list)
    proof_gap_count: int = 0
    conflict_count: int = 0
    proof_task_count: int = 0
    open_proof_gap_count: int = 0


class CaseIssueWorkProduct(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    confirmed_issues: list[IssueWorkProduct] = Field(default_factory=list)
    candidate_issues: list[IssueWorkProduct] = Field(default_factory=list)
    proof_state_counts: dict[str, int] = Field(default_factory=dict)
    assessment_count: int = 0
    top_blocking_issue: IssueWorkProduct | None = None
    recommended_next_issue: IssueWorkProduct | None = None


class LitigationPlanView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    confirmed_issues: list[dict[str, Any]] = Field(default_factory=list)
    claims: list[dict[str, Any]] = Field(default_factory=list)
    readiness_status: str
    readiness_display: str
    assessments: list[dict[str, Any]] = Field(default_factory=list)
