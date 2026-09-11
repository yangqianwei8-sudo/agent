"""Route GitHub webhook events to worker / review handoff."""

from __future__ import annotations

import logging
import threading
from typing import Any

from autonomous_dev.config import AutonomousDevSettings
from autonomous_dev.execution_identity import compute_execution_key
from autonomous_dev.github_client import GitHubClient, GitHubClientError
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
        self.review_bridge = review_bridge or ReviewBridge(settings, store)
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

        issue = payload.get("issue") or {}
        if issue.get("state") != "open":
            self.store.mark_delivery(delivery_id, status=DeliveryStatus.IGNORED)
            return {"status": "ignored", "reason": "issue not open"}

        if not is_current_cursor_task(payload):
            self.store.mark_delivery(delivery_id, status=DeliveryStatus.IGNORED)
            return {"status": "ignored", "reason": "not current cursor-task"}

        num = issue_number(payload)
        if num is None:
            self.store.mark_delivery(delivery_id, status=DeliveryStatus.FAILED, error="no issue number")
            return {"status": "failed", "reason": "no issue number"}

        execution_key = compute_execution_key(self.settings, payload)
        if execution_key:
            active = self.store.get_active_execution(execution_key)
            if active is not None:
                self.store.mark_delivery(
                    delivery_id,
                    status=DeliveryStatus.IGNORED,
                    error=f"active execution exists task_id={active.id}",
                )
                return {
                    "status": "ignored",
                    "reason": "duplicate active execution",
                    "execution_key": execution_key,
                    "task_id": active.id,
                }

        self.store.recover_stale_lease()
        if self.store.is_locked():
            self.store.mark_delivery(
                delivery_id,
                status=DeliveryStatus.IGNORED,
                error="worker lease held",
            )
            return {"status": "ignored", "reason": "concurrent worker blocked"}

        task = self.store.create_task(
            issue_number=num,
            delivery_id=delivery_id,
            execution_key=execution_key,
        )
        lease_owner = f"worker-{task.id}"
        if not self.store.try_acquire_lease(
            num,
            task.id,
            owner=lease_owner,
            ttl_seconds=self.settings.worker_lease_ttl_seconds,
        ):
            self.store.mark_delivery(
                delivery_id,
                status=DeliveryStatus.IGNORED,
                error="lease acquire failed",
            )
            return {"status": "ignored", "reason": "lease acquire failed"}

        issue_body = issue.get("body") or ""
        self._spawn_worker(task, issue_body, lease_owner=lease_owner)
        self.store.mark_delivery(delivery_id, status=DeliveryStatus.PROCESSED)
        return {
            "status": "worker_started",
            "task_id": task.id,
            "issue_number": num,
            "execution_key": execution_key,
        }

    def _spawn_worker(self, task, issue_body: str, *, lease_owner: str) -> None:
        def _run() -> None:
            with self._executor_lock:
                result = self.worker.run_task(
                    task,
                    issue_body=issue_body,
                    lease_owner=lease_owner,
                )
                logger.info(
                    "worker finished task=%s status=%s commit=%s",
                    result.task_id,
                    result.status,
                    result.commit_sha,
                )

        thread = threading.Thread(target=_run, name=f"worker-task-{task.id}", daemon=True)
        thread.start()

    def run_worker_sync(
        self, task, *, issue_body: str = "", lease_owner: str | None = None
    ) -> Any:
        """Synchronous worker execution for tests."""
        owner = lease_owner or f"worker-{task.id}"
        return self.worker.run_task(task, issue_body=issue_body, lease_owner=owner)

    def seal_review_pass(self, issue_number: int) -> dict[str, Any]:
        """Reviewer PASS path — only from ready-for-review to completed."""
        task = self.store.get_task_by_issue(issue_number)
        if task is None:
            raise ValueError(f"no task for issue #{issue_number}")
        if task.status != TaskStatus.READY_FOR_REVIEW:
            raise ValueError(
                f"issue #{issue_number} task status={task.status}, expected ready-for-review"
            )
        self._github.sync_completed(issue_number)
        updated = self.store.update_task(task.id, status=TaskStatus.COMPLETED)
        return {
            "status": "completed",
            "task_id": updated.id,
            "issue_number": issue_number,
        }

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
            self.store.mark_delivery(delivery_id, status=DeliveryStatus.IGNORED)
            return {"status": "ignored", "reason": "no correlated task"}

        if task.status == TaskStatus.READY_FOR_REVIEW and task.commit_sha:
            stored = task.commit_sha
            if stored == commit_sha or commit_sha.startswith(stored) or stored.startswith(commit_sha[:12]):
                self.store.mark_delivery(delivery_id, status=DeliveryStatus.PROCESSED)
                return {
                    "status": "ready_for_review",
                    "task_id": task.id,
                    "issue_number": task.issue_number,
                    "commit_sha": commit_sha,
                    "idempotent": True,
                }

        if task.status not in {TaskStatus.RUNNING, TaskStatus.QUEUED}:
            self.store.mark_delivery(delivery_id, status=DeliveryStatus.IGNORED)
            return {"status": "ignored", "reason": f"task status={task.status}"}

        try:
            task = self.store.update_task(
                task.id,
                status=TaskStatus.READY_FOR_REVIEW,
                commit_sha=commit_sha,
            )
            self._github.sync_ready_for_review(task.issue_number)
            self.review_bridge.notify_ready_for_review(task, commit_sha=commit_sha)
        except GitHubClientError as exc:
            self.store.mark_delivery(delivery_id, status=DeliveryStatus.FAILED, error=str(exc))
            return {"status": "failed", "reason": str(exc)}

        self.store.mark_delivery(delivery_id, status=DeliveryStatus.PROCESSED)
        return {
            "status": "ready_for_review",
            "task_id": task.id,
            "issue_number": task.issue_number,
            "commit_sha": commit_sha,
        }
