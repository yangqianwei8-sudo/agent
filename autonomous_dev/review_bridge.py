"""Review trigger adapters — transport separate from review policy."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

from autonomous_dev.config import AutonomousDevSettings
from autonomous_dev.github_client import GitHubClient, GitHubClientError
from autonomous_dev.state import StateStore, TaskRecord

logger = logging.getLogger(__name__)


@dataclass
class ReviewTriggerResult:
    triggered: bool
    adapter: str
    detail: str


class ReviewTriggerAdapter(ABC):
    @abstractmethod
    def trigger(self, task: TaskRecord, *, commit_sha: str) -> ReviewTriggerResult:
        raise NotImplementedError


class GitHubIssueReviewAdapter(ReviewTriggerAdapter):
    """Primary adapter — GitHub Issue comment handoff (not ChatGPT web session)."""

    def __init__(self, github: GitHubClient, store: StateStore) -> None:
        self._github = github
        self._store = store

    def trigger(self, task: TaskRecord, *, commit_sha: str) -> ReviewTriggerResult:
        _, count = self._store.get_review_trigger(task.id)
        body = (
            f"## Ready for Review\n\n"
            f"- task_id: `{task.id}`\n"
            f"- commit: `{commit_sha}`\n"
            f"- status: awaiting independent reviewer (not instant ChatGPT web session)\n"
        )
        try:
            self._github.add_comment(task.issue_number, body)
            self._store.record_review_trigger(task.id)
            detail = f"GitHub review handoff posted issue=#{task.issue_number} commit={commit_sha}"
            logger.info(detail)
            return ReviewTriggerResult(triggered=True, adapter="github_issue", detail=detail)
        except GitHubClientError as exc:
            detail = f"GitHub review handoff failed issue=#{task.issue_number}: {exc}"
            logger.error(detail)
            return ReviewTriggerResult(triggered=False, adapter="github_issue", detail=detail)


class OpenAIApiReviewAdapter(ReviewTriggerAdapter):
    """Optional adapter for OpenAI API reviewer service — not wired in P0."""

    def trigger(self, task: TaskRecord, *, commit_sha: str) -> ReviewTriggerResult:
        detail = (
            f"openai-api-reviewer not configured issue=#{task.issue_number} commit={commit_sha}"
        )
        logger.info(detail)
        return ReviewTriggerResult(triggered=False, adapter="openai_api", detail=detail)


class WatchdogFallbackAdapter(ReviewTriggerAdapter):
    """Records that watchdog may pick up overdue ready-for-review tasks."""

    def trigger(self, task: TaskRecord, *, commit_sha: str) -> ReviewTriggerResult:
        detail = (
            f"watchdog-fallback eligible issue=#{task.issue_number} commit={commit_sha} "
            "(hourly scan; not instant ChatGPT review)"
        )
        logger.warning(detail)
        return ReviewTriggerResult(triggered=False, adapter="watchdog", detail=detail)


class ReviewBridge:
    def __init__(
        self,
        settings: AutonomousDevSettings | None = None,
        store: StateStore | None = None,
        adapters: list[ReviewTriggerAdapter] | None = None,
    ) -> None:
        if adapters is not None:
            self.adapters = adapters
            return
        settings = settings or AutonomousDevSettings()
        store = store or StateStore(settings.state_db_path)
        github = GitHubClient(settings)
        self.adapters = [
            GitHubIssueReviewAdapter(github, store),
            OpenAIApiReviewAdapter(),
            WatchdogFallbackAdapter(),
        ]

    def notify_ready_for_review(self, task: TaskRecord, *, commit_sha: str) -> list[ReviewTriggerResult]:
        results: list[ReviewTriggerResult] = []
        for adapter in self.adapters:
            results.append(adapter.trigger(task, commit_sha=commit_sha))
        return results
