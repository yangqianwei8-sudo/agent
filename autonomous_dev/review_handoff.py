"""Shared ready-for-review transition after worker push."""

from __future__ import annotations

from autonomous_dev.github_client import GitHubClient
from autonomous_dev.review_bridge import ReviewBridge
from autonomous_dev.state import StateStore, TaskRecord, TaskStatus


def transition_ready_for_review(
    store: StateStore,
    github: GitHubClient,
    review_bridge: ReviewBridge,
    task: TaskRecord,
    commit_sha: str,
) -> TaskRecord:
    updated = store.update_task(
        task.id,
        status=TaskStatus.READY_FOR_REVIEW,
        commit_sha=commit_sha,
    )
    github.sync_ready_for_review(updated.issue_number)
    review_bridge.notify_ready_for_review(updated, commit_sha=commit_sha)
    return updated
