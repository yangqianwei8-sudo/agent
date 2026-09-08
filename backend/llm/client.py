"""LLM client protocol + OpenAI-compatible Chat Completions transport."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Protocol

import httpx

from backend.infrastructure.config import Settings, get_settings
from backend.llm.dto import LLMJsonResult, LLMUsage
from backend.llm.errors import (
    LLMConfigurationError,
    LLMRateLimitError,
    LLMRequestError,
    LLMTimeoutError,
)
from backend.llm.json_parser import extract_json_value

logger = logging.getLogger(__name__)


class LLMClient(Protocol):
    def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema_name: str,
    ) -> LLMJsonResult: ...


class OpenAICompatibleClient:
    """HTTP Chat Completions adapter (DeepSeek / OpenAI-compatible)."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._validate_config()

    def _validate_config(self) -> None:
        s = self.settings
        if not (s.llm_api_key or "").strip():
            raise LLMConfigurationError(
                "Real LLM mode is enabled but LLM configuration is incomplete "
                "(LLM_API_KEY missing)."
            )
        if not (s.llm_model or "").strip():
            raise LLMConfigurationError(
                "Real LLM mode is enabled but LLM configuration is incomplete "
                "(LLM_MODEL missing)."
            )
        if not (s.llm_base_url or "").strip():
            raise LLMConfigurationError(
                "Real LLM mode is enabled but LLM configuration is incomplete "
                "(LLM_BASE_URL missing)."
            )

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema_name: str,
    ) -> LLMJsonResult:
        _ = schema_name
        s = self.settings
        url = s.llm_base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {s.llm_api_key}",
            "Content-Type": "application/json",
        }
        body: dict[str, Any] = {
            "model": s.llm_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
        }
        # Prefer JSON mode when supported; ignore if provider rejects.
        body["response_format"] = {"type": "json_object"}

        max_attempts = max(1, int(s.llm_max_retries) + 1)
        last_exc: Exception | None = None
        for attempt in range(max_attempts):
            started = time.perf_counter()
            try:
                with httpx.Client(timeout=float(s.llm_timeout_seconds)) as client:
                    resp = client.post(url, headers=headers, json=body)
            except httpx.TimeoutException as exc:
                last_exc = LLMTimeoutError("LLM request timed out")
                if attempt + 1 < max_attempts:
                    continue
                raise last_exc from exc
            except httpx.HTTPError as exc:
                raise LLMRequestError("LLM HTTP transport error") from exc

            latency_ms = int((time.perf_counter() - started) * 1000)
            request_id = resp.headers.get("x-request-id") or str(uuid.uuid4())

            if resp.status_code == 429:
                last_exc = LLMRateLimitError("LLM rate limited")
                if attempt + 1 < max_attempts:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise last_exc

            if resp.status_code >= 500:
                last_exc = LLMRequestError(f"LLM provider HTTP {resp.status_code}")
                if attempt + 1 < max_attempts:
                    continue
                raise last_exc

            if resp.status_code >= 400:
                # Do not include response body — may echo secrets / case text
                raise LLMRequestError(f"LLM provider HTTP {resp.status_code}")

            try:
                payload = resp.json()
            except Exception as exc:  # noqa: BLE001
                raise LLMRequestError("LLM response is not JSON") from exc

            try:
                raw_text = payload["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                raise LLMRequestError("LLM response missing message content") from exc

            if not isinstance(raw_text, str):
                raise LLMRequestError("LLM message content is not text")

            parsed = extract_json_value(raw_text)
            usage_raw = payload.get("usage") or {}
            usage = LLMUsage(
                prompt_tokens=_int_or_none(usage_raw.get("prompt_tokens")),
                completion_tokens=_int_or_none(usage_raw.get("completion_tokens")),
                total_tokens=_int_or_none(usage_raw.get("total_tokens")),
            )
            logger.info(
                "llm_complete provider=%s model=%s schema=%s latency_ms=%s "
                "request_id=%s usage_total=%s",
                s.llm_provider,
                s.llm_model,
                schema_name,
                latency_ms,
                request_id,
                usage.total_tokens,
            )
            return LLMJsonResult(
                raw_text=raw_text,
                parsed_json=parsed if isinstance(parsed, dict) else {"items": parsed},
                model=s.llm_model,
                provider=s.llm_provider,
                usage=usage,
                request_id=request_id,
                latency_ms=latency_ms,
            )

        raise last_exc or LLMRequestError("LLM request failed")


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
