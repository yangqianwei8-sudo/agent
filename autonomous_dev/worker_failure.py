"""Worker startup / early-execution failure observability and self-heal coordination."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from autonomous_dev.config import AutonomousDevSettings
from autonomous_dev.github_client import GitHubClient, GitHubClientError
from autonomous_dev.state import (
    ExecutionFailureRecord,
    StateStore,
    TaskRecord,
    TaskStatus,
    WorkerReactivationStatus,
)
from autonomous_dev.status_deriver import summarize_error
from autonomous_dev.worker import PRODUCT_DECISION_MARKER

logger = logging.getLogger(__name__)

FAILURE_COMMENT_MARKER = "## Worker Technical Failure"
_recovery_guard: set[int] = set()


def classify_worker_failure(exc: BaseException) -> tuple[str, str, str]:
    """Return (stage, error_class, sanitized_message)."""
    error_class = type(exc).__name__
    raw = str(exc)
    message = summarize_error(raw) or error_class
    lower = raw.lower()

    if "lease acquire" in lower:
        stage = "worker-startup"
    elif "cursor_api_key" in lower or "cursor_model" in lower:
        stage = "cursor-sdk-config"
    elif "self-heal acceptance" in lower:
        stage = "worker-startup"
    elif "ruff failed" in lower or "pytest" in lower:
        stage = "tests"
    elif "push verification" in lower or "github auth" in lower:
        stage = "commit-push"
    elif "cursor agent" in lower or "cursor_sdk" in lower:
        stage = "cursor-runtime"
    elif "working tree" in lower or "git" in lower:
        stage = "git-preflight"
    else:
        stage = "worker-startup"

    return stage, error_class, message


def structured_error_text(stage: str, error_class: str, message: str) -> str:
    sanitized = summarize_error(message) or error_class
    return f"[{stage}] {error_class}: {sanitized}"


def compute_next_action(
    *,
    retry_count: int,
    max_attempts: int,
    exhausted: bool,
) -> str:
    if exhausted:
        return (
            "Self-heal retry budget exhausted — inspect dashboard/logs and fix root cause manually."
        )
    remaining = max(0, max_attempts - retry_count)
    return (
        f"Automatic self-heal retry scheduled ({retry_count}/{max_attempts}, "
        f"{remaining} remaining) with fresh execution identity."
    )


def format_failure_comment(
    *,
    task_id: int,
    execution_key: str | None,
    stage: str,
    error_class: str,
    message: str,
    retry_count: int,
    next_action: str,
) -> str:
    key = execution_key or "unknown"
    return (
        f"{FAILURE_COMMENT_MARKER}\n\n"
        f"- task_id: `{task_id}`\n"
        f"- execution_key: `{key}`\n"
        f"- stage: `{stage}`\n"
        f"- error_class: `{error_class}`\n"
        f"- message: {message}\n"
        f"- retry_count: {retry_count}\n"
        f"- next_action: {next_action}\n"
    )


def report_worker_failure(
    settings: AutonomousDevSettings,
    store: StateStore,
    github: GitHubClient,
    task: TaskRecord,
    *,
    exc: BaseException,
    stage: str | None = None,
) -> ExecutionFailureRecord | None:
    """Persist structured failure and post exactly one GitHub comment per execution generation."""
    if not task.execution_key:
        logger.warning("worker failure without execution_key task=%s", task.id)
        return None

    if stage is None:
        stage, error_class, message = classify_worker_failure(exc)
    else:
        error_class = type(exc).__name__
        message = summarize_error(str(exc)) or error_class

    react = store.get_worker_reactivation(task.issue_number)
    retry_count = react.attempt_count if react else 0
    exhausted = react is not None and react.status == WorkerReactivationStatus.EXHAUSTED
    next_action = compute_next_action(
        retry_count=retry_count,
        max_attempts=settings.worker_retry_max_attempts,
        exhausted=exhausted,
    )

    failure = store.upsert_execution_failure(
        execution_key=task.execution_key,
        task_id=task.id,
        issue_number=task.issue_number,
        stage=stage,
        error_class=error_class,
        error_message=message,
        retry_count=retry_count,
        next_action=next_action,
    )

    existing = store.get_execution_failure(task.execution_key)
    if existing is not None and existing.comment_posted_at is not None:
        return failure

    if failure.comment_posted_at is None:
        body = format_failure_comment(
            task_id=task.id,
            execution_key=task.execution_key,
            stage=stage,
            error_class=error_class,
            message=message,
            retry_count=retry_count,
            next_action=next_action,
        )
        try:
            github.add_comment(task.issue_number, body)
            store.mark_execution_failure_comment_posted(task.execution_key)
        except GitHubClientError as comment_exc:
            logger.error(
                "worker failure comment failed issue=#%s task=%s: %s",
                task.issue_number,
                task.id,
                comment_exc,
            )

    return failure


def handle_technical_worker_failure(
    settings: AutonomousDevSettings,
    store: StateStore,
    github: GitHubClient,
    task: TaskRecord,
    *,
    exc: BaseException,
    issue_body: str = "",
    stage: str | None = None,
) -> None:
    """Persist structured failure, emit deduplicated comment."""
    if PRODUCT_DECISION_MARKER in issue_body:
        return

    stage_name, error_class, message = (
        classify_worker_failure(exc) if stage is None else (stage, type(exc).__name__, summarize_error(str(exc)) or type(exc).__name__)
    )

    report_worker_failure(
        settings,
        store,
        github,
        store.get_task(task.id) or task,
        exc=exc,
        stage=stage_name,
    )


def trigger_post_failure_recovery(
    settings: AutonomousDevSettings,
    store: StateStore,
    github: GitHubClient,
    issue_number: int,
) -> None:
    """Invoke unified recovery after failure."""
    if issue_number in _recovery_guard:
        return
    _recovery_guard.add(issue_number)
    try:
        from autonomous_dev.recovery_simple import (
            reconcile_needs_fix_and_stalled_reviews,
        )

        reconcile_needs_fix_and_stalled_reviews(settings, store, github)
    except Exception:
        logger.exception("post-failure recovery failed issue=#%s", issue_number)
    finally:
        _recovery_guard.discard(issue_number)


def defer_same_generation_to_self_heal(
    settings: AutonomousDevSettings,
    store: StateStore,
    github: GitHubClient,
    issue_number: int,
    *,
    prior_task: TaskRecord,
    reason: str,
) -> dict[str, str]:
    """Block duplicate same-generation worker spawn; schedule self-heal with fresh identity."""
    trigger_post_failure_recovery(settings, store, github, issue_number)
    logger.info(
        "deferred same-generation retry issue=#%s task=%s reason=%s",
        issue_number,
        prior_task.id,
        reason[:120],
    )
    return {
        "status": "deferred",
        "reason": "same-generation failure; self-heal scheduled",
        "prior_task_id": str(prior_task.id),
    }


def derive_failure_dashboard_state(
    store: StateStore,
    *,
    task: TaskRecord | None,
) -> dict[str, str | int | None] | None:
    issue_number = task.issue_number if task else None
    failure = (
        store.get_latest_execution_failure_for_issue(issue_number)
        if issue_number is not None
        else None
    )
    if failure is None and task is not None and task.execution_key:
        failure = store.get_execution_failure(task.execution_key)
    if failure is None and (task is None or not task.error):
        return None
    if failure is not None:
        return {
            "execution_key": failure.execution_key,
            "task_id": failure.task_id,
            "stage": failure.stage,
            "error_class": failure.error_class,
            "message": failure.error_message,
            "retry_count": failure.retry_count,
            "next_action": failure.next_action,
            "comment_posted": failure.comment_posted_at is not None,
        }
    stage, error_class, message = classify_worker_failure(RuntimeError(task.error or "unknown"))
    return {
        "execution_key": task.execution_key,
        "task_id": task.id,
        "stage": stage,
        "error_class": error_class,
        "message": message,
        "retry_count": None,
        "next_action": None,
        "comment_posted": None,
    }
