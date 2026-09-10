"""Hourly watchdog fallback — scans stale runnable tasks."""

from __future__ import annotations

import logging
import threading
import uuid

import httpx

from autonomous_dev.config import get_autonomous_settings
from autonomous_dev.github_webhook import is_current_cursor_task
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


def stop_watchdog() -> None:
    _stop.set()


def _loop() -> None:
    settings = get_autonomous_settings()
    interval = getattr(settings, "watchdog_interval_seconds", 3600)
    while not _stop.wait(interval):
        try:
            _tick()
        except Exception:
            logger.exception("watchdog tick failed")


def _tick() -> None:
    settings = get_autonomous_settings()
    store = StateStore(settings.state_db_path)
    if store.is_locked():
        return
    if not settings.github_token:
        return

    url = f"https://api.github.com/repos/{settings.github_repo}/issues"
    headers = {
        "Authorization": f"Bearer {settings.github_token}",
        "Accept": "application/vnd.github+json",
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
        existing = store.get_task_by_issue(num)
        if existing and existing.status in {
            TaskStatus.RUNNING,
            TaskStatus.READY_FOR_REVIEW,
            TaskStatus.COMPLETED,
            TaskStatus.PRODUCT_DECISION,
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
