"""P0 live acceptance — harmless marker-only changes (issue #38 chain).

Issue #38 materializes as a roadmap task that updates only
``autonomous_dev/acceptance_marker.txt``.  The worker must commit that path
alone; the reviewer verifies the marker appears in the git diff.
"""

from __future__ import annotations

from pathlib import Path

from autonomous_dev.acceptance_marker import MARKER_GIT_PATH, write_marker
from autonomous_dev.reviewer_service import REVIEWER_ACCEPTANCE_MARKER

P0_LIVE_ACCEPTANCE_MARKER = "[P0-LIVE-ACCEPTANCE]"
_MARKER_ONLY_INSTRUCTION = "Update autonomous_dev/acceptance_marker.txt only."


def is_marker_only_acceptance(issue_body: str) -> bool:
    """Return True when the issue SSOT requires only a marker file update."""
    return (
        P0_LIVE_ACCEPTANCE_MARKER in issue_body
        and REVIEWER_ACCEPTANCE_MARKER in issue_body
        and _MARKER_ONLY_INSTRUCTION in issue_body
    )


def marker_commit_paths() -> list[str]:
    """Git paths staged for a marker-only P0 acceptance commit."""
    return [MARKER_GIT_PATH]


def apply_harmless_marker_change(repo_root: Path, issue_number: int) -> Path:
    """Write the acceptance marker for *issue_number* and return its path."""
    return write_marker(repo_root, issue_number=issue_number)
