"""Technical needs-fix self-heal — bounded retry without product intervention."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime, timedelta

from autonomous_dev.config import AutonomousDevSettings
from autonomous_dev.github_client import (
    LABEL_CURRENT_TASK,
    LABEL_CURSOR_TASK,
    LABEL_NEEDS_FIX,
    LABEL_PRODUCT_DECISION,
    GitHubClient,
    GitHubClientError,
)
from autonomous_dev.state import (
    DeliveryStatus,
    StateStore,
    TaskRecord,
    TaskStatus,
    WorkerReactivationStatus,
)
from autonomous_dev.worker import PRODUCT_DECISION_MARKER

logger = logging.getLogger(__name__)

GENERATION_BUMP_RE = re.compile(r"\n\n<!-- generation-bump:.*? -->", re.DOTALL)
SELF_HEAL_ACCEPTANCE_MARKER = "[SELF-HEAL-ACCEPTANCE]"


def is_genuine_product_decision(*, issue_body: str, labels: set[str]) -> bool:
    if LABEL_PRODUCT_DECISION in labels:
        return True
    return PRODUCT_DECISION_MARKER in issue_body


def is_technical_needs_fix(*, issue_body: str, labels: set[str]) -> bool:
    if LABEL_NEEDS_FIX not in labels:
        return False
    return not is_genuine_product_decision(issue_body=issue_body, labels=labels)


def issue_has_active_worker_execution(store: StateStore, issue_number: int) -> bool:
    return store.get_active_task_for_issue(issue_number) is not None


def bump_issue_generation(github: GitHubClient, issue_number: int) -> str:
    """Persist a new generation on GitHub so execution identity is fresh."""
    try:
        body = github.get_issue_body(issue_number)
    except GitHubClientError:
        body = ""
    stamp = datetime.now(UTC).isoformat()
    marker = f"\n\n<!-- generation-bump:{stamp} -->"
    if GENERATION_BUMP_RE.search(body):
        body = GENERATION_BUMP_RE.sub(marker, body)
    else:
        body = body.rstrip() + marker
    github.update_issue_body(issue_number, body)
    return stamp.replace("+00:00", "Z")


def record_technical_failure(
    store: StateStore,
    task: TaskRecord,
    *,
    error: str,
    max_attempts: int,
    backoff_seconds: int,
) -> None:
    existing = store.get_worker_reactivation(task.issue_number)
    attempt = (existing.attempt_count if existing else 0) + 1
    if attempt >= max_attempts:
        store.upsert_worker_reactivation(
            issue_number=task.issue_number,
            task_id=task.id,
            attempt_count=attempt,
            next_retry_at=None,
            last_error=error[:2000],
            status=WorkerReactivationStatus.EXHAUSTED,
        )
        logger.warning(
            "worker self-heal exhausted issue=#%s attempts=%s",
            task.issue_number,
            attempt,
        )
        return
    delay = backoff_seconds * attempt
    next_retry = (datetime.now(UTC) + timedelta(seconds=delay)).isoformat()
    store.upsert_worker_reactivation(
        issue_number=task.issue_number,
        task_id=task.id,
        attempt_count=attempt,
        next_retry_at=next_retry,
        last_error=error[:2000],
        status=WorkerReactivationStatus.PENDING,
    )


def _reactivation_is_due(store: StateStore, issue_number: int, *, now_iso: str) -> bool:
    record = store.get_worker_reactivation(issue_number)
    if record is None:
        return True
    if record.status == WorkerReactivationStatus.EXHAUSTED:
        return False
    if record.next_retry_at is None:
        return True
    return record.next_retry_at <= now_iso


def _cooldown_blocks_kick(record, *, cooldown_seconds: int, now: datetime) -> bool:
    if not record or not record.last_kick_at:
        return False
    try:
        last = datetime.fromisoformat(record.last_kick_at.replace("Z", "+00:00"))
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
    except ValueError:
        return False
    return (now - last).total_seconds() < cooldown_seconds


def kick_worker_reactivation(
    settings: AutonomousDevSettings,
    store: StateStore,
    github: GitHubClient,
    issue_number: int,
    *,
    reason: str,
) -> dict[str, str]:
    """Route a synthetic current-task event — GitHub labels stay needs-fix until worker runs."""
    from autonomous_dev.task_router import TaskRouter

    now = datetime.now(UTC)
    now_iso = now.isoformat()

    if store.is_locked():
        lease = store.get_lease()
        if lease.issue_number != issue_number:
            return {"status": "deferred", "reason": "worker lease held"}

    try:
        body = github.get_issue_body(issue_number)
        labels = github.get_issue_labels(issue_number)
    except GitHubClientError as exc:
        return {"status": "failed", "reason": str(exc)[:200]}

    record = store.get_worker_reactivation(issue_number)
    has_pending_reactivation = record is not None and record.status in {
        WorkerReactivationStatus.PENDING,
        WorkerReactivationStatus.SCHEDULED,
    }
    if not has_pending_reactivation and not is_technical_needs_fix(
        issue_body=body, labels=labels
    ):
        return {"status": "skipped", "reason": "not technical needs-fix"}

    if issue_has_active_worker_execution(store, issue_number):
        return {"status": "skipped", "reason": "active execution exists"}
    if record and record.status == WorkerReactivationStatus.EXHAUSTED:
        return {"status": "skipped", "reason": "retry budget exhausted"}
    if not _reactivation_is_due(store, issue_number, now_iso=now_iso):
        return {"status": "skipped", "reason": "backoff pending"}
    if _cooldown_blocks_kick(record, cooldown_seconds=settings.worker_retry_backoff_seconds, now=now):
        return {"status": "skipped", "reason": "kick cooldown"}

    try:
        generation = bump_issue_generation(github, issue_number)
        body = github.get_issue_body(issue_number)
    except GitHubClientError as exc:
        return {"status": "failed", "reason": f"generation bump failed: {exc}"[:200]}

    attempt = (record.attempt_count if record else 0) + 1
    delivery_id = f"self-heal-{issue_number}-{attempt}-{uuid.uuid4().hex[:8]}"
    if store.delivery_exists(delivery_id):
        return {"status": "skipped", "reason": "duplicate delivery"}

    payload = {
        "action": "labeled",
        "issue": {
            "number": issue_number,
            "state": "open",
            "title": "",
            "body": body,
            "labels": [
                {"name": LABEL_CURSOR_TASK},
                {"name": LABEL_CURRENT_TASK},
            ],
            "updated_at": generation,
            "created_at": generation,
        },
    }
    store.record_delivery(
        delivery_id=delivery_id,
        event_type="issues",
        action="labeled",
        payload=payload,
        status=DeliveryStatus.RECEIVED,
    )
    store.upsert_worker_reactivation(
        issue_number=issue_number,
        task_id=record.task_id if record else None,
        attempt_count=attempt,
        next_retry_at=None,
        last_error=record.last_error if record else reason[:2000],
        status=WorkerReactivationStatus.SCHEDULED,
        last_kick_at=now_iso,
    )

    router = TaskRouter(settings, store)
    result = router.handle(
        event_type="issues",
        action="labeled",
        delivery_id=delivery_id,
        payload=payload,
    )
    status = str(result.get("status", "unknown"))
    logger.info(
        "worker self-heal kick issue=#%s attempt=%s reason=%s result=%s",
        issue_number,
        attempt,
        reason[:120],
        status,
    )
    if status != "worker_started":
        delay = settings.worker_retry_backoff_seconds * attempt
        next_retry = (now + timedelta(seconds=delay)).isoformat()
        store.upsert_worker_reactivation(
            issue_number=issue_number,
            task_id=record.task_id if record else None,
            attempt_count=attempt,
            next_retry_at=next_retry,
            last_error=result.get("reason") or reason[:2000],
            status=WorkerReactivationStatus.PENDING,
            last_kick_at=now_iso,
        )
    return {k: str(v) for k, v in result.items()}


def _reactivate_issue_if_needed(
    settings: AutonomousDevSettings,
    store: StateStore,
    github: GitHubClient,
    num: int,
    *,
    body: str,
    labels: set[str],
    now_iso: str,
) -> bool:
    record = store.get_worker_reactivation(num)
    has_pending = record is not None and record.status in {
        WorkerReactivationStatus.PENDING,
        WorkerReactivationStatus.SCHEDULED,
    }
    if not has_pending and not is_technical_needs_fix(issue_body=body, labels=labels):
        return False
    if issue_has_active_worker_execution(store, num):
        return False
    if store.is_locked():
        lease = store.get_lease()
        if lease.issue_number == num and store._lease_is_valid(store.get_lease(), now_iso=now_iso):
            return False

    latest = store.get_task_by_issue(num)
    if latest is not None and latest.status == TaskStatus.PRODUCT_DECISION:
        return False

    if record is None and latest is not None and latest.status in {
        TaskStatus.NEEDS_FIX,
        TaskStatus.FAILED,
    }:
        store.upsert_worker_reactivation(
            issue_number=num,
            task_id=latest.id,
            attempt_count=0,
            next_retry_at=now_iso,
            last_error=latest.error,
            status=WorkerReactivationStatus.PENDING,
        )

    if not _reactivation_is_due(store, num, now_iso=now_iso):
        return False

    result = kick_worker_reactivation(
        settings,
        store,
        github,
        num,
        reason="loop_recovery technical needs-fix",
    )
    if result.get("status") == "worker_started":
        return True
    if result.get("status") not in {"skipped", "deferred"}:
        logger.info("self-heal reconcile issue=#%s result=%s", num, result)
    return False


def reconcile_technical_needs_fix(
    settings: AutonomousDevSettings,
    store: StateStore,
    github: GitHubClient,
) -> int:
    """Detect orphan technical needs-fix repairs and reactivate with fresh execution identity."""
    reactivated = 0
    now_iso = datetime.now(UTC).isoformat()
    seen: set[int] = set()

    for record in store.list_due_worker_reactivations(limit=20):
        num = record.issue_number
        seen.add(num)
        try:
            body = github.get_issue_body(num) if getattr(github, "configured", True) else ""
            labels = (
                github.get_issue_labels(num)
                if getattr(github, "configured", True)
                else {LABEL_NEEDS_FIX, LABEL_CURSOR_TASK}
            )
        except GitHubClientError:
            body = ""
            labels = {LABEL_NEEDS_FIX, LABEL_CURSOR_TASK}
        if _reactivate_issue_if_needed(
            settings, store, github, num, body=body, labels=labels, now_iso=now_iso
        ):
            reactivated += 1

    if not getattr(github, "configured", True):
        return reactivated
    try:
        issues = github.list_open_issues_with_label(LABEL_NEEDS_FIX, limit=30)
    except GitHubClientError:
        return reactivated

    for issue in issues:
        num = int(issue["number"])
        if num in seen:
            continue
        labels = {lbl["name"] for lbl in (issue.get("labels") or []) if isinstance(lbl, dict)}
        body = str(issue.get("body") or "")
        if _reactivate_issue_if_needed(
            settings, store, github, num, body=body, labels=labels, now_iso=now_iso
        ):
            reactivated += 1

    return reactivated


def derive_self_heal_dashboard_state(
    store: StateStore,
    *,
    issue_number: int | None,
    primary_status: TaskStatus | None,
) -> dict[str, str | int | None] | None:
    if issue_number is None:
        return None
    record = store.get_worker_reactivation(issue_number)
    if record is None and primary_status != TaskStatus.NEEDS_FIX:
        return None
    if record is None:
        return {
            "status": WorkerReactivationStatus.PENDING.value,
            "attempt_count": 0,
            "max_attempts": None,
            "next_retry_at": None,
            "last_error": None,
            "reason": "technical needs-fix awaiting first self-heal tick",
        }
    return {
        "status": record.status.value,
        "attempt_count": record.attempt_count,
        "max_attempts": None,
        "next_retry_at": record.next_retry_at,
        "last_kick_at": record.last_kick_at,
        "last_error": record.last_error,
        "reason": record.last_error,
    }
