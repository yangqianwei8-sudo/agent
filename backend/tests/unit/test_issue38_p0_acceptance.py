"""Behavioral tests for issue #38 P0 marker-only acceptance and fixture determinism."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import pytest
from autonomous_dev.acceptance_marker import read_marker
from autonomous_dev.p0_acceptance import (
    evaluate_marker_only_review,
    execute_marker_only_acceptance,
    is_marker_only_acceptance,
    marker_commit_paths,
)
from autonomous_dev.reviewer_service import REVIEWER_ACCEPTANCE_MARKER

from backend.fixtures.deterministic import (
    DETERMINISTIC_PDF_EPOCH,
    FIXTURE_NAMES,
    generate,
    pdf_has_deterministic_metadata,
    sync_golden_fixtures,
    validate_fixtures,
)


def _issue38_body() -> str:
    return (
        f"{REVIEWER_ACCEPTANCE_MARKER}\n"
        "[P0-LIVE-ACCEPTANCE]\n"
        "Harmless marker-only change for [ACCEPT] Restart B 747cb2ef.\n"
        "Update autonomous_dev/acceptance_marker.txt only."
    )


def _init_git_repo(root: Path) -> None:
    (root / "autonomous_dev").mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text("init\n", encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=root, check=True)
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True)


def test_issue38_body_is_marker_only_acceptance() -> None:
    body = _issue38_body()
    assert is_marker_only_acceptance(body)
    assert marker_commit_paths() == ["autonomous_dev/acceptance_marker.txt"]


def test_sync_golden_fixtures_pins_pdf_metadata_and_is_idempotent() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        sync_golden_fixtures(output_dir=root)
        first = {name: (root / name).read_bytes() for name in FIXTURE_NAMES}
        for name in ("sample_text.pdf", "sample_scanned.pdf"):
            assert pdf_has_deterministic_metadata(first[name])
            text = first[name].decode("latin-1")
            assert DETERMINISTIC_PDF_EPOCH in text
        sync_golden_fixtures(output_dir=root)
        second = {name: (root / name).read_bytes() for name in FIXTURE_NAMES}
        assert first == second


def test_execute_marker_only_acceptance_stages_only_marker(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    fixtures = tmp_path / "fixtures"
    generate(output_dir=fixtures)

    gate_ran = {"ok": False}

    def gate() -> None:
        gate_ran["ok"] = True

    def commit(paths: list[str]) -> str:
        subprocess.run(["git", "add", *paths], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "issue #38 marker-only"], cwd=repo, check=True)
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    marker_path, commit_sha = execute_marker_only_acceptance(
        repo,
        38,
        run_gate_tests=gate,
        commit_paths=commit,
        fixtures_dir=fixtures,
    )
    assert gate_ran["ok"]
    assert marker_path.name == "acceptance_marker.txt"
    assert "worker-run issue=38" in read_marker(repo)
    show = subprocess.run(
        ["git", "show", "--name-only", "--pretty=format:", commit_sha],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    changed = [line.strip() for line in show.stdout.splitlines() if line.strip()]
    assert changed == marker_commit_paths()


def test_execute_marker_only_acceptance_aborts_on_fixture_drift(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    fixtures = tmp_path / "fixtures"
    generate(output_dir=fixtures)
    (fixtures / "sample_text.pdf").write_bytes(b"%PDF-1.4 corrupt")

    marker_path = repo / "autonomous_dev" / "acceptance_marker.txt"
    marker_before = read_marker(repo) if marker_path.exists() else ""

    with pytest.raises(ValueError, match="sample_text.pdf"):
        execute_marker_only_acceptance(
            repo,
            38,
            run_gate_tests=lambda: None,
            commit_paths=lambda paths: "unused",
            fixtures_dir=fixtures,
        )

    assert read_marker(repo) == marker_before


def test_issue38_reviewer_passes_on_marker_only_diff() -> None:
    diff = (
        "diff --git a/autonomous_dev/acceptance_marker.txt "
        "b/autonomous_dev/acceptance_marker.txt\n"
        "+++ b/autonomous_dev/acceptance_marker.txt\n"
        "+worker-run issue=38\n"
    )
    verdict = evaluate_marker_only_review(diff, _issue38_body())
    assert verdict is not None
    assert verdict.verdict == "PASS"
    assert "Harmless acceptance marker updated as required" in verdict.reason


def test_issue38_reviewer_fails_without_marker_diff() -> None:
    verdict = evaluate_marker_only_review("diff --git a/README.md b/README.md\n", _issue38_body())
    assert verdict is not None
    assert verdict.verdict == "FAIL"
    assert "Expected acceptance_marker.txt change not found in diff" in verdict.reason


def test_committed_golden_fixtures_match_sync_output() -> None:
    from backend.fixtures.deterministic import DEFAULT_FIXTURES_DIR

    validate_fixtures(DEFAULT_FIXTURES_DIR)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        sync_golden_fixtures(output_dir=root)
        for name in FIXTURE_NAMES:
            assert (DEFAULT_FIXTURES_DIR / name).read_bytes() == (root / name).read_bytes()


def test_issue75_repair_gate_includes_issue38_behavioral_tests() -> None:
    """Issue #75 repair wires issue #38 P0 acceptance tests into the production gate."""
    from autonomous_dev.p0_acceptance import ACCEPTANCE_GATE_PYTEST_TARGETS

    assert "backend/tests/unit/test_issue38_p0_acceptance.py" in ACCEPTANCE_GATE_PYTEST_TARGETS
