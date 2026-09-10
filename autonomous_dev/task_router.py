"""Route GitHub webhook events to worker / review handoff."""

from __future__ import annotations

import logging
import threading
from typing import Any

from autonomous_dev.config import AutonomousDevSettings
from autonomous_dev.github_client import GitHubClient
from autonomous_dev.github_webhook import (
    is_current_cursor_task,
    is_main_push,
    issue_number,
    push_commit_sha,
)
from autonomous_dev.review_bridge import ReviewBridge
from autonomous_dev.state import DeliveryStatus, StateStore, TaskStatus
from autonomous_dev.worker import Worker

logger = logging.getLogger(__name__)


class TaskRouter:
    def __init__(
        self,
        settings: AutonomousDevSettings,
        store: StateStore,
        *,
        worker: Worker | None = None,
        review_bridge: ReviewBridge | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.worker = worker or Worker(settings, store)
        self.review_bridge = review_bridge or ReviewBridge()
        self._github = GitHubClient(settings)
        self._executor_lock = threading.Lock()

    def handle(
        self,
        *,
        event_type: str,
        action: str | None,
        delivery_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if event_type == "issues":
            return self._handle_issue(action, delivery_id, payload)
        if event_type == "push":
            return self._handle_push(delivery_id, payload)
        self.store.mark_delivery(delivery_id, status=DeliveryStatus.IGNORED)
        return {"status": "ignored", "event": event_type}

    def _handle_issue(
        self,
        action: str | None,
        delivery_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if action not in {"opened", "labeled", "edited", "reopened"}:
            self.store.mark_delivery(delivery_id, status=DeliveryStatus.IGNORED)
            return {"status": "ignored", "reason": f"action={action}"}

        if not is_current_cursor_task(payload):
            self.store.mark_delivery(delivery_id, status=DeliveryStatus.IGNORED)
            return {"status": "ignored", "reason": "not current cursor-task"}

        num = issue_number(payload)
        if num is None:
            self.store.mark_delivery(delivery_id, status=DeliveryStatus.FAILED, error="no issue number")
            return {"status": "failed", "reason": "no issue number"}

        if self.store.is_locked():
            self.store.mark_delivery(
                delivery_id,
                status=DeliveryStatus.IGNORED,
                error="worker lock held",
            )
            return {"status": "ignored", "reason": "concurrent worker blocked"}

        task = self.store.create_task(issue_number=num, delivery_id=delivery_id)
        if not self.store.try_acquire_lock(num, task.id):
            self.store.mark_delivery(
                delivery_id,
                status=DeliveryStatus.IGNORED,
                error="lock acquire failed",
            )
            return {"status": "ignored", "reason": "lock acquire failed"}

        issue_body = (payload.get("issue") or {}).get("body") or ""
        self._spawn_worker(task, issue_body)
        self.store.mark_delivery(delivery_id, status=DeliveryStatus.PROCESSED)
        return {"status": "worker_started", "task_id": task.id, "issue_number": num}

    def _spawn_worker(self, task, issue_body: str) -> None:
        def _run() -> None:
            with self._executor_lock:
                result = self.worker.run_task(task, issue_body=issue_body)
                logger.info(
                    "worker finished task=%s status=%s commit=%s",
                    result.task_id,
                    result.status,
                    result.commit_sha,
                )

        thread = threading.Thread(target=_run, name=f"worker-task-{task.id}", daemon=True)
        thread.start()

    def run_worker_sync(self, task, *, issue_body: str = "") -> Any:
        """Synchronous worker execution for tests."""
        return self.worker.run_task(task, issue_body=issue_body)

    def _handle_push(self, delivery_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not is_main_push(payload):
            self.store.mark_delivery(delivery_id, status=DeliveryStatus.IGNORED)
            return {"status": "ignored", "reason": "not main push"}

        commit_sha = push_commit_sha(payload)
        if not commit_sha:
            self.store.mark_delivery(delivery_id, status=DeliveryStatus.FAILED, error="no commit")
            return {"status": "failed", "reason": "no commit"}

        task = self.store.get_task_by_commit(commit_sha)
        if task is None:
            active = self.store.get_active_task()
            if active and active.status == TaskStatus.RUNNING:
                task = self.store.update_task(
                    active.id,
                    status=TaskStatus.READY_FOR_REVIEW,
                    commit_sha=commit_sha,
                )
            else:
                self.store.mark_delivery(delivery_id, status=DeliveryStatus.IGNORED)
                return {"status": "ignored", "reason": "no correlated task"}

        if task.status != TaskStatus.READY_FOR_REVIEW:
            task = self.store.update_task(
                task.id,
                status=TaskStatus.READY_FOR_REVIEW,
                commit_sha=commit_sha,
            )

        self._github.sync_ready_for_review(task.issue_number)
        self.review_bridge.notify_ready_for_review(task, commit_sha=commit_sha)
        self.store.mark_delivery(delivery_id, status=DeliveryStatus.PROCESSED)
        return {
            "status": "ready_for_review",
            "task_id": task.id,
            "issue_number": task.issue_number,
            "commit_sha": commit_sha,
        }
