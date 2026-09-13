"""Simplified unified recovery — replaces worker_self_heal + reviewer_self_heal."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from autonomous_dev.config import AutonomousDevSettings
from autonomous_dev.github_client import (
    LABEL_CURRENT_TASK,
    LABEL_CURSOR_TASK,
    LABEL_NEEDS_FIX,
    LABEL_PRODUCT_DECISION,
    LABEL_WORKER_RUNNING,
    GitHubClient,
    GitHubClientError,
)
from autonomous_dev.state import (
    DeliveryStatus,
    ReviewInvocationStatus,
    StateStore,
    TaskRecord,
    TaskStatus,
)

logger = logging.getLogger(__name__)

PRODUCT_DECISION_MARKER = "## 🔄 PRODUCT_DECISION"
MAX_RETRY_ATTEMPTS = 5
RETRY_BACKOFF_BASE_SECONDS = 60


def _compute_retry_count(store: StateStore, issue_number: int) -> int:
    """Derive retry count from task history instead of separate table."""
    tasks = list(store.list_tasks_for_issue(issue_number, limit=MAX_RETRY_ATTEMPTS + 1))
    failed_count = sum(
        1 for t in tasks if t.status in {TaskStatus.NEEDS_FIX, TaskStatus.FAILED}
    )
    return min(failed_count, MAX_RETRY_ATTEMPTS)


def _should_retry_issue(
    store: StateStore,
    github: GitHubClient,
    issue_number: int,
    *,
    now: datetime,
) -> tuple[bool, str]:
    """Unified retry decision for both worker and reviewer failures."""
    # Check if already running
    if store.get_running_task_for_issue(issue_number):
        return False, "already running"

    # Check if lease held
    if store.is_locked():
        lease = store.get_lease()
        if lease.issue_number != issue_number:
            return False, "lease held by other issue"

    # Get latest task and derive retry count
    latest = store.get_task_by_issue(issue_number)
    if not latest:
        return False, "no task found"

    retry_count = _compute_retry_count(store, issue_number)
    if retry_count >= MAX_RETRY_ATTEMPTS:
        return False, f"retry budget exhausted ({retry_count}/{MAX_RETRY_ATTEMPTS})"

    # Check cooldown — last task updated_at + backoff
    if latest.updated_at:
        try:
            last_updated = datetime.fromisoformat(
                latest.updated_at.replace("Z", "+00:00")
            )
            if last_updated.tzinfo is None:
                last_updated = last_updated.replace(tzinfo=UTC)
            cooldown = RETRY_BACKOFF_BASE_SECONDS * (retry_count + 1)
            elapsed = (now - last_updated).total_seconds()
            if elapsed < cooldown:
                return False, f"backoff pending ({int(elapsed)}/{cooldown}s)"
        except ValueError:
            pass

    # For worker failures (needs-fix) — verify GitHub labels
    if latest.status in {TaskStatus.NEEDS_FIX, TaskStatus.FAILED}:
        try:
            body = github.get_issue_body(issue_number)
            if PRODUCT_DECISION_MARKER in body:
                return False, "product decision required"
            labels = github.get_issue_labels(issue_number)
            if LABEL_NEEDS_FIX not in labels:
                return False, "not labeled needs-fix"
        except GitHubClientError:
            pass  # Proceed with retry on GitHub error
        return True, "technical needs-fix retry"

    # For reviewer failures (ready-for-review stalled)
    if latest.status == TaskStatus.READY_FOR_REVIEW and latest.commit_sha:
        inv = store.get_review_invocation(latest.id, latest.commit_sha)
        if inv and inv.status == ReviewInvocationStatus.FAILED:
            return True, "reviewer retry"
        # Check staleness
        ref_time = (
            datetime.fromisoformat(latest.updated_at.replace("Z", "+00:00"))
            if latest.updated_at
            else now
        )
        if ref_time.tzinfo is None:
            ref_time = ref_time.replace(tzinfo=UTC)
        stale_seconds = 3600  # 1 hour
        if (now - ref_time).total_seconds() > stale_seconds:
            return True, "reviewer stalled"

    return False, "no retry condition met"


def _kick_worker_synthetic(
    settings: AutonomousDevSettings,
    store: StateStore,
    github: GitHubClient,
    issue_number: int,
    *,
    reason: str,
) -> dict[str, str]:
    """Simplified worker kick — generate synthetic webhook."""
    from autonomous_dev.task_router import TaskRouter

    # Verify issue still open
    try:
        issues = github.list_open_issues_with_label("", limit=100, state="open")
        if not any(int(iss.get("number", -1)) == issue_number for iss in issues):
            return {"status": "skipped", "reason": "issue closed"}
        body = github.get_issue_body(issue_number)
        labels = github.get_issue_labels(issue_number)
    except GitHubClientError as exc:
        return {"status": "failed", "reason": str(exc)[:200]}

    # Bump generation if needs-fix
    if LABEL_NEEDS_FIX in labels:
        stamp = datetime.now(UTC).isoformat()
        marker = f"\n\n<!-- retry-generation:{stamp} -->"
        if not body.endswith(marker[:50]):  # Avoid duplicate markers
            github.update_issue_body(issue_number, body.rstrip() + marker)
            body = github.get_issue_body(issue_number)

    # Create synthetic delivery
    delivery_id = f"retry-{issue_number}-{uuid.uuid4().hex[:8]}"
    if store.delivery_exists(delivery_id):
        return {"status": "skipped", "reason": "duplicate delivery"}

    payload = {
        "action": "labeled",
        "issue": {
            "number": issue_number,
            "state": "open",
            "title": "",
            "body": body,
            "labels": [{"name": LABEL_CURSOR_TASK}, {"name": LABEL_CURRENT_TASK}],
            "updated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    }
    store.record_delivery(
        delivery_id=delivery_id,
        event_type="issues",
        action="labeled",
        payload=payload,
        status=DeliveryStatus.RECEIVED,
    )

    router = TaskRouter(settings, store)
    result = router.handle(
        event_type="issues",
        action="labeled",
        delivery_id=delivery_id,
        payload=payload,
    )
    logger.info(
        "simple recovery kick issue=#%s reason=%s result=%s",
        issue_number,
        reason[:80],
        result.get("status"),
    )
    return result


def reconcile_needs_fix_and_stalled_reviews(
    settings: AutonomousDevSettings,
    store: StateStore,
    github: GitHubClient,
) -> dict[str, int]:
    """Unified reconciliation — replaces separate worker/reviewer self-heal."""
    recovered = {"needs_fix": 0, "reviewer": 0}
    now = datetime.now(UTC)

    # Scan needs-fix issues
    try:
        issues = github.list_open_issues_with_label(LABEL_NEEDS_FIX, limit=30)
        for issue in issues:
            num = int(issue["number"])
            should_retry, reason = _should_retry_issue(store, github, num, now=now)
            if should_retry:
                result = _kick_worker_synthetic(
                    settings, store, github, num, reason=reason
                )
                if result.get("status") == "worker_started":
                    recovered["needs_fix"] += 1
    except GitHubClientError:
        logger.debug("needs-fix scan failed")

    # Scan stalled ready-for-review
    for task in store.list_tasks_by_status(TaskStatus.READY_FOR_REVIEW, limit=30):
        should_retry, reason = _should_retry_issue(
            store, github, task.issue_number, now=now
        )
        if should_retry and "reviewer" in reason:
            # Reschedule review instead of kicking worker
            from autonomous_dev.review_executor import ReviewExecutor

            executor = ReviewExecutor(settings, store)
            if task.commit_sha:
                outcome = executor.schedule_review(task, commit_sha=task.commit_sha)
                if outcome.get("status") in {"scheduled", "retry_scheduled"}:
                    recovered["reviewer"] += 1
                    logger.info(
                        "simple recovery reviewer issue=#%s result=%s",
                        task.issue_number,
                        outcome.get("status"),
                    )

    # Clean stale worker-running labels
    if getattr(github, "configured", True):
        lease = store.get_lease()
        now_iso = datetime.now(UTC).isoformat()
        try:
            issues = github.list_open_issues_with_label(LABEL_WORKER_RUNNING, limit=20)
            for issue in issues:
                num = int(issue["number"])
                labels = {
                    lbl["name"]
                    for lbl in (issue.get("labels") or [])
                    if isinstance(lbl, dict)
                }
                if LABEL_WORKER_RUNNING not in labels:
                    continue
                if (
                    lease.locked
                    and lease.issue_number == num
                    and store._lease_is_valid(lease, now_iso=now_iso)
                ):
                    continue
                running = store.get_running_task_for_issue(num)
                if running and running.status in {TaskStatus.QUEUED, TaskStatus.RUNNING}:
                    if (
                        lease.locked
                        and lease.task_id == running.id
                        and store._lease_is_valid(lease, now_iso=now_iso)
                    ):
                        continue
                github.remove_label(num, LABEL_WORKER_RUNNING)
                logger.info("cleaned stale worker-running label issue=#%s", num)
        except GitHubClientError:
            pass

    return recovered
