"""Pleading readiness evaluation DTOs."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ReadinessStatus(StrEnum):
    READY = "READY"
    NOT_READY = "NOT_READY"


class ReadinessIssueCode(StrEnum):
    PLAINTIFF_NOT_CONFIRMED = "PLAINTIFF_NOT_CONFIRMED"
    DEFENDANT_NOT_CONFIRMED = "DEFENDANT_NOT_CONFIRMED"
    DEFENDANT_LIABILITY_BASIS_MISSING = "DEFENDANT_LIABILITY_BASIS_MISSING"
    PERFORMANCE_NOT_ESTABLISHED = "PERFORMANCE_NOT_ESTABLISHED"
    PAYMENT_CONDITION_NOT_ESTABLISHED = "PAYMENT_CONDITION_NOT_ESTABLISHED"
    PAYMENT_TERM_UNCLEAR = "PAYMENT_TERM_UNCLEAR"
    CLAIM_AMOUNT_NOT_PROVEN = "CLAIM_AMOUNT_NOT_PROVEN"
    DEBT_DUE_STATUS_UNCLEAR = "DEBT_DUE_STATUS_UNCLEAR"
    JURISDICTION_NOT_FINALIZED = "JURISDICTION_NOT_FINALIZED"
    MATERIAL_FACT_CONFLICT = "MATERIAL_FACT_CONFLICT"
    CLAIM_DIRECTION_NOT_CONFIRMED = "CLAIM_DIRECTION_NOT_CONFIRMED"
    CLAIM_FACT_INCONSISTENT = "CLAIM_FACT_INCONSISTENT"
    KEY_FACT_PROVENANCE_INVALID = "KEY_FACT_PROVENANCE_INVALID"
    PENDING_MATERIAL_DISCLOSED = "PENDING_MATERIAL_DISCLOSED"
    NO_CONFIRMED_ISSUE = "NO_CONFIRMED_ISSUE"
    ISSUE_ONLY_CANDIDATE = "ISSUE_ONLY_CANDIDATE"
    ISSUE_CRITICAL_GAP = "ISSUE_CRITICAL_GAP"
    NO_CONFIRMED_CLAIM = "NO_CONFIRMED_CLAIM"
    CLAIM_ONLY_CANDIDATE = "CLAIM_ONLY_CANDIDATE"
    CLAIM_WITHOUT_BASIS = "CLAIM_WITHOUT_BASIS"
    CLAIM_STALE = "CLAIM_STALE"


class ReadinessIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    severity: str  # BLOCKER | WARNING
    message: str
    suggested_action: str | None = None
    related_fact_keys: list[str] = Field(default_factory=list)
    related_party_keys: list[str] = Field(default_factory=list)


class PleadingReadinessResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ReadinessStatus
    blocking_issues: list[ReadinessIssue] = Field(default_factory=list)
    warnings: list[ReadinessIssue] = Field(default_factory=list)
    confirmed_strengths: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    suggested_next_actions: list[str] = Field(default_factory=list)
    checked_at: datetime
    input_refs: dict[str, Any] = Field(default_factory=dict)
    display_status: str = "尚未具备"  # 已具备 | 尚未具备

    @property
    def is_ready(self) -> bool:
        return self.status == ReadinessStatus.READY


class PleadingNotReadyError(Exception):
    """Raised when Writer is invoked while readiness is NOT_READY."""

    def __init__(self, readiness: PleadingReadinessResult) -> None:
        self.readiness = readiness
        blockers = "; ".join(i.message for i in readiness.blocking_issues[:5])
        super().__init__(blockers or "案件尚未具备生成起诉状条件")
        self.message = str(self)
        self.code = "PLEADING_NOT_READY"
