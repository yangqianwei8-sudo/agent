"""LLM package — provider/client errors (engines via factory submodule)."""

from backend.llm.client import LLMClient, OpenAICompatibleClient
from backend.llm.errors import (
    LLMConfigurationError,
    LLMError,
    LLMOutputParseError,
    LLMRateLimitError,
    LLMRequestError,
    LLMSchemaValidationError,
    LLMTimeoutError,
)

__all__ = [
    "LLMClient",
    "OpenAICompatibleClient",
    "LLMError",
    "LLMConfigurationError",
    "LLMRequestError",
    "LLMTimeoutError",
    "LLMRateLimitError",
    "LLMOutputParseError",
    "LLMSchemaValidationError",
]
