"""Factory: wire Intent / Organizer / Analyst based on LLM_MODE."""

from __future__ import annotations

from functools import lru_cache

from backend.agent.intent_router import DeterministicIntentRouter, IntentEngine
from backend.infrastructure.config import Settings, get_settings
from backend.llm.client import LLMClient, OpenAICompatibleClient
from backend.llm.errors import LLMConfigurationError
from backend.llm.intent_router import LLMIntentRouter
from backend.skills.case_analyst import CaseAnalystEngine, DeterministicCaseAnalystStub
from backend.skills.evidence_organizer import DeterministicOrganizerStub, OrganizerEngine


def is_real_llm_mode(settings: Settings | None = None) -> bool:
    s = settings or get_settings()
    return (s.llm_mode or "deterministic").strip().lower() == "real"


def ai_mode_label(settings: Settings | None = None) -> dict[str, str | None]:
    s = settings or get_settings()
    if is_real_llm_mode(s):
        return {
            "ai_mode": "Real",
            "model": s.llm_model or None,
            "provider": s.llm_provider or None,
        }
    return {"ai_mode": "Deterministic", "model": None, "provider": None}


@lru_cache
def get_llm_client() -> LLMClient:
    settings = get_settings()
    if not is_real_llm_mode(settings):
        raise LLMConfigurationError("LLM client requested while LLM_MODE is not real")
    provider = (settings.llm_provider or "openai_compatible").strip().lower()
    if provider not in {"openai_compatible", "openai", "deepseek"}:
        raise LLMConfigurationError(f"unsupported LLM_PROVIDER: {provider}")
    return OpenAICompatibleClient(settings)


def build_intent_engine(
    *,
    settings: Settings | None = None,
    client: LLMClient | None = None,
) -> IntentEngine:
    s = settings or get_settings()
    if not is_real_llm_mode(s):
        return DeterministicIntentRouter()
    return LLMIntentRouter(client or get_llm_client())


def build_organizer_engine(
    *,
    settings: Settings | None = None,
    client: LLMClient | None = None,
) -> OrganizerEngine:
    s = settings or get_settings()
    if not is_real_llm_mode(s):
        return DeterministicOrganizerStub()
    from backend.llm.organizer import LLMEvidenceOrganizerEngine

    return LLMEvidenceOrganizerEngine(client or get_llm_client())


def build_analyst_engine(
    *,
    settings: Settings | None = None,
    client: LLMClient | None = None,
) -> CaseAnalystEngine:
    s = settings or get_settings()
    if not is_real_llm_mode(s):
        return DeterministicCaseAnalystStub()
    from backend.llm.analyst import LLMCaseAnalystEngine

    return LLMCaseAnalystEngine(client or get_llm_client())


def build_claim_direction_engine(
    *,
    settings: Settings | None = None,
    client: LLMClient | None = None,
):
    from backend.skills.claim_direction import (
        DeterministicClaimDirectionStub,
    )

    s = settings or get_settings()
    if not is_real_llm_mode(s):
        return DeterministicClaimDirectionStub()
    from backend.llm.claim_direction import LLMClaimDirectionEngine

    return LLMClaimDirectionEngine(client or get_llm_client())


def build_pleading_writer_engine(
    *,
    settings: Settings | None = None,
    client: LLMClient | None = None,
):
    from backend.skills.pleading_writer import (
        DeterministicPleadingWriterStub,
    )

    s = settings or get_settings()
    if not is_real_llm_mode(s):
        return DeterministicPleadingWriterStub()
    from backend.llm.pleading_writer import LLMPleadingWriterEngine

    return LLMPleadingWriterEngine(client or get_llm_client())


def clear_llm_caches() -> None:
    get_llm_client.cache_clear()
