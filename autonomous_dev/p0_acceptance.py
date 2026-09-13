"""P0 live acceptance — harmless marker-only changes (issue #38 chain).

Issue #38 materializes as a roadmap task that updates only
``autonomous_dev/acceptance_marker.txt``.  The worker must commit that path
alone; the reviewer verifies the marker appears in the git diff.

Golden PDF/DOCX fixtures are validated before marker-only commits so
CreationDate/ModDate and /ID pinning in ``backend.fixtures.deterministic``
prevents binary churn from being staged alongside the marker.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from autonomous_dev.acceptance_marker import MARKER_GIT_PATH, diff_includes_marker, write_marker
from autonomous_dev.reviewer_service import REVIEWER_ACCEPTANCE_MARKER

P0_LIVE_ACCEPTANCE_MARKER = "[P0-LIVE-ACCEPTANCE]"
_MARKER_ONLY_INSTRUCTION = "Update autonomous_dev/acceptance_marker.txt only."

# Pytest targets run before marker-only commits — health, fixture determinism, issue #38 flow.
ACCEPTANCE_GATE_PYTEST_TARGETS: tuple[str, ...] = (
    "backend/tests/test_health.py",
    "backend/tests/unit/test_generate_fixtures.py",
    "backend/tests/unit/test_issue38_p0_acceptance.py",
)


@dataclass(frozen=True)
class MarkerOnlyReviewVerdict:
    """Deterministic reviewer outcome for issue #38 marker-only tasks."""

    verdict: str
    reason: str
    fail_repair_summary: str | None = None


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


def validate_marker_only_commit_paths(paths: list[str]) -> None:
    """Raise ValueError when a marker-only commit would touch paths outside the marker."""
    allowed = [MARKER_GIT_PATH]
    if paths != allowed:
        raise ValueError(
            f"marker-only acceptance must commit exactly {allowed}, got {paths}"
        )


def apply_harmless_marker_change(repo_root: Path, issue_number: int) -> Path:
    """Write the acceptance marker for *issue_number* and return its path."""
    return write_marker(repo_root, issue_number=issue_number)


def ensure_golden_fixtures_stable(*, fixtures_dir: Path | None = None) -> None:
    """Validate golden fixtures byte-match fresh deterministic generation."""
    from backend.fixtures.deterministic import DEFAULT_FIXTURES_DIR, validate_fixtures

    validate_fixtures(fixtures_dir if fixtures_dir is not None else DEFAULT_FIXTURES_DIR)


def acceptance_gate_pytest_argv() -> list[str]:
    """Argv fragment for pytest gate runs before marker-only worker commits."""
    return ["-m", "pytest", *ACCEPTANCE_GATE_PYTEST_TARGETS, "-q"]


def evaluate_marker_only_review(diff: str, issue_body: str) -> MarkerOnlyReviewVerdict | None:
    """Production reviewer gate for issue #38 Restart-B marker-only acceptance."""
    if not is_marker_only_acceptance(issue_body):
        return None
    if diff_includes_marker(diff):
        return MarkerOnlyReviewVerdict(
            verdict="PASS",
            reason="Harmless acceptance marker updated as required",
        )
    return MarkerOnlyReviewVerdict(
        verdict="FAIL",
        reason="Expected acceptance_marker.txt change not found in diff",
        fail_repair_summary="Ensure worker updates autonomous_dev/acceptance_marker.txt",
    )


def execute_marker_only_acceptance(
    repo_root: Path,
    issue_number: int,
    *,
    run_gate_tests: Callable[[], None],
    commit_paths: Callable[[list[str]], str],
    fixtures_dir: Path | None = None,
) -> tuple[Path, str]:
    """Production worker flow: stable fixtures, marker write, gate tests, path-scoped commit."""
    ensure_golden_fixtures_stable(fixtures_dir=fixtures_dir)
    marker_path = apply_harmless_marker_change(repo_root, issue_number)
    run_gate_tests()
    paths = marker_commit_paths()
    validate_marker_only_commit_paths(paths)
    commit_sha = commit_paths(paths)
    return marker_path, commit_sha
