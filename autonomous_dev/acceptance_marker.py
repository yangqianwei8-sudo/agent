"""Acceptance marker file helpers for the P0 live acceptance chain."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

MARKER_REL_PATH = Path("autonomous_dev/acceptance_marker.txt")
MARKER_GIT_PATH = "autonomous_dev/acceptance_marker.txt"
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


def diff_includes_marker(diff: str) -> bool:
    """Return True when a git diff touches the P0 acceptance marker file."""
    return MARKER_GIT_PATH in diff


def diff_paths_changed(diff: str) -> frozenset[str]:
    """Extract changed file paths from a unified git diff."""
    paths: set[str] = set()
    for line in diff.splitlines():
        if not line.startswith("diff --git "):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        for token in (parts[2], parts[3]):
            if token.startswith(("a/", "b/")):
                paths.add(token[2:])
    return frozenset(paths)


def diff_is_marker_only(diff: str) -> bool:
    """Return True when the diff changes only the P0 acceptance marker file."""
    paths = diff_paths_changed(diff)
    return paths == frozenset({MARKER_GIT_PATH})
