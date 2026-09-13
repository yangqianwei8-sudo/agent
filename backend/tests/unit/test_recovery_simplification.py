"""Test simplified recovery system — replaces worker_self_heal + reviewer_self_heal tests."""

import pytest
from autonomous_dev.config import AutonomousDevSettings
from autonomous_dev.recovery_simple import (
    _compute_retry_count,
    _should_retry_issue,
    reconcile_needs_fix_and_stalled_reviews,
)
from autonomous_dev.state import StateStore, TaskStatus


def test_compute_retry_count_from_task_history(tmp_path):
    """Retry count derived from task history instead of separate table."""
    db = tmp_path / "test.db"
    settings = AutonomousDevSettings(state_db_path=db)
    store = StateStore(settings.state_db_path)

    # Create 3 failed tasks for issue #42
    for i in range(3):
        task_id = store.create_task(
            issue_number=42,
            status=TaskStatus.FAILED,
            delivery_id=f"delivery-{i}",
            execution_key=f"key-{i}",
        )
        store.update_task(task_id, status=TaskStatus.FAILED, error=f"error {i}")

    retry_count = _compute_retry_count(store, issue_number=42)
    assert retry_count == 3


def test_retry_count_capped_at_max_attempts(tmp_path):
    """Retry count never exceeds MAX_RETRY_ATTEMPTS even with many failures."""
    db = tmp_path / "test.db"
    settings = AutonomousDevSettings(state_db_path=db)
    store = StateStore(settings.state_db_path)

    # Create 10 failed tasks
    for i in range(10):
        task_id = store.create_task(
            issue_number=99,
            status=TaskStatus.FAILED,
            delivery_id=f"delivery-{i}",
            execution_key=f"key-{i}",
        )
        store.update_task(task_id, status=TaskStatus.FAILED, error=f"error {i}")

    retry_count = _compute_retry_count(store, issue_number=99)
    assert retry_count == 5  # MAX_RETRY_ATTEMPTS


def test_should_retry_respects_budget_exhaustion(tmp_path, monkeypatch):
    """No retry when budget exhausted (5 failed tasks)."""
    from datetime import UTC, datetime

    db = tmp_path / "test.db"
    settings = AutonomousDevSettings(state_db_path=db)
    store = StateStore(settings.state_db_path)

    # Mock GitHubClient
    class _MockGitHub:
        configured = True

        def get_issue_body(self, num):
            return "test body"

        def get_issue_labels(self, num):
            return {"needs-fix", "cursor-task"}

    github = _MockGitHub()

    # Create 5 failed tasks (exhausted)
    for i in range(5):
        task_id = store.create_task(
            issue_number=42,
            status=TaskStatus.FAILED,
            delivery_id=f"delivery-{i}",
            execution_key=f"key-{i}",
        )
        store.update_task(task_id, status=TaskStatus.NEEDS_FIX, error=f"error {i}")

    should, reason = _should_retry_issue(
        store, github, 42, now=datetime.now(UTC)
    )
    assert not should
    assert "exhausted" in reason.lower()


def test_should_retry_respects_backoff_cooldown(tmp_path, monkeypatch):
    """No retry when backoff period hasn't elapsed."""
    from datetime import UTC, datetime, timedelta

    db = tmp_path / "test.db"
    settings = AutonomousDevSettings(state_db_path=db)
    store = StateStore(settings.state_db_path)

    class _MockGitHub:
        configured = True

        def get_issue_body(self, num):
            return "test"

        def get_issue_labels(self, num):
            return {"needs-fix", "cursor-task"}

    github = _MockGitHub()

    # Create 1 failed task updated 10 seconds ago
    task_id = store.create_task(
        issue_number=42,
        status=TaskStatus.NEEDS_FIX,
        delivery_id="d1",
        execution_key="k1",
    )
    recent_time = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
    # Manually set updated_at to recent past
    with store._get_conn() as conn:
        conn.execute(
            "UPDATE task_executions SET updated_at=? WHERE id=?",
            (recent_time, task_id),
        )
        conn.commit()

    should, reason = _should_retry_issue(
        store, github, 42, now=datetime.now(UTC)
    )
    assert not should
    assert "backoff pending" in reason


def test_should_retry_allows_after_backoff_expires(tmp_path):
    """Retry allowed after backoff cooldown expires."""
    from datetime import UTC, datetime, timedelta

    db = tmp_path / "test.db"
    settings = AutonomousDevSettings(state_db_path=db)
    store = StateStore(settings.state_db_path)

    class _MockGitHub:
        configured = True

        def get_issue_body(self, num):
            return "test"

        def get_issue_labels(self, num):
            return {"needs-fix", "cursor-task"}

    github = _MockGitHub()

    # Create failed task updated 2 minutes ago (backoff for 1 attempt = 60s)
    task_id = store.create_task(
        issue_number=42,
        status=TaskStatus.NEEDS_FIX,
        delivery_id="d1",
        execution_key="k1",
    )
    old_time = (datetime.now(UTC) - timedelta(seconds=150)).isoformat()
    with store._get_conn() as conn:
        conn.execute(
            "UPDATE task_executions SET updated_at=? WHERE id=?",
            (old_time, task_id),
        )
        conn.commit()

    should, reason = _should_retry_issue(
        store, github, 42, now=datetime.now(UTC)
    )
    assert should
    assert "retry" in reason.lower()


def test_reconcile_skips_product_decision_issues(tmp_path):
    """Issues marked product-decision are not retried."""
    from datetime import UTC, datetime

    db = tmp_path / "test.db"
    settings = AutonomousDevSettings(state_db_path=db)
    store = StateStore(settings.state_db_path)

    class _MockGitHub:
        configured = True

        def list_open_issues_with_label(self, label, limit=100, state="open"):
            return [{"number": 42, "labels": [{"name": "needs-fix"}]}]

        def get_issue_body(self, num):
            return "## 🔄 PRODUCT_DECISION\n\nNeeds owner input"

        def get_issue_labels(self, num):
            return {"needs-fix", "cursor-task"}

    github = _MockGitHub()
    task_id = store.create_task(
        issue_number=42,
        status=TaskStatus.NEEDS_FIX,
        delivery_id="d1",
        execution_key="k1",
    )

    recovered = reconcile_needs_fix_and_stalled_reviews(settings, store, github)
    assert recovered["needs_fix"] == 0


def test_simplified_recovery_no_reactivation_tables_written():
    """Simplified recovery does not write to deprecated reactivation tables."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        db = Path(tmpdir) / "test.db"
        settings = AutonomousDevSettings(state_db_path=db)
        store = StateStore(settings.state_db_path)

        class _MockGitHub:
            configured = True

            def list_open_issues_with_label(self, label, limit=100, state="open"):
                return []

        github = _MockGitHub()

        # Run reconcile
        reconcile_needs_fix_and_stalled_reviews(settings, store, github)

        # Verify reactivation tables are empty (not used)
        with store._get_conn() as conn:
            worker_count = conn.execute(
                "SELECT COUNT(*) FROM worker_reactivations"
            ).fetchone()[0]
            reviewer_count = conn.execute(
                "SELECT COUNT(*) FROM reviewer_reactivations"
            ).fetchone()[0]
        assert worker_count == 0
        assert reviewer_count == 0
