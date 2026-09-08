"""LLM layer DTOs — never carry API keys."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class LLMUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class LLMJsonResult(BaseModel):
    """Structured completion result (safe for logs / skill metrics)."""

    model_config = ConfigDict(extra="forbid")

    raw_text: str
    parsed_json: dict[str, Any] | list[Any]
    model: str
    provider: str
    usage: LLMUsage = Field(default_factory=LLMUsage)
    request_id: str | None = None
    latency_ms: int | None = None
