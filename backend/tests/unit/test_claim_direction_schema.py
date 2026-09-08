"""Unit tests — ClaimDirection schema (no DB)."""

import uuid

import pytest

from backend.domain.errors import ValidationError
from backend.schemas.claim_direction import validate_claim_direction_payload


def test_claim_direction_valid_payment() -> None:
    payload = {
        "overall_strategy": "主张设计费及违约金",
        "claims": [
            {
                "claim_type": "PAYMENT",
                "description": "支付设计费",
                "amount": 100000,
                "currency": "CNY",
                "supporting_fact_ids": [str(uuid.uuid4())],
            }
        ],
    }
    result = validate_claim_direction_payload(payload)
    assert result["claims"][0]["amount"] == 100000


def test_payment_missing_amount_rejected() -> None:
    payload = {
        "overall_strategy": "主张设计费",
        "claims": [
            {
                "claim_type": "PAYMENT",
                "description": "支付设计费",
                "currency": "CNY",
                "supporting_fact_ids": [],
            }
        ],
    }
    with pytest.raises(ValidationError):
        validate_claim_direction_payload(payload)


def test_amount_negative_rejected() -> None:
    payload = {
        "overall_strategy": "主张设计费",
        "claims": [
            {
                "claim_type": "LIQUIDATED_DAMAGES",
                "description": "违约金",
                "amount": -1,
                "currency": "CNY",
                "supporting_fact_ids": [],
            }
        ],
    }
    with pytest.raises(ValidationError):
        validate_claim_direction_payload(payload)


def test_extra_properties_rejected() -> None:
    payload = {
        "overall_strategy": "x",
        "claims": [
            {
                "claim_type": "TERMINATION",
                "description": "解除合同",
                "supporting_fact_ids": [],
                "foo": "bar",
            }
        ],
    }
    with pytest.raises(ValidationError):
        validate_claim_direction_payload(payload)
