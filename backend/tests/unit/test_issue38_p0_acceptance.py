"""Behavioral tests for issue #38 P0 marker-only acceptance and fixture determinism."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import pytest
from autonomous_dev.acceptance_marker import diff_is_marker_only, diff_paths_changed, read_marker
from autonomous_dev.p0_acceptance import (
    evaluate_marker_only_review,
    execute_marker_only_acceptance,
    is_marker_only_acceptance,
    marker_commit_paths,
    validate_marker_only_commit_paths,
)
from autonomous_dev.reviewer_service import REVIEWER_ACCEPTANCE_MARKER

from backend.fixtures.deterministic import (
    DETERMINISTIC_PDF_EPOCH,
    FIXTURE_NAMES,
    assert_pdf_fixture_metadata,
    extract_stable_pdf_id,
    generate,
    pdf_has_deterministic_metadata,
    pin_pdf_deterministic_metadata,
    sync_golden_fixtures,
    validate_fixtures,
    verify_independent_generation_byte_identity,
    write_deterministic_pdf_bytes,
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


def test_issue38_reviewer_fails_when_marker_diff_includes_fixtures() -> None:
    """Issue #75 repair: marker+fixture churn must FAIL, not pass as harmless marker."""
    diff = (
        "diff --git a/autonomous_dev/acceptance_marker.txt "
        "b/autonomous_dev/acceptance_marker.txt\n"
        "+++ b/autonomous_dev/acceptance_marker.txt\n"
        "+worker-run issue=38\n"
        "diff --git a/backend/tests/fixtures/sample_text.pdf "
        "b/backend/tests/fixtures/sample_text.pdf\n"
    )
    assert diff_is_marker_only(diff) is False
    assert "backend/tests/fixtures/sample_text.pdf" in diff_paths_changed(diff)
    verdict = evaluate_marker_only_review(diff, _issue38_body())
    assert verdict is not None
    assert verdict.verdict == "FAIL"
    assert "sample_text.pdf" in verdict.reason
    assert verdict.fail_repair_summary is not None
    assert "Commit only autonomous_dev/acceptance_marker.txt" in verdict.fail_repair_summary


def test_issue75_repair_pdf_write_path_asserts_pinned_metadata() -> None:
    """Production PDF write path rejects unpinned metadata before fixtures are committed."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        generate(output_dir=root)
        for name in ("sample_text.pdf", "sample_scanned.pdf"):
            data = (root / name).read_bytes()
            assert_pdf_fixture_metadata(data, label=name)
            doc_id = extract_stable_pdf_id(data)
            assert doc_id is not None
            assert len(doc_id) == 32
            generate(output_dir=root)
            assert extract_stable_pdf_id((root / name).read_bytes()) == doc_id


def test_committed_golden_fixtures_match_sync_output() -> None:
    from backend.fixtures.deterministic import DEFAULT_FIXTURES_DIR

    validate_fixtures(DEFAULT_FIXTURES_DIR)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        sync_golden_fixtures(output_dir=root)
        for name in FIXTURE_NAMES:
            assert (DEFAULT_FIXTURES_DIR / name).read_bytes() == (root / name).read_bytes()


def test_issue38_production_pdf_write_path_is_byte_stable() -> None:
    """Issue #38: production PDF writer pins metadata so marker-only commits avoid PDF churn."""

    def render(c) -> None:  # type: ignore[no-untyped-def]
        c.drawString(72, 800, "issue #38 stable fixture")

    first = write_deterministic_pdf_bytes(render)
    second = write_deterministic_pdf_bytes(render)
    assert first == second
    assert DETERMINISTIC_PDF_EPOCH in first.decode("latin-1")
    assert extract_stable_pdf_id(first) == extract_stable_pdf_id(second)


def test_issue38_fixture_generation_pins_pdf_metadata_before_marker_commit() -> None:
    """Issue #38 repair: production generator pins CreationDate/ModDate and stable /ID."""
    verify_independent_generation_byte_identity()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        generate(output_dir=root)
        first = {name: (root / name).read_bytes() for name in FIXTURE_NAMES}
        for name in ("sample_text.pdf", "sample_scanned.pdf"):
            assert pdf_has_deterministic_metadata(first[name])
            assert pin_pdf_deterministic_metadata(first[name]) == first[name]
        generate(output_dir=root)
        second = {name: (root / name).read_bytes() for name in FIXTURE_NAMES}
        assert first == second


def test_marker_only_acceptance_end_to_end_with_stable_fixtures() -> None:
    """Issue #38 flow: stable fixtures gate passes before marker-only commit proceeds."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        fixtures = Path(tmp) / "fixtures"
        generate(output_dir=fixtures)

        marker_path, commit_sha = execute_marker_only_acceptance(
            repo,
            38,
            run_gate_tests=lambda: None,
            commit_paths=lambda paths: "deadbeef",
            fixtures_dir=fixtures,
        )
        assert marker_path.name == "acceptance_marker.txt"
        assert "worker-run issue=38" in read_marker(repo)
        assert commit_sha == "deadbeef"


def test_validate_marker_only_commit_paths_rejects_broad_scope() -> None:
    validate_marker_only_commit_paths(marker_commit_paths())
    with pytest.raises(ValueError, match="must commit exactly"):
        validate_marker_only_commit_paths(
            ["autonomous_dev/acceptance_marker.txt", "backend/tests/fixtures/sample_text.pdf"]
        )


def test_execute_marker_only_acceptance_enforces_marker_only_commit_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Production guard must abort before commit when scope includes fixture paths."""
    import autonomous_dev.p0_acceptance as p0

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    fixtures = tmp_path / "fixtures"
    generate(output_dir=fixtures)

    monkeypatch.setattr(
        p0,
        "marker_commit_paths",
        lambda: ["autonomous_dev/acceptance_marker.txt", "backend/tests/fixtures/sample_text.pdf"],
    )
    with pytest.raises(ValueError, match="must commit exactly"):
        execute_marker_only_acceptance(
            repo,
            38,
            run_gate_tests=lambda: None,
            commit_paths=lambda paths: "unused",
            fixtures_dir=fixtures,
        )


def test_issue81_marker_only_worker_and_reviewer_e2e(tmp_path: Path) -> None:
    """Issue #81 repair: marker-only worker commit passes reviewer on marker diff only."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    fixtures = tmp_path / "fixtures"
    generate(output_dir=fixtures)

    captured_paths: list[list[str]] = []

    def commit(paths: list[str]) -> str:
        captured_paths.append(list(paths))
        subprocess.run(["git", "add", *paths], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-m", "issue #81 marker-only"], cwd=repo, check=True)
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    execute_marker_only_acceptance(
        repo,
        81,
        run_gate_tests=lambda: None,
        commit_paths=commit,
        fixtures_dir=fixtures,
    )
    assert captured_paths == [marker_commit_paths()]

    diff = subprocess.run(
        ["git", "show", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    verdict = evaluate_marker_only_review(diff, _issue38_body())
    assert verdict is not None
    assert verdict.verdict == "PASS"
    assert "worker-run issue=81" in read_marker(repo)
