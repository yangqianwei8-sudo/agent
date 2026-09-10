"""Review trigger adapters — transport separate from review policy."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

from autonomous_dev.state import TaskRecord

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

    def trigger(self, task: TaskRecord, *, commit_sha: str) -> ReviewTriggerResult:
        detail = (
            f"Review handoff issue=#{task.issue_number} commit={commit_sha} "
            "(awaiting independent reviewer via GitHub)"
        )
        logger.info(detail)
        return ReviewTriggerResult(triggered=True, adapter="github_issue", detail=detail)


class OpenAIApiReviewAdapter(ReviewTriggerAdapter):
    """Optional adapter for OpenAI API reviewer service."""

    def trigger(self, task: TaskRecord, *, commit_sha: str) -> ReviewTriggerResult:
        detail = f"openai-api-reviewer queued issue=#{task.issue_number} commit={commit_sha}"
        logger.info(detail)
        return ReviewTriggerResult(triggered=False, adapter="openai_api", detail=detail)


class WatchdogFallbackAdapter(ReviewTriggerAdapter):
    """Hourly ChatGPT watchdog fallback — records handoff only."""

    def trigger(self, task: TaskRecord, *, commit_sha: str) -> ReviewTriggerResult:
        detail = f"watchdog-fallback queued issue=#{task.issue_number} commit={commit_sha}"
        logger.warning(detail)
        return ReviewTriggerResult(triggered=False, adapter="watchdog", detail=detail)


class ReviewBridge:
    def __init__(self, adapters: list[ReviewTriggerAdapter] | None = None) -> None:
        self.adapters = adapters or [
            GitHubIssueReviewAdapter(),
            OpenAIApiReviewAdapter(),
            WatchdogFallbackAdapter(),
        ]

    def notify_ready_for_review(self, task: TaskRecord, *, commit_sha: str) -> list[ReviewTriggerResult]:
        results: list[ReviewTriggerResult] = []
        for adapter in self.adapters:
            results.append(adapter.trigger(task, commit_sha=commit_sha))
        return results
