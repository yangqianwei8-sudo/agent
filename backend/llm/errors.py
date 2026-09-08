"""LLM error taxonomy — safe messages (never include API keys)."""

from __future__ import annotations


class LLMError(Exception):
    """Base LLM error."""

    code: str = "LLM_ERROR"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        safe = _redact_secrets(message)
        super().__init__(safe)
        self.message = safe
        if code is not None:
            self.code = code


class LLMConfigurationError(LLMError):
    code = "LLM_CONFIGURATION_ERROR"


class LLMRequestError(LLMError):
    code = "LLM_REQUEST_ERROR"


class LLMTimeoutError(LLMError):
    code = "LLM_TIMEOUT"


class LLMRateLimitError(LLMError):
    code = "LLM_RATE_LIMIT"


class LLMOutputParseError(LLMError):
    code = "LLM_OUTPUT_INVALID"


class LLMSchemaValidationError(LLMError):
    code = "LLM_SCHEMA_INVALID"


_SECRET_PATTERNS = (
    "sk-",
    "Bearer ",
    "api_key",
    "apikey",
    "authorization",
)


def _redact_secrets(text: str) -> str:
    lowered = text.lower()
    if any(p.lower() in lowered for p in _SECRET_PATTERNS):
        # Never echo secrets — replace whole message if suspicious
        return "LLM error (details redacted for secret safety)"
    # Also strip long token-like substrings
    out: list[str] = []
    for part in text.split():
        if part.startswith("sk-") or (len(part) > 24 and part.isalnum()):
            out.append("[REDACTED]")
        else:
            out.append(part)
    return " ".join(out)
