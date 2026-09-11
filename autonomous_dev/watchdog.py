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
from autonomous_dev.review_bridge import ReviewBridge
from autonomous_dev.state import StateStore, TaskStatus
from autonomous_dev.task_router import TaskRouter

logger = logging.getLogger(__name__)

_thread: threading.Thread | None = None
_stop = threading.Event()


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
    except Exception:
        logger.exception("watchdog startup tick failed")


def stop_watchdog() -> None:
    _stop.set()


def _loop() -> None:
    settings = get_autonomous_settings()
    interval = settings.watchdog_interval_seconds
    while not _stop.wait(interval):
        try:
            _tick()
        except Exception:
            logger.exception("watchdog tick failed")


def _tick() -> None:
    settings = get_autonomous_settings()
    store = StateStore(settings.state_db_path)
    if store.recover_stale_lease():
        logger.warning("watchdog recovered stale worker lease")

    token = resolve_github_token()
    if not token:
        logger.debug("watchdog skip: no GitHub token")
        return

    review_bridge = ReviewBridge(settings, store)
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
        review_bridge.notify_ready_for_review(task, commit_sha=task.commit_sha)

    if store.is_locked():
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
        if execution_key and store.get_active_execution(execution_key):
            continue
        existing = store.get_task_by_issue(num)
        if existing and existing.status in {
            TaskStatus.RUNNING,
            TaskStatus.READY_FOR_REVIEW,
            TaskStatus.COMPLETED,
            TaskStatus.PRODUCT_DECISION,
            TaskStatus.FAILED,
        }:
            continue
        logger.info("watchdog triggering issue #%s", num)
        router.handle(
            event_type="issues",
            action="labeled",
            delivery_id=f"watchdog-{num}-{uuid.uuid4()}",
            payload=payload,
        )
        break
