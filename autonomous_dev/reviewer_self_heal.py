"""Reviewer-stage stall recovery — bounded retry without product intervention."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from autonomous_dev.config import AutonomousDevSettings
from autonomous_dev.review_executor import ReviewExecutor
from autonomous_dev.state import (
    ReviewerReactivationStatus,
    ReviewInvocationStatus,
    ReviewVerdict,
    StateStore,
    TaskRecord,
    TaskStatus,
)

logger = logging.getLogger(__name__)


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except ValueError:
        return None


def _task_reference_time(store: StateStore, task: TaskRecord) -> datetime | None:
    ref = _parse_ts(task.updated_at)
    trigger_at, _ = store.get_review_trigger(task.id)
    trigger_ts = _parse_ts(trigger_at)
    if trigger_ts is not None and (ref is None or trigger_ts > ref):
        ref = trigger_ts
    return ref


def _has_valid_reviewer_lease(
    settings: AutonomousDevSettings,
    store: StateStore,
    task: TaskRecord,
    inv,
    *,
    now: datetime,
) -> bool:
    """True only when an in-flight review is actively backed by a non-stale lease."""
    if inv.status == ReviewInvocationStatus.RUNNING:
        started = _parse_ts(inv.started_at)
        if started is not None:
            return (now - started).total_seconds() < settings.reviewer_lease_ttl_seconds
        return False
    if inv.status != ReviewInvocationStatus.PENDING:
        return False
    lock = store.get_reviewer_lock()
    if not lock.locked or lock.task_id != task.id:
        return False
    expires = _parse_ts(lock.lease_expires_at)
    if expires is not None and expires <= now:
        return False
    acquired = _parse_ts(lock.acquired_at)
    if acquired is None:
        return False
    # Brief grace while PENDING transitions to RUNNING after lock acquire.
    return (now - acquired).total_seconds() < min(30, settings.reviewer_lease_ttl_seconds)


def _invocation_blocks_stall(
    settings: AutonomousDevSettings,
    store: StateStore,
    task: TaskRecord,
    *,
    now: datetime,
) -> bool:
    if not task.commit_sha:
        return True
    inv = store.get_review_invocation(task.id, task.commit_sha)
    if inv is None:
        return False
    if inv.status == ReviewInvocationStatus.COMPLETED:
        if inv.verdict == ReviewVerdict.SKIP.value:
            return False
        return True
    if inv.status in {ReviewInvocationStatus.RUNNING, ReviewInvocationStatus.PENDING}:
        if _has_valid_reviewer_lease(settings, store, task, inv, now=now):
            return True
    if inv.status == ReviewInvocationStatus.FAILED:
        if not store.is_review_retryable(inv, max_attempts=settings.review_max_attempts):
            return True
        retry_at = _parse_ts(inv.next_retry_at)
        if retry_at is not None and retry_at > now:
            return True
    return False


def is_reviewer_stalled(
    settings: AutonomousDevSettings,
    store: StateStore,
    task: TaskRecord,
    *,
    now: datetime | None = None,
) -> bool:
    if task.status != TaskStatus.READY_FOR_REVIEW or not task.commit_sha:
        return False
    now = now or datetime.now(UTC)
    if _invocation_blocks_stall(settings, store, task, now=now):
        return False
    ref = _task_reference_time(store, task)
    if ref is None:
        return True
    return (now - ref).total_seconds() >= settings.reviewer_stall_seconds


def _reactivation_is_due(store: StateStore, issue_number: int, *, now_iso: str) -> bool:
    record = store.get_reviewer_reactivation(issue_number)
    if record is None:
        return True
    if record.status == ReviewerReactivationStatus.EXHAUSTED:
        return False
    if record.next_retry_at is None:
        return True
    return record.next_retry_at <= now_iso


def _cooldown_blocks_kick(record, *, cooldown_seconds: int, now: datetime) -> bool:
    if not record or not record.last_kick_at:
        return False
    last = _parse_ts(record.last_kick_at)
    if last is None:
        return False
    return (now - last).total_seconds() < cooldown_seconds


def kick_reviewer_reactivation(
    settings: AutonomousDevSettings,
    store: StateStore,
    task: TaskRecord,
    *,
    reason: str,
    recovery_status: ReviewerReactivationStatus = ReviewerReactivationStatus.PENDING,
) -> dict[str, str]:
    """Schedule or retry review for a stalled ready-for-review task."""
    if not task.commit_sha:
        return {"status": "skipped", "reason": "missing commit_sha"}

    now = datetime.now(UTC)
    now_iso = now.isoformat()
    record = store.get_reviewer_reactivation(task.issue_number)

    if record and record.status == ReviewerReactivationStatus.EXHAUSTED:
        last_kick = _parse_ts(record.last_kick_at)
        exhausted_cooldown_seconds = 3600
        if last_kick is not None and (now - last_kick).total_seconds() < exhausted_cooldown_seconds:
            return {"status": "skipped", "reason": "recovery budget exhausted (cooldown active)"}
        logger.info(
            "reviewer self-heal: resetting exhausted status for issue=#%s after cooldown",
            task.issue_number
        )
        store.upsert_reviewer_reactivation(
            issue_number=task.issue_number,
            task_id=task.id,
            attempt_count=0,
            next_retry_at=None,
            last_error="reset after exhausted cooldown",
            status=ReviewerReactivationStatus.PENDING,
            last_kick_at=None,
        )
        record = store.get_reviewer_reactivation(task.issue_number)
    if not _reactivation_is_due(store, task.issue_number, now_iso=now_iso):
        return {"status": "skipped", "reason": "backoff pending"}
    if _cooldown_blocks_kick(
        record,
        cooldown_seconds=settings.review_retry_backoff_seconds,
        now=now,
    ):
        return {"status": "skipped", "reason": "kick cooldown"}

    store.recover_stale_reviewer_lock()
    store.recover_orphaned_reviewer_lock(
        stall_seconds=settings.reviewer_orphan_lock_stall_seconds,
    )
    for inv_id in store.recover_stale_running_reviews(
        older_than_seconds=settings.reviewer_lease_ttl_seconds,
    ):
        logger.warning("reviewer self-heal reclaimed stale RUNNING invocation_id=%s", inv_id)

    attempt = (record.attempt_count if record else 0) + 1
    if attempt > settings.review_max_attempts:
        store.upsert_reviewer_reactivation(
            issue_number=task.issue_number,
            task_id=task.id,
            attempt_count=attempt,
            next_retry_at=None,
            last_error=reason[:2000],
            status=ReviewerReactivationStatus.EXHAUSTED,
            last_kick_at=now_iso,
        )
        return {"status": "exhausted", "reason": "recovery attempts exceeded"}

    executor = ReviewExecutor(settings, store)
    outcome = executor.schedule_review(task, commit_sha=task.commit_sha)
    schedule_status = outcome.get("status", "unknown")

    next_status = recovery_status
    if schedule_status == "retry_scheduled":
        next_status = ReviewerReactivationStatus.RETRYING
    elif schedule_status in {"scheduled", "already_scheduled"}:
        next_status = ReviewerReactivationStatus.RUNNING
    elif schedule_status == "failed_permanent":
        next_status = ReviewerReactivationStatus.EXHAUSTED

    delay = settings.review_retry_backoff_seconds * attempt
    next_retry = (now + timedelta(seconds=delay)).isoformat()
    store.upsert_reviewer_reactivation(
        issue_number=task.issue_number,
        task_id=task.id,
        attempt_count=attempt,
        next_retry_at=next_retry if schedule_status not in {"scheduled", "already_scheduled", "idempotent"} else None,
        last_error=reason[:2000],
        status=next_status,
        last_kick_at=now_iso,
    )

    from autonomous_dev.review_worker import process_due_reviews

    process_due_reviews(settings, store)
    logger.info(
        "reviewer self-heal kick issue=#%s task=%s attempt=%s schedule=%s reason=%s",
        task.issue_number,
        task.id,
        attempt,
        schedule_status,
        reason[:120],
    )
    return {k: str(v) for k, v in outcome.items()}


def reconcile_stalled_ready_for_review(
    settings: AutonomousDevSettings,
    store: StateStore,
) -> int:
    """Detect ready-for-review tasks with no completed verdict and reschedule review."""
    recovered = 0
    now = datetime.now(UTC)
    seen: set[int] = set()

    for record in store.list_due_reviewer_reactivations(limit=20):
        num = record.issue_number
        seen.add(num)
        task = store.get_task(record.task_id) if record.task_id else store.get_task_by_issue(num)
        if task is None or task.status != TaskStatus.READY_FOR_REVIEW:
            continue
        if not is_reviewer_stalled(settings, store, task, now=now):
            continue
        result = kick_reviewer_reactivation(
            settings,
            store,
            task,
            reason="loop_recovery reviewer reactivation",
            recovery_status=ReviewerReactivationStatus.STALE_RECOVERED,
        )
        if result.get("status") not in {"skipped", "deferred", "exhausted"}:
            recovered += 1

    for task in store.list_tasks_by_status(TaskStatus.READY_FOR_REVIEW, limit=50):
        if task.issue_number in seen:
            continue
        if not is_reviewer_stalled(settings, store, task, now=now):
            continue
        result = kick_reviewer_reactivation(
            settings,
            store,
            task,
            reason="loop_recovery stalled ready-for-review",
            recovery_status=ReviewerReactivationStatus.STALE_RECOVERED,
        )
        if result.get("status") not in {"skipped", "deferred", "exhausted"}:
            recovered += 1

    return recovered


def derive_reviewer_self_heal_dashboard_state(
    store: StateStore,
    *,
    issue_number: int | None,
    primary_status: TaskStatus | None,
    active_review_status: ReviewInvocationStatus | None = None,
) -> dict[str, str | int | None] | None:
    if issue_number is None:
        return None
    record = store.get_reviewer_reactivation(issue_number)
    if record is None and primary_status != TaskStatus.READY_FOR_REVIEW:
        return None
    if record is None:
        if active_review_status == ReviewInvocationStatus.RUNNING:
            return {
                "status": ReviewerReactivationStatus.RUNNING.value,
                "attempt_count": 0,
                "max_attempts": None,
                "next_retry_at": None,
                "last_error": None,
                "reason": "reviewer invocation running",
            }
        if active_review_status == ReviewInvocationStatus.PENDING:
            return {
                "status": ReviewerReactivationStatus.PENDING.value,
                "attempt_count": 0,
                "max_attempts": None,
                "next_retry_at": None,
                "last_error": None,
                "reason": "review pending",
            }
        return {
            "status": ReviewerReactivationStatus.PENDING.value,
            "attempt_count": 0,
            "max_attempts": None,
            "next_retry_at": None,
            "last_error": None,
            "reason": "ready-for-review awaiting reviewer",
        }
    return {
        "status": record.status.value,
        "attempt_count": record.attempt_count,
        "max_attempts": None,
        "next_retry_at": record.next_retry_at,
        "last_kick_at": record.last_kick_at,
        "last_error": record.last_error,
        "reason": record.last_error,
    }
