"""DB-backed review worker — durable execution and stale recovery."""

from __future__ import annotations

import logging
import threading

from autonomous_dev.config import AutonomousDevSettings
from autonomous_dev.review_executor import ReviewExecutor
from autonomous_dev.state import StateStore

logger = logging.getLogger(__name__)

_worker_lock = threading.RLock()
_kick_lock = threading.Lock()
_kick_thread: threading.Thread | None = None
_kick_pending = threading.Event()
_executor: ReviewExecutor | None = None


def _get_executor(settings: AutonomousDevSettings, store: StateStore) -> ReviewExecutor:
    global _executor
    if _executor is None:
        _executor = ReviewExecutor(settings, store)
    return _executor


def reset_review_worker_singleton() -> None:
    global _executor, _kick_thread
    _executor = None
    _kick_thread = None
    _kick_pending.clear()


def _run_kicked_reviews(
    settings: AutonomousDevSettings | None,
    store: StateStore | None,
) -> None:
    """Drain pending review kicks without blocking webhook/event-loop threads."""
    while True:
        _kick_pending.wait(0.05)
        _kick_pending.clear()
        try:
            process_due_reviews(settings=settings, store=store)
        except Exception:
            logger.exception("review worker tick failed")
        if not _kick_pending.is_set():
            break


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
        if store.recover_orphaned_reviewer_lock():
            logger.warning("recovered orphaned reviewer lock")
        finalized = store.finalize_obsolete_review_invocations()
        for inv_id in finalized:
            logger.info("finalized obsolete review invocation_id=%s", inv_id)
        recovered = store.recover_stale_running_reviews(
            older_than_seconds=settings.reviewer_lease_ttl_seconds,
        )
        for inv_id in recovered:
            logger.warning("recovered stale RUNNING review invocation_id=%s", inv_id)

    results: list[dict[str, str]] = []
    for _ in range(20):
        with _worker_lock:
            due = store.get_due_review_invocations(max_attempts=settings.review_max_attempts)
        if not due:
            break
        progress = False
        for invocation in due:
            with _worker_lock:
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
                    continue
            try:
                result = executor.execute_review_invocation(
                    task,
                    invocation,
                )
                results.append(result)
                progress = True
            finally:
                store.release_reviewer_lock(owner)
        if not progress:
            break
    return results


def schedule_review_processing(
    settings: AutonomousDevSettings | None = None,
    store: StateStore | None = None,
) -> None:
    """Best-effort immediate processing in background; watchdog provides durability."""
    thread_name = threading.current_thread().name
    if thread_name.startswith("review-worker"):
        try:
            process_due_reviews(settings=settings, store=store)
        except Exception:
            logger.exception("review worker tick failed")
        return

    global _kick_thread
    _kick_pending.set()
    with _kick_lock:
        if _kick_thread is not None and _kick_thread.is_alive():
            return
        _kick_thread = threading.Thread(
            target=_run_kicked_reviews,
            args=(settings, store),
            name="review-worker-kick",
            daemon=True,
        )
        _kick_thread.start()
