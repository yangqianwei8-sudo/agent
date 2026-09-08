"""Application settings."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "litigation-case-agent"
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"
    database_url: str = (
        "postgresql+psycopg://litigation_agent:litigation_agent_dev"
        "@127.0.0.1:5432/litigation_case_agent"
    )
    test_database_url: str | None = None

    # Local object storage (Phase 4)
    storage_root: str = "storage"

    # Scan-PDF / OCR heuristics (tool config — not domain rules)
    pdf_scan_min_total_non_ws_chars: int = 200
    pdf_scan_empty_page_non_ws_chars: int = 20
    pdf_scan_empty_page_ratio: float = 0.8
    max_material_bytes: int = 50 * 1024 * 1024

    # Real LLM (Stage 1) — default deterministic so tests never hit network
    llm_mode: str = "deterministic"  # deterministic | real
    llm_provider: str = "openai_compatible"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout_seconds: int = 60
    llm_max_retries: int = 2


@lru_cache
def get_settings() -> Settings:
    return Settings()


def clear_settings_cache() -> None:
    get_settings.cache_clear()
