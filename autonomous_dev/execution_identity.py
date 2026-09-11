"""Task execution identity — repo + issue + generation for exactly-once."""

from __future__ import annotations

from typing import Any

from autonomous_dev.config import AutonomousDevSettings


def compute_execution_key(settings: AutonomousDevSettings, payload: dict[str, Any]) -> str | None:
    issue = payload.get("issue") or {}
    number = issue.get("number")
    if number is None:
        return None
    generation = issue.get("updated_at") or issue.get("created_at") or "unknown"
    return f"{settings.github_repo}#{number}#{generation}"
