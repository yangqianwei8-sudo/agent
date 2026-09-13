"""Acceptance marker file helpers for the P0 live acceptance chain."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

MARKER_REL_PATH = Path("autonomous_dev/acceptance_marker.txt")
_MARKER_ISSUE_RE = re.compile(r"worker-run issue=(\d+)")


def format_marker_content(*, issue_number: int, at: datetime | None = None) -> str:
    ts = at or datetime.now(UTC)
    return f"worker-run issue={issue_number} at={ts.isoformat()}\n"


def marker_path(repo_root: Path) -> Path:
    return repo_root / MARKER_REL_PATH


def read_marker(repo_root: Path) -> str:
    path = marker_path(repo_root)
    return path.read_text(encoding="utf-8") if path.exists() else ""


def write_marker(
    repo_root: Path,
    *,
    issue_number: int,
    at: datetime | None = None,
) -> Path:
    path = marker_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        format_marker_content(issue_number=issue_number, at=at),
        encoding="utf-8",
    )
    return path


def parse_marker_issue_number(content: str) -> int | None:
    match = _MARKER_ISSUE_RE.search(content)
    return int(match.group(1)) if match else None
