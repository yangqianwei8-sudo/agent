"""Case Work Product V1 — read-only projection DTOs for lawyer workspace."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WorkActionButton(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    action_type: str
    target: str | None = None
    variant: str = "primary"  # primary | ghost | danger


class WorkTodoItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    todo_type: str
    title: str
    summary: str
    reason: str | None = None
    actions: list[WorkActionButton] = Field(default_factory=list)
    entity_ref: dict[str, Any] = Field(default_factory=dict)
    priority: int = 100


class WorkStageView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage_key: str
    stage_label: str
    stage_status: str  # not_started | in_progress | waiting_lawyer | completed
    stage_status_label: str
    completion_hint: str | None = None
    workflow_node: str | None = None


class NextActionView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    description: str
    action_type: str | None = None
    action_target: str | None = None
    section_anchor: str | None = None


class CaseSummaryView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    case_no_internal: str | None = None
    plaintiff: str | None = None
    defendant: str | None = None
    goal_summary: str | None = None
    updated_at: str | None = None


class TodoSummaryView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_count: int
    items: list[WorkTodoItem] = Field(default_factory=list)


class TimelineEventView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    occurred_at: str
    event_type: str
    summary: str


class CaseWorkProductView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_summary: CaseSummaryView
    stage: WorkStageView
    next_action: NextActionView
    todo_summary: TodoSummaryView
    timeline: list[TimelineEventView] = Field(default_factory=list)
    resume: dict[str, Any] = Field(default_factory=dict)
