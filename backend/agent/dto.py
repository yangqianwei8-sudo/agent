"""Agent DTOs and error codes (Phase 9)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AgentErrorCode(StrEnum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    AMBIGUOUS_TARGET = "AMBIGUOUS_TARGET"
    INVALID_WORKFLOW_STATE = "INVALID_WORKFLOW_STATE"
    HUMAN_GATE_REQUIRED = "HUMAN_GATE_REQUIRED"
    NOT_FOUND = "NOT_FOUND"
    COMMAND_REPLAY = "COMMAND_REPLAY"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    UNKNOWN_INTENT = "UNKNOWN_INTENT"
    LLM_REQUEST_FAILED = "LLM_REQUEST_FAILED"


class AgentIntent(StrEnum):
    START_CASE_WORKFLOW = "START_CASE_WORKFLOW"
    CONTINUE = "CONTINUE"
    STATUS = "STATUS"
    PAUSE = "PAUSE"
    RESUME = "RESUME"
    RETRY = "RETRY"
    ORGANIZE_EVIDENCE = "ORGANIZE_EVIDENCE"
    ACCEPT_EVIDENCE = "ACCEPT_EVIDENCE"
    EXCLUDE_EVIDENCE = "EXCLUDE_EVIDENCE"
    CONFIRM_PARTY = "CONFIRM_PARTY"
    CREATE_PARTY = "CREATE_PARTY"
    REJECT_PARTY = "REJECT_PARTY"
    AMEND_PARTY = "AMEND_PARTY"
    CONFIRM_FACT = "CONFIRM_FACT"
    REJECT_FACT = "REJECT_FACT"
    AMEND_FACT = "AMEND_FACT"
    CONFIRM_CLAIM_DIRECTION = "CONFIRM_CLAIM_DIRECTION"
    REJECT_CLAIM_DIRECTION = "REJECT_CLAIM_DIRECTION"
    AMEND_CLAIM_DIRECTION = "AMEND_CLAIM_DIRECTION"
    GENERATE_COMPLAINT = "GENERATE_COMPLAINT"
    REVIEW_DRAFT = "REVIEW_DRAFT"
    APPROVE_DRAFT = "APPROVE_DRAFT"
    SHOW_EVIDENCE = "SHOW_EVIDENCE"
    SHOW_FACTS = "SHOW_FACTS"
    SHOW_CLAIMS = "SHOW_CLAIMS"
    SHOW_DRAFT = "SHOW_DRAFT"
    UNKNOWN = "UNKNOWN"


class IntentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: AgentIntent
    targets: list[str] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 1.0


class AgentAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    label: str


class AgentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: UUID
    case_id: UUID
    message: str
    intent: AgentIntent
    workflow_status: str | None = None
    current_node: str | None = None
    current_node_label: str | None = None
    pending_count: int | None = None
    blocking_reason: str | None = None
    actions: list[AgentAction] = Field(default_factory=list)
    references: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error_code: AgentErrorCode | None = None
    command_id: UUID | None = None
    idempotent_replay: bool = False


class AgentError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: AgentErrorCode = AgentErrorCode.VALIDATION_ERROR,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
