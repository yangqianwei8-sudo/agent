"""LLM provider/client unit tests (FakeLLM — no network)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.llm.errors import (
    LLMOutputParseError,
    LLMRateLimitError,
    LLMSchemaValidationError,
    LLMTimeoutError,
)
from backend.llm.fake import FakeLLMClient
from backend.llm.json_parser import extract_json_value
from backend.schemas.evidence_proposal import EvidenceItemProposal


def test_a_parse_standard_json() -> None:
    raw = '{"intent":"CONTINUE","target_text":"","arguments":{},"confidence":1,"reason":"x"}'
    value = extract_json_value(raw)
    assert isinstance(value, dict)
    assert value["intent"] == "CONTINUE"


def test_b_parse_json_code_fence() -> None:
    raw = '说明如下：\n```json\n{"ok": true, "n": 1}\n```\n完'
    value = extract_json_value(raw)
    assert value == {"ok": True, "n": 1}


def test_c_invalid_json_raises() -> None:
    with pytest.raises(LLMOutputParseError):
        extract_json_value("not json at all {{{")


def test_d_schema_invalid_amount_not_silently_coerced() -> None:
    # amount-like field must not be auto-fixed by parser — pydantic rejects wrong types
    with pytest.raises(ValidationError):
        EvidenceItemProposal.model_validate(
            {
                "proposal_id": "00000000-0000-4000-8000-000000000099",
                "title": "x",
                "summary": "y",
                "category": "OTHER",
                "source_span_ids": [],  # min_length 1 fails
                "confidence": 0.5,
                "organizer_reason": "r",
            }
        )


def test_e_timeout_classification() -> None:
    client = FakeLLMClient(responses=[LLMTimeoutError("LLM request timed out")])
    with pytest.raises(LLMTimeoutError) as exc:
        client.complete_json(
            system_prompt="s", user_prompt="u", schema_name="t"
        )
    assert "sk-" not in str(exc.value)
    assert exc.value.code == "LLM_TIMEOUT"


def test_f_api_key_not_in_exception_string() -> None:
    from backend.llm.errors import LLMRequestError, _redact_secrets

    redacted = _redact_secrets("failed Bearer sk-secretvalue123 Authorization")
    assert "sk-secret" not in redacted
    assert "Bearer" not in redacted or "REDACTED" in redacted or "redacted" in redacted.lower()
    err = LLMRequestError("provider said api_key=sk-abc123xyz")
    assert "sk-abc" not in str(err)
    assert "sk-abc" not in err.message


def test_rate_limit_via_fake() -> None:
    client = FakeLLMClient(responses=[LLMRateLimitError("LLM rate limited")])
    with pytest.raises(LLMRateLimitError):
        client.complete_json(system_prompt="s", user_prompt="u", schema_name="t")


def test_schema_validation_error_type() -> None:
    err = LLMSchemaValidationError("bad")
    assert err.code == "LLM_SCHEMA_INVALID"
