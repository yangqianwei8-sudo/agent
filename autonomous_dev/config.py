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
    webhook_public_url: str = ""

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


@lru_cache
def get_autonomous_settings() -> AutonomousDevSettings:
    return AutonomousDevSettings()


def clear_autonomous_settings_cache() -> None:
    get_autonomous_settings.cache_clear()
