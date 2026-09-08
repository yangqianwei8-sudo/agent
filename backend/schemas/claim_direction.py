"""ClaimDirection payload schema (blueprint-frozen)."""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.domain.enums import ClaimType
from backend.domain.errors import ValidationError


class ClaimItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_type: ClaimType
    description: str = Field(min_length=1, max_length=2000)
    amount: float | None = None
    currency: str | None = None
    calculation_basis: str | None = Field(default=None, max_length=2000)
    interest_start_date: date | None = None
    interest_rate: str | None = Field(default=None, max_length=200)
    supporting_fact_ids: list[UUID] = Field(default_factory=list)

    @field_validator("currency")
    @classmethod
    def currency_iso(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if len(value) != 3 or not value.isalpha():
            raise ValueError("currency must be ISO 4217 three-letter code")
        return value.upper()

    @model_validator(mode="after")
    def amount_rules(self) -> ClaimItem:
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
        return self


class ClaimDirectionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overall_strategy: str = Field(min_length=1, max_length=2000)
    claims: list[ClaimItem] = Field(min_length=1, max_length=20)


def validate_claim_direction_payload(
    payload: dict[str, Any] | ClaimDirectionPayload,
) -> dict[str, Any]:
    try:
        if isinstance(payload, ClaimDirectionPayload):
            model = payload
        else:
            model = ClaimDirectionPayload.model_validate(payload)
    except Exception as exc:  # noqa: BLE001 — normalize to Domain ValidationError
        raise ValidationError(f"ClaimDirection payload invalid: {exc}") from exc
    return model.model_dump(mode="json")
