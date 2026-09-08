"""Fake LLM client for deterministic unit/integration tests."""

from __future__ import annotations

from typing import Any

from backend.llm.dto import LLMJsonResult, LLMUsage
from backend.llm.errors import (
    LLMOutputParseError,
    LLMRateLimitError,
    LLMSchemaValidationError,
    LLMTimeoutError,
)
from backend.llm.json_parser import extract_json_value


class FakeLLMClient:
    """Preset responses / errors — never hits the network."""

    def __init__(
        self,
        *,
        responses: list[str | dict[str, Any] | Exception] | None = None,
        model: str = "fake-model",
        provider: str = "fake",
    ) -> None:
        self._queue: list[str | dict[str, Any] | Exception] = list(responses or [])
        self.model = model
        self.provider = provider
        self.calls: list[dict[str, str]] = []

    def enqueue(self, item: str | dict[str, Any] | Exception) -> None:
        self._queue.append(item)

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema_name: str,
    ) -> LLMJsonResult:
        self.calls.append(
            {
                "schema_name": schema_name,
                "system_len": str(len(system_prompt)),
                "user_len": str(len(user_prompt)),
            }
        )
        if not self._queue:
            raise LLMOutputParseError("FakeLLMClient queue empty")
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, dict):
            raw = __import__("json").dumps(item, ensure_ascii=False)
            parsed: dict[str, Any] | list[Any] = item
        else:
            raw = item
            try:
                parsed = extract_json_value(raw)
            except LLMOutputParseError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise LLMOutputParseError("fake parse failed") from exc
        if not isinstance(parsed, (dict, list)):
            raise LLMSchemaValidationError("fake JSON root invalid")
        return LLMJsonResult(
            raw_text=raw if isinstance(item, str) else raw,
            parsed_json=parsed if isinstance(parsed, dict) else {"items": parsed},
            model=self.model,
            provider=self.provider,
            usage=LLMUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
            request_id="fake-req-1",
            latency_ms=1,
        )


# Re-export error helpers used by tests
__all__ = [
    "FakeLLMClient",
    "LLMTimeoutError",
    "LLMRateLimitError",
    "LLMOutputParseError",
]
