"""Review trigger adapters — event-driven OpenAI reviewer primary path."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

from autonomous_dev.config import AutonomousDevSettings
from autonomous_dev.github_client import GitHubClient, GitHubClientError
from autonomous_dev.review_executor import ReviewExecutor
from autonomous_dev.reviewer_service import ReviewerCredentialError
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


class OpenAIApiReviewAdapter(ReviewTriggerAdapter):
    """Primary adapter — schedules independent OpenAI API reviewer (not ChatGPT web session)."""

    def __init__(
        self,
        settings: AutonomousDevSettings,
        store: StateStore,
        *,
        executor: ReviewExecutor | None = None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._executor = executor or ReviewExecutor(settings, store)

    def trigger(self, task: TaskRecord, *, commit_sha: str) -> ReviewTriggerResult:
        creds = self._settings.resolve_reviewer_credentials()
        if creds is None and self._settings.autonomous_worker_mode != "deterministic":
            detail = (
                f"OPENAI_API_KEY credential blocker issue=#{task.issue_number} "
                f"commit={commit_sha} — will not substitute Cursor as reviewer"
            )
            logger.error(detail)
            return ReviewTriggerResult(triggered=False, adapter="openai_api", detail=detail)

        try:
            outcome = self._executor.schedule_review(task, commit_sha=commit_sha)
            detail = (
                f"openai-api-reviewer scheduled issue=#{task.issue_number} "
                f"commit={commit_sha} status={outcome.get('status')}"
            )
            logger.info(detail)
            triggered = outcome.get("status") in {
                "scheduled",
                "idempotent",
                "already_scheduled",
                "retry_scheduled",
            }
            return ReviewTriggerResult(triggered=triggered, adapter="openai_api", detail=detail)
        except ReviewerCredentialError as exc:
            detail = f"reviewer credential blocker: {exc}"
            logger.error(detail)
            return ReviewTriggerResult(triggered=False, adapter="openai_api", detail=detail)


class GitHubIssueReviewAdapter(ReviewTriggerAdapter):
    """Secondary adapter — posts review handoff comment (notification only)."""

    def __init__(self, github: GitHubClient, store: StateStore) -> None:
        self._github = github
        self._store = store

    def trigger(self, task: TaskRecord, *, commit_sha: str) -> ReviewTriggerResult:
        _, count = self._store.get_review_trigger(task.id)
        body = (
            f"## Ready for Review\n\n"
            f"- task_id: `{task.id}`\n"
            f"- commit: `{commit_sha}`\n"
            f"- status: event-driven OpenAI reviewer scheduled (not ChatGPT web session)\n"
            f"- review_trigger_count: {count + 1}\n"
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


class WatchdogFallbackAdapter(ReviewTriggerAdapter):
    """Records watchdog fallback eligibility — not primary review path."""

    def trigger(self, task: TaskRecord, *, commit_sha: str) -> ReviewTriggerResult:
        detail = (
            f"watchdog-fallback eligible issue=#{task.issue_number} commit={commit_sha} "
            "(hourly scan only)"
        )
        logger.debug(detail)
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
            OpenAIApiReviewAdapter(settings, store),
            GitHubIssueReviewAdapter(github, store),
        ]

    def notify_ready_for_review(self, task: TaskRecord, *, commit_sha: str) -> list[ReviewTriggerResult]:
        results: list[ReviewTriggerResult] = []
        for adapter in self.adapters:
            results.append(adapter.trigger(task, commit_sha=commit_sha))
        return results
