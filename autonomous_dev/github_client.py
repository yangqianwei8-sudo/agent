"""GitHub REST API — issue labels and review comments."""

from __future__ import annotations

import logging

import httpx

from autonomous_dev.config import AutonomousDevSettings

logger = logging.getLogger(__name__)

LABEL_CURSOR_TASK = "cursor-task"
LABEL_CURRENT_TASK = "current-task"
LABEL_WORKER_RUNNING = "worker-running"
LABEL_READY_FOR_REVIEW = "ready-for-review"
LABEL_NEEDS_FIX = "needs-fix"
LABEL_PRODUCT_DECISION = "product-decision"
LABEL_COMPLETED = "completed"


class GitHubClient:
    def __init__(self, settings: AutonomousDevSettings) -> None:
        self._settings = settings
        self._base = "https://api.github.com"

    @property
    def configured(self) -> bool:
        return bool(self._settings.github_token)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._settings.github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def set_issue_labels(self, issue_number: int, labels: set[str]) -> None:
        if not self.configured:
            return
        url = f"{self._base}/repos/{self._settings.github_repo}/issues/{issue_number}/labels"
        with httpx.Client(timeout=30.0) as client:
            resp = client.put(url, headers=self._headers(), json={"labels": sorted(labels)})
            resp.raise_for_status()
        logger.info("GitHub labels issue #%s: %s", issue_number, sorted(labels))

    def add_comment(self, issue_number: int, body: str) -> None:
        if not self.configured:
            return
        url = f"{self._base}/repos/{self._settings.github_repo}/issues/{issue_number}/comments"
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(url, headers=self._headers(), json={"body": body})
            resp.raise_for_status()

    def sync_worker_running(self, issue_number: int) -> None:
        self.set_issue_labels(
            issue_number,
            {LABEL_CURSOR_TASK, LABEL_WORKER_RUNNING},
        )

    def sync_ready_for_review(self, issue_number: int) -> None:
        self.set_issue_labels(
            issue_number,
            {LABEL_CURSOR_TASK, LABEL_READY_FOR_REVIEW},
        )

    def sync_needs_fix(self, issue_number: int) -> None:
        self.set_issue_labels(
            issue_number,
            {LABEL_CURSOR_TASK, LABEL_NEEDS_FIX},
        )

    def sync_product_decision(self, issue_number: int) -> None:
        self.set_issue_labels(
            issue_number,
            {LABEL_CURSOR_TASK, LABEL_PRODUCT_DECISION},
        )
