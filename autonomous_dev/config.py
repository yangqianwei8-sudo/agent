"""Autonomous dev configuration — secrets from env only."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class AutonomousDevSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    autonomous_dev_enabled: bool = False
    github_webhook_secret: str = ""
    github_token: str = ""
    github_auth_mode: str = "auto"  # auto | pat | app | ssh
    github_app_id: str = ""
    github_app_installation_id: str = ""
    github_app_private_key: str = ""
    github_app_private_key_path: str = ""
    github_ssh_key_path: str = ""
    github_token_file: str = ""
    cursor_api_key: str = ""
    cursor_model: str = "composer-2"
    autonomous_worker_mode: str = "deterministic"  # deterministic | live | cursor_sdk
    autonomous_repo_root: str = "."
    autonomous_state_db_path: str = "data/autonomous_dev.db"
    github_repo: str = "yangqianwei8-sudo/agent"
    watchdog_interval_seconds: int = 3600
    current_task_scan_interval_seconds: int = 10
    watchdog_github_connect_timeout_seconds: float = 2.0
    watchdog_github_read_timeout_seconds: float = 5.0
    worker_lease_ttl_seconds: int = 300
    worker_heartbeat_interval_seconds: int = 30
    review_watchdog_stale_seconds: int = 3600
    webhook_public_url: str = ""
    openai_api_key: str = ""
    reviewer_model: str = ""
    reviewer_base_url: str = ""
    reviewer_max_retries: int = 2
    reviewer_lease_ttl_seconds: int = 300
    review_max_attempts: int = 5
    review_retry_backoff_seconds: int = 5
    review_recovery_interval_seconds: int = 10
    handoff_stall_seconds: int = 20
    autonomous_progress_stale_seconds: int = 120
    cursor_long_op_grace_seconds: int = 600
    cursor_long_op_suspect_seconds: int = 900

    @property
    def repo_root(self) -> Path:
        return Path(self.autonomous_repo_root).resolve()

    @property
    def state_db_path(self) -> Path:
        p = Path(self.autonomous_state_db_path)
        if not p.is_absolute():
            p = self.repo_root / p
        return p

    def validate_cursor_sdk_config(self) -> None:
        """Fail closed when cursor_sdk mode is active but Cursor SDK is misconfigured."""
        if self.autonomous_worker_mode != "cursor_sdk":
            return
        if not self.cursor_api_key.strip():
            raise RuntimeError("CURSOR_API_KEY required for cursor_sdk mode")
        if not self.cursor_model.strip():
            raise RuntimeError("CURSOR_MODEL required for cursor_sdk mode")

    def resolve_reviewer_credentials(self) -> tuple[str, str, str] | None:
        """Return (api_key, base_url, model) or None if reviewer credentials absent.

        Reviewer credentials are intentionally isolated from worker LLM settings
        (LLM_API_KEY / LLM_BASE_URL / LLM_MODEL). Only OPENAI_API_KEY and
        REVIEWER_* env vars configure the independent reviewer — never the
        DeepSeek/Cursor worker provider — so the reviewer cannot be silently
        substituted by the same API the worker uses.
        """
        import os

        api_key = (self.openai_api_key or os.environ.get("OPENAI_API_KEY") or "").strip()
        base_url = (
            self.reviewer_base_url
            or os.environ.get("REVIEWER_BASE_URL")
            or "https://api.openai.com/v1"
        ).strip().rstrip("/")
        model = (
            self.reviewer_model
            or os.environ.get("REVIEWER_MODEL")
            or "gpt-4o-mini"
        ).strip()
        if not api_key:
            return None
        return api_key, base_url, model


@lru_cache
def get_autonomous_settings() -> AutonomousDevSettings:
    return AutonomousDevSettings()


def clear_autonomous_settings_cache() -> None:
    get_autonomous_settings.cache_clear()
