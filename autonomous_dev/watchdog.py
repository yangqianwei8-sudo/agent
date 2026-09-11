"""Hourly watchdog fallback — stale lock recovery + overdue review scan."""

from __future__ import annotations

import logging
import threading
import uuid

import httpx

from autonomous_dev.config import get_autonomous_settings
from autonomous_dev.execution_identity import compute_execution_key
from autonomous_dev.github_auth import resolve_github_token
from autonomous_dev.github_webhook import is_current_cursor_task
from autonomous_dev.state import StateStore, TaskStatus
from autonomous_dev.task_router import TaskRouter

logger = logging.getLogger(__name__)

_thread: threading.Thread | None = None
_stop = threading.Event()


def is_current_task_recoverable(
    store: StateStore,
    *,
    execution_key: str | None,
    issue_number: int,
) -> bool:
    """True when a GitHub current-task should trigger worker via watchdog fallback.

    Exactly-once is keyed by (repo, issue, generation). A prior FAILED/NEEDS_FIX
    record for an older generation must not block reactivation with a fresh
    updated_at, but the same generation must never spawn duplicate workers.
    """
    if execution_key:
        if store.get_active_execution(execution_key) is not None:
            return False
        if store.get_task_by_execution_key(execution_key) is not None:
            return False

    in_flight = store.get_running_task_for_issue(issue_number)
    if in_flight is not None:
        return False

    latest = store.get_task_by_issue(issue_number)
    if (
        latest is not None
        and latest.status == TaskStatus.READY_FOR_REVIEW
        and execution_key
        and latest.execution_key == execution_key
    ):
        return False

    return True


def start_watchdog() -> None:
    global _thread
    settings = get_autonomous_settings()
    if not settings.autonomous_dev_enabled:
        return
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="autonomous-watchdog", daemon=True)
    _thread.start()
    logger.info("watchdog started interval=%ss", settings.watchdog_interval_seconds)
    # Recover missed activations promptly when webhook delivery is delayed/unavailable.
    threading.Thread(target=_startup_tick, name="autonomous-watchdog-startup", daemon=True).start()


def _startup_tick() -> None:
    if _stop.wait(3):
        return
    try:
        _tick()
        settings = get_autonomous_settings()
        _scan_current_tasks(settings)
    except Exception:
        logger.exception("watchdog startup tick failed")


def stop_watchdog() -> None:
    _stop.set()


def _loop() -> None:
    settings = get_autonomous_settings()
    review_interval = max(1, settings.review_recovery_interval_seconds)
    watchdog_interval = settings.watchdog_interval_seconds
    elapsed = 0
    while not _stop.wait(review_interval):
        elapsed += review_interval
        try:
            _tick()
        except Exception:
            logger.exception("watchdog tick failed")
        if elapsed >= watchdog_interval:
            elapsed = 0
            try:
                _tick_full_scan(settings)
            except Exception:
                logger.exception("watchdog full scan failed")


def _tick() -> None:
    settings = get_autonomous_settings()
    store = StateStore(settings.state_db_path)
    if store.recover_stale_lease():
        logger.warning("watchdog recovered stale worker lease")

    from autonomous_dev.review_worker import process_due_reviews

    try:
        process_due_reviews(settings, store)
    except Exception:
        logger.exception("watchdog review recovery failed")


def _tick_full_scan(settings) -> None:
    store = StateStore(settings.state_db_path)
    stale_tasks = store.get_stale_ready_for_review_tasks(
        older_than_seconds=settings.review_watchdog_stale_seconds,
    )
    for task in stale_tasks:
        _, count = store.get_review_trigger(task.id)
        if count >= 3:
            logger.info("watchdog skip review re-trigger task=%s count=%s", task.id, count)
            continue
        if not task.commit_sha:
            continue
        logger.info("watchdog review fallback task=%s issue=#%s", task.id, task.issue_number)
        from autonomous_dev.review_executor import ReviewExecutor

        executor = ReviewExecutor(settings, store)
        executor.schedule_review(task, commit_sha=task.commit_sha)

    _scan_current_tasks(settings)


def _scan_current_tasks(settings) -> None:
    store = StateStore(settings.state_db_path)
    store.recover_stale_lease()
    if store.is_locked():
        return

    token = resolve_github_token()
    if not token:
        logger.debug("watchdog skip: no GitHub token")
        return

    url = f"https://api.github.com/repos/{settings.github_repo}/issues"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(
            url,
            headers=headers,
            params={"state": "open", "labels": "current-task", "per_page": 10},
        )
        resp.raise_for_status()
        issues = resp.json()

    router = TaskRouter(settings, store)
    for issue in issues:
        payload = {"issue": issue, "action": "labeled"}
        if not is_current_cursor_task(payload):
            continue
        num = issue["number"]
        execution_key = compute_execution_key(settings, payload)
        if not is_current_task_recoverable(
            store,
            execution_key=execution_key,
            issue_number=num,
        ):
            continue
        logger.info("watchdog triggering issue #%s", num)
        router.handle(
            event_type="issues",
            action="labeled",
            delivery_id=f"watchdog-{num}-{uuid.uuid4()}",
            payload=payload,
        )
        break
