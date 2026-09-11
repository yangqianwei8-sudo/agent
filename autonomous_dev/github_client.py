"""GitHub REST API — issue labels and review comments (fail-closed)."""

from __future__ import annotations

import logging
import time

import httpx

from autonomous_dev.config import AutonomousDevSettings
from autonomous_dev.github_auth import resolve_github_token

logger = logging.getLogger(__name__)

LABEL_CURSOR_TASK = "cursor-task"
LABEL_CURRENT_TASK = "current-task"
LABEL_WORKER_RUNNING = "worker-running"
LABEL_READY_FOR_REVIEW = "ready-for-review"
LABEL_NEEDS_FIX = "needs-fix"
LABEL_PRODUCT_DECISION = "product-decision"
LABEL_COMPLETED = "completed"


class GitHubClientError(RuntimeError):
    """Raised when GitHub API sync fails or is unavailable."""


class GitHubClient:
    def __init__(self, settings: AutonomousDevSettings, *, max_retries: int = 3) -> None:
        self._settings = settings
        self._base = "https://api.github.com"
        self._max_retries = max_retries

    @property
    def configured(self) -> bool:
        return bool(resolve_github_token())

    def _require_configured(self) -> None:
        if not self.configured:
            raise GitHubClientError(
                "GitHub token not configured — label sync requires GITHUB_TOKEN or GitHub App"
            )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {resolve_github_token()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _request_with_retry(self, method: str, url: str, **kwargs) -> httpx.Response:
        self._require_configured()
        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                with httpx.Client(timeout=30.0) as client:
                    resp = client.request(method, url, headers=self._headers(), **kwargs)
                if resp.status_code in {429, 502, 503, 504} and attempt < self._max_retries - 1:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                resp.raise_for_status()
                return resp
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt < self._max_retries - 1:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise GitHubClientError(f"GitHub API request failed: {exc}") from exc
        raise GitHubClientError(f"GitHub API request failed after retries: {last_exc}")

    def get_issue_labels(self, issue_number: int) -> set[str]:
        url = f"{self._base}/repos/{self._settings.github_repo}/issues/{issue_number}"
        resp = self._request_with_retry("GET", url)
        labels = resp.json().get("labels") or []
        return {lbl["name"] for lbl in labels if isinstance(lbl, dict) and lbl.get("name")}

    def set_issue_labels(self, issue_number: int, labels: set[str]) -> None:
        expected = set(labels)
        url = f"{self._base}/repos/{self._settings.github_repo}/issues/{issue_number}/labels"
        self._request_with_retry("PUT", url, json={"labels": sorted(expected)})
        actual = self.get_issue_labels(issue_number)
        if not expected.issubset(actual):
            missing = expected - actual
            raise GitHubClientError(
                f"GitHub label sync verification failed issue #{issue_number} missing={sorted(missing)}"
            )
        logger.info("GitHub labels issue #%s: %s", issue_number, sorted(expected))

    def add_comment(self, issue_number: int, body: str) -> None:
        url = f"{self._base}/repos/{self._settings.github_repo}/issues/{issue_number}/comments"
        self._request_with_retry("POST", url, json={"body": body})

    def sync_worker_running(self, issue_number: int) -> None:
        self.set_issue_labels(issue_number, {LABEL_CURSOR_TASK, LABEL_WORKER_RUNNING})

    def sync_ready_for_review(self, issue_number: int) -> None:
        self.set_issue_labels(issue_number, {LABEL_CURSOR_TASK, LABEL_READY_FOR_REVIEW})

    def sync_needs_fix(self, issue_number: int) -> None:
        self.set_issue_labels(issue_number, {LABEL_CURSOR_TASK, LABEL_NEEDS_FIX})

    def sync_product_decision(self, issue_number: int) -> None:
        self.set_issue_labels(issue_number, {LABEL_CURSOR_TASK, LABEL_PRODUCT_DECISION})

    def sync_completed(self, issue_number: int) -> None:
        self.set_issue_labels(issue_number, {LABEL_CURSOR_TASK, LABEL_COMPLETED})
