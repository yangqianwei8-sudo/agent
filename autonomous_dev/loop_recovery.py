"""Autonomous loop stall recovery — orphan pushes, stale labels, DB reconciliation."""

from __future__ import annotations

import logging
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from autonomous_dev.config import AutonomousDevSettings
from autonomous_dev.github_auth import git_env
from autonomous_dev.github_client import (
    LABEL_WORKER_RUNNING,
    GitHubClient,
    GitHubClientError,
)
from autonomous_dev.review_bridge import ReviewBridge
from autonomous_dev.review_handoff import transition_ready_for_review
from autonomous_dev.state import StateStore, TaskRecord, TaskStatus

logger = logging.getLogger(__name__)

_PROGRESS_EVENT_TYPES = frozenset(
    {
        "CURSOR_STARTED",
        "CURSOR_PROGRESS",
        "CURSOR_FINISHED",
        "FILE_READ",
        "FILE_EDIT",
        "FILE_CREATE",
        "FILE_DELETE",
        "COMMAND_STARTED",
        "COMMAND_FINISHED",
        "COMMIT_CREATED",
        "PUSH_FINISHED",
        "TEST_STARTED",
        "TEST_FINISHED",
    }
)


def run_startup_recovery(settings: AutonomousDevSettings, store: StateStore) -> None:
    """Run once on service boot — clear orphan leases and reconcile persisted state."""
    store.recover_stale_lease(
        heartbeat_ttl_seconds=settings.worker_lease_ttl_seconds,
        progress_grace_seconds=settings.cursor_long_op_suspect_seconds,
    )
    run_loop_recovery_tick(settings, store)


def run_loop_recovery_tick(settings: AutonomousDevSettings, store: StateStore) -> dict[str, int]:
    """Periodic watchdog recovery — bounded, idempotent."""
    counts = {
        "orphan_pushes": 0,
        "stale_labels": 0,
        "stale_db_running": 0,
        "technical_needs_fix": 0,
        "stalled_ready_for_review": 0,
    }
    github = GitHubClient(settings)
    review_bridge = ReviewBridge(settings, store)

    try:
        counts["orphan_pushes"] = reconcile_orphan_pushes(
            settings, store, github, review_bridge
        )
    except Exception:
        logger.exception("orphan push reconcile failed")

    try:
        counts["stale_labels"] = cleanup_stale_worker_running_labels(store, github)
    except Exception:
        logger.exception("stale worker-running cleanup failed")

    try:
        counts["stale_db_running"] = reconcile_stale_running_db_records(store)
    except Exception:
        logger.exception("stale running DB reconcile failed")

    try:
        from autonomous_dev.recovery_simple import (
            reconcile_needs_fix_and_stalled_reviews,
        )

        recovered = reconcile_needs_fix_and_stalled_reviews(settings, store, github)
        counts["technical_needs_fix"] = recovered["needs_fix"]
        counts["stalled_ready_for_review"] = recovered["reviewer"]
    except Exception:
        logger.exception("unified recovery failed")
    return counts


def reconcile_orphan_pushes(
    settings: AutonomousDevSettings,
    store: StateStore,
    github: GitHubClient,
    review_bridge: ReviewBridge,
) -> int:
    """Failed/running tasks with no commit_sha but a main commit after start → ready-for-review."""
    reconciled = 0
    candidates = store.list_tasks_for_push_reconcile(
        statuses=(TaskStatus.FAILED, TaskStatus.RUNNING, TaskStatus.NEEDS_FIX),
        limit=20,
    )
    for task in candidates:
        if task.commit_sha:
            continue
        sha = _find_orphan_commit_for_task(settings.repo_root, task)
        if not sha:
            continue
        try:
            updated = transition_ready_for_review(
                store, github, review_bridge, task, sha
            )
            logger.warning(
                "reconciled orphan push task=%s issue=#%s sha=%s status=%s",
                task.id,
                task.issue_number,
                sha[:12],
                updated.status.value,
            )
            reconciled += 1
        except GitHubClientError as exc:
            logger.warning(
                "orphan push reconcile label sync failed task=%s: %s",
                task.id,
                exc,
            )
        except Exception:
            logger.exception("orphan push reconcile failed task=%s", task.id)
    return reconciled


def cleanup_stale_worker_running_labels(
    store: StateStore,
    github: GitHubClient,
) -> int:
    """Remove worker-running when no valid lease backs the issue."""
    if not getattr(github, "configured", True):
        return 0
    cleaned = 0
    lease = store.get_lease()
    now_iso = datetime.now(UTC).isoformat()
    try:
        issues = github.list_open_issues_with_label(LABEL_WORKER_RUNNING, limit=30)
    except GitHubClientError:
        return 0
    for issue in issues:
        num = int(issue["number"])
        labels = {lbl["name"] for lbl in (issue.get("labels") or []) if isinstance(lbl, dict)}
        if LABEL_WORKER_RUNNING not in labels:
            continue
        if lease.locked and lease.issue_number == num and store._lease_is_valid(lease, now_iso=now_iso):
            continue
        running = store.get_running_task_for_issue(num)
        if running is not None and running.status in {TaskStatus.QUEUED, TaskStatus.RUNNING}:
            if lease.locked and lease.task_id == running.id and store._lease_is_valid(
                lease, now_iso=now_iso
            ):
                continue
        try:
            github.remove_label(num, LABEL_WORKER_RUNNING)
            cleaned += 1
            logger.info("cleared stale worker-running label issue=#%s", num)
        except GitHubClientError:
            logger.warning("failed clearing worker-running issue=#%s", num)
    return cleaned


def reconcile_stale_running_db_records(store: StateStore) -> int:
    """Mark orphan running/queued DB rows failed when lease is not held."""
    fixed = 0
    lease = store.get_lease()
    now_iso = datetime.now(UTC).isoformat()
    for task in store.list_tasks_by_status(
        TaskStatus.RUNNING, TaskStatus.QUEUED, limit=50
    ):
        if lease.locked and lease.task_id == task.id and store._lease_is_valid(
            lease, now_iso=now_iso
        ):
            continue
        last_progress = store.get_task_last_progress_at(task.id)
        if last_progress is not None:
            age = (datetime.now(UTC) - last_progress).total_seconds()
            if age < 900:
                continue
        store.update_task(
            task.id,
            status=TaskStatus.FAILED,
            error="orphan running record reconciled",
        )
        fixed += 1
    return fixed


def _find_orphan_commit_for_task(repo_root: Path, task: TaskRecord) -> str | None:
    since = task.created_at or task.updated_at
    if not since:
        return None
    try:
        result = subprocess.run(
            [
                "git",
                "log",
                "origin/main",
                f"--since={since}",
                "--format=%H %s",
                "-20",
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            env=git_env(),
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    issue_ref = f"issue #{task.issue_number}"
    issue_ref_alt = f"issue #{task.issue_number}"
    for line in result.stdout.splitlines():
        parts = line.strip().split(" ", 1)
        if len(parts) < 1:
            continue
        sha = parts[0]
        msg = parts[1] if len(parts) > 1 else ""
        if issue_ref in msg.lower() or issue_ref_alt in msg.lower():
            return sha
        if task.issue_number == 24 and "auto handoff" in msg.lower():
            return sha
    return None
