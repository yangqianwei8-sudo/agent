"""DB-backed review worker — durable execution and stale recovery."""

from __future__ import annotations

import logging
import threading

from autonomous_dev.config import AutonomousDevSettings
from autonomous_dev.review_executor import ReviewExecutor
from autonomous_dev.state import StateStore

logger = logging.getLogger(__name__)

_worker_lock = threading.Lock()
_executor: ReviewExecutor | None = None


def _get_executor(settings: AutonomousDevSettings, store: StateStore) -> ReviewExecutor:
    global _executor
    if _executor is None:
        _executor = ReviewExecutor(settings, store)
    return _executor


def reset_review_worker_singleton() -> None:
    global _executor
    _executor = None


def process_due_reviews(
    settings: AutonomousDevSettings | None = None,
    store: StateStore | None = None,
) -> list[dict[str, str]]:
    """Process pending/retryable/stale-recovered review invocations."""
    from autonomous_dev.config import get_autonomous_settings

    settings = settings or get_autonomous_settings()
    store = store or StateStore(settings.state_db_path)
    executor = _get_executor(settings, store)

    with _worker_lock:
        store.recover_stale_reviewer_lock()
        recovered = store.recover_stale_running_reviews(
            older_than_seconds=settings.reviewer_lease_ttl_seconds,
        )
        for inv_id in recovered:
            logger.warning("recovered stale RUNNING review invocation_id=%s", inv_id)

        due = store.get_due_review_invocations(max_attempts=settings.review_max_attempts)
        results: list[dict[str, str]] = []
        for invocation in due:
            task = store.get_task(invocation.task_id)
            if task is None:
                continue
            owner = f"review-worker-{invocation.invocation_id[:8]}"
            if not store.try_acquire_reviewer_lock(
                task.id,
                owner=owner,
                ttl_seconds=settings.reviewer_lease_ttl_seconds,
            ):
                logger.debug(
                    "review worker blocked by lock invocation_id=%s",
                    invocation.invocation_id,
                )
                break
            try:
                result = executor.execute_review_invocation(
                    task,
                    invocation,
                )
                results.append(result)
            finally:
                store.release_reviewer_lock(owner)
        return results


def schedule_review_processing(
    settings: AutonomousDevSettings | None = None,
    store: StateStore | None = None,
) -> None:
    """Best-effort immediate processing; watchdog provides durability."""
    try:
        process_due_reviews(settings=settings, store=store)
    except Exception:
        logger.exception("review worker tick failed")
