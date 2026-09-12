"""Pure functions for autonomous dev dashboard status derivation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from autonomous_dev.state import (
    ReviewerReactivationRecord,
    ReviewerReactivationStatus,
    ReviewInvocationRecord,
    ReviewInvocationStatus,
    TaskRecord,
    TaskStatus,
    WorkerReactivationRecord,
    WorkerReactivationStatus,
)

_SECRET_PATTERNS = (
    re.compile(r"ghp_[A-Za-z0-9_]+"),
    re.compile(r"gho_[A-Za-z0-9_]+"),
    re.compile(r"ghu_[A-Za-z0-9_]+"),
    re.compile(r"ghs_[A-Za-z0-9_]+"),
    re.compile(r"ghr_[A-Za-z0-9_]+"),
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._-]+", re.IGNORECASE),
    re.compile(r"x-hub-signature-256:\s*\S+", re.IGNORECASE),
)


class SystemStatus(StrEnum):
    RUNNING = "RUNNING"
    REVIEWING = "REVIEWING"
    WAITING_USER = "WAITING_USER"
    WAITING_PRODUCT_DIRECTION = "WAITING_PRODUCT_DIRECTION"
    HANDOFF_PENDING = "HANDOFF_PENDING"
    HANDOFF_STALLED = "HANDOFF_STALLED"
    SELF_HEAL_PENDING = "SELF_HEAL_PENDING"
    SELF_HEAL_RUNNING = "SELF_HEAL_RUNNING"
    SELF_HEAL_EXHAUSTED = "SELF_HEAL_EXHAUSTED"
    REVIEWER_RETRY_PENDING = "REVIEWER_RETRY_PENDING"
    REVIEWER_RUNNING = "REVIEWER_RUNNING"
    REVIEWER_RETRYING = "REVIEWER_RETRYING"
    REVIEWER_STALE_RECOVERED = "REVIEWER_STALE_RECOVERED"
    REVIEWER_EXHAUSTED = "REVIEWER_EXHAUSTED"
    FAILED = "FAILED"
    STALE = "STALE"
    IDLE = "IDLE"


@dataclass
class WorkerLeaseSnapshot:
    locked: bool
    owner: str | None
    task_id: int | None
    issue_number: int | None
    acquired_at: str | None
    heartbeat_at: str | None
    lease_expires_at: str | None


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def is_lease_valid(lease: WorkerLeaseSnapshot, *, now: datetime | None = None) -> bool:
    if not lease.locked:
        return False
    now = now or datetime.now(UTC)
    expires = _parse_ts(lease.lease_expires_at)
    if expires is not None and expires <= now:
        return False
    return True


def is_heartbeat_fresh(
    lease: WorkerLeaseSnapshot,
    *,
    lease_ttl_seconds: int,
    now: datetime | None = None,
) -> bool:
    if not lease.heartbeat_at:
        return False
    heartbeat = _parse_ts(lease.heartbeat_at)
    if heartbeat is None:
        return False
    now = now or datetime.now(UTC)
    age = (now - heartbeat).total_seconds()
    return age <= lease_ttl_seconds


def is_worker_runtime_stale(
    primary_task: TaskRecord | None,
    lease: WorkerLeaseSnapshot,
    *,
    lease_ttl_seconds: int,
    now: datetime | None = None,
) -> bool:
    if primary_task is None:
        return False
    if primary_task.status not in {TaskStatus.RUNNING, TaskStatus.QUEUED}:
        return False
    now = now or datetime.now(UTC)
    if not is_lease_valid(lease, now=now):
        return True
    if not is_heartbeat_fresh(lease, lease_ttl_seconds=lease_ttl_seconds, now=now):
        return True
    if lease.task_id != primary_task.id:
        return True
    return False


def derive_system_status(
    *,
    primary_task: TaskRecord | None,
    lease: WorkerLeaseSnapshot,
    reviewer_locked: bool,
    active_review: ReviewInvocationRecord | None,
    lease_ttl_seconds: int,
    now: datetime | None = None,
    recent_failed_task: TaskRecord | None = None,
    handoff_state: str | None = None,
    worker_reactivation: WorkerReactivationRecord | None = None,
    reviewer_reactivation: ReviewerReactivationRecord | None = None,
) -> SystemStatus:
    now = now or datetime.now(UTC)

    if handoff_state == "WAITING_PRODUCT_DIRECTION":
        return SystemStatus.WAITING_PRODUCT_DIRECTION
    if handoff_state == "HANDOFF_STALLED":
        return SystemStatus.HANDOFF_STALLED
    if handoff_state == "HANDOFF_PENDING":
        return SystemStatus.HANDOFF_PENDING

    if primary_task is None:
        if recent_failed_task is not None and recent_failed_task.status == TaskStatus.FAILED:
            return SystemStatus.FAILED
        return SystemStatus.IDLE

    task_status = primary_task.status

    if task_status == TaskStatus.PRODUCT_DECISION:
        return SystemStatus.WAITING_USER

    if task_status == TaskStatus.FAILED:
        return SystemStatus.FAILED

    if task_status == TaskStatus.NEEDS_FIX:
        if worker_reactivation is not None:
            if worker_reactivation.status == WorkerReactivationStatus.EXHAUSTED:
                return SystemStatus.SELF_HEAL_EXHAUSTED
            if worker_reactivation.status == WorkerReactivationStatus.SCHEDULED:
                return SystemStatus.SELF_HEAL_RUNNING
            if worker_reactivation.next_retry_at:
                retry_at = _parse_ts(worker_reactivation.next_retry_at)
                if retry_at is not None and retry_at > now:
                    return SystemStatus.SELF_HEAL_PENDING
            return SystemStatus.SELF_HEAL_PENDING
        return SystemStatus.SELF_HEAL_PENDING

    if task_status == TaskStatus.READY_FOR_REVIEW or reviewer_locked or (
        active_review is not None
        and active_review.status
        in {ReviewInvocationStatus.RUNNING, ReviewInvocationStatus.PENDING}
    ):
        if reviewer_reactivation is not None:
            if reviewer_reactivation.status == ReviewerReactivationStatus.EXHAUSTED:
                return SystemStatus.REVIEWER_EXHAUSTED
            if reviewer_reactivation.status == ReviewerReactivationStatus.STALE_RECOVERED:
                return SystemStatus.REVIEWER_STALE_RECOVERED
            if reviewer_reactivation.status == ReviewerReactivationStatus.RETRYING:
                return SystemStatus.REVIEWER_RETRYING
            if reviewer_reactivation.status == ReviewerReactivationStatus.RUNNING:
                return SystemStatus.REVIEWER_RUNNING
            if reviewer_reactivation.next_retry_at:
                retry_at = _parse_ts(reviewer_reactivation.next_retry_at)
                if retry_at is not None and retry_at > now:
                    return SystemStatus.REVIEWER_RETRY_PENDING
            return SystemStatus.REVIEWER_RETRY_PENDING
        if active_review and active_review.status == ReviewInvocationStatus.FAILED:
            if active_review.next_retry_at:
                retry_at = _parse_ts(active_review.next_retry_at)
                if retry_at is not None and retry_at > now:
                    return SystemStatus.REVIEWER_RETRY_PENDING
            return SystemStatus.REVIEWER_RETRYING
        return SystemStatus.REVIEWING

    if task_status in {TaskStatus.RUNNING, TaskStatus.QUEUED}:
        if is_worker_runtime_stale(
            primary_task,
            lease,
            lease_ttl_seconds=lease_ttl_seconds,
            now=now,
        ):
            return SystemStatus.STALE
        if (
            task_status == TaskStatus.RUNNING
            and is_lease_valid(lease, now=now)
            and is_heartbeat_fresh(lease, lease_ttl_seconds=lease_ttl_seconds, now=now)
            and lease.task_id == primary_task.id
        ):
            return SystemStatus.RUNNING
        if task_status == TaskStatus.RUNNING:
            return SystemStatus.STALE
        return SystemStatus.IDLE

    return SystemStatus.IDLE


def parse_generation(execution_key: str | None) -> str | None:
    if not execution_key:
        return None
    if "#" not in execution_key:
        return execution_key
    return execution_key.rsplit("#", 1)[-1]


def derive_current_phase(
    task: TaskRecord | None,
    *,
    system_status: SystemStatus,
    lease: WorkerLeaseSnapshot,
) -> str:
    if task is None:
        return "空闲"
    if system_status == SystemStatus.WAITING_USER:
        return "等待产品/业务决策"
    if system_status == SystemStatus.REVIEWING:
        return "Reviewer 审查中"
    if system_status == SystemStatus.REVIEWER_RETRY_PENDING:
        return "Reviewer 停滞 — 等待自动重试"
    if system_status == SystemStatus.REVIEWER_RUNNING:
        return "Reviewer 自动恢复执行中"
    if system_status == SystemStatus.REVIEWER_RETRYING:
        return "Reviewer 技术失败 — 自动重试中"
    if system_status == SystemStatus.REVIEWER_STALE_RECOVERED:
        return "Reviewer 停滞已自动回收"
    if system_status == SystemStatus.REVIEWER_EXHAUSTED:
        return "Reviewer 自动重试已耗尽"
    if system_status == SystemStatus.STALE:
        return "Worker 运行时过期"
    if system_status == SystemStatus.FAILED:
        return "任务失败"
    if system_status == SystemStatus.SELF_HEAL_PENDING:
        return "技术失败 — 等待自动重试"
    if system_status == SystemStatus.SELF_HEAL_RUNNING:
        return "技术失败 — 自动重试执行中"
    if system_status == SystemStatus.SELF_HEAL_EXHAUSTED:
        return "自动重试已耗尽"
    if system_status == SystemStatus.RUNNING:
        return "Worker / Cursor 执行中"
    mapping = {
        TaskStatus.QUEUED: "排队等待 Worker",
        TaskStatus.RUNNING: "Worker 启动中",
        TaskStatus.READY_FOR_REVIEW: "等待 Reviewer",
        TaskStatus.NEEDS_FIX: "需要修复",
        TaskStatus.PRODUCT_DECISION: "等待产品决策",
        TaskStatus.COMPLETED: "已完成",
        TaskStatus.FAILED: "失败",
    }
    return mapping.get(task.status, "未知")


def derive_pipeline_stages(
    task: TaskRecord | None,
    *,
    system_status: SystemStatus,
    lease: WorkerLeaseSnapshot,
    active_review: ReviewInvocationRecord | None,
) -> list[dict[str, str]]:
    stage_names = [
        "github_event",
        "webhook_router",
        "worker",
        "cursor_runtime",
        "tests",
        "commit_push",
        "reviewer",
        "verdict",
    ]
    labels = [
        "GitHub Event",
        "Webhook/Router",
        "Worker",
        "Cursor AgentRuntime",
        "Tests",
        "Commit/Push",
        "Reviewer",
        "Verdict",
    ]

    def stage(name: str, label: str, state: str) -> dict[str, str]:
        return {"key": name, "label": label, "state": state}

    if task is None:
        return [stage(n, lbl, "unknown") for n, lbl in zip(stage_names, labels, strict=True)]

    status = task.status
    stages: list[dict[str, str]] = []

    stages.append(stage("github_event", labels[0], "pass"))
    stages.append(stage("webhook_router", labels[1], "pass"))

    worker_state = "unknown"
    cursor_state = "unknown"
    tests_state = "unknown"
    commit_state = "unknown"
    reviewer_state = "unknown"
    verdict_state = "unknown"

    if status == TaskStatus.QUEUED:
        worker_state = "running"
    elif status == TaskStatus.RUNNING:
        if system_status == SystemStatus.STALE:
            worker_state = "stale"
            cursor_state = "stale"
        elif system_status == SystemStatus.RUNNING:
            worker_state = "pass"
            cursor_state = "running"
            tests_state = "running"
        else:
            worker_state = "running"
    elif status == TaskStatus.READY_FOR_REVIEW:
        worker_state = "pass"
        cursor_state = "pass"
        tests_state = "pass"
        commit_state = "pass"
        if system_status in {
            SystemStatus.REVIEWER_STALE_RECOVERED,
        }:
            reviewer_state = "stale-recovered"
        elif system_status in {
            SystemStatus.REVIEWER_RETRYING,
            SystemStatus.REVIEWER_RETRY_PENDING,
        }:
            reviewer_state = "retrying"
        elif system_status == SystemStatus.REVIEWER_EXHAUSTED:
            reviewer_state = "exhausted"
        elif active_review and active_review.status.value == "running":
            reviewer_state = "running"
        elif system_status in {
            SystemStatus.REVIEWING,
            SystemStatus.REVIEWER_RUNNING,
        }:
            reviewer_state = "running"
        else:
            reviewer_state = "pending"
    elif status == TaskStatus.COMPLETED:
        worker_state = cursor_state = tests_state = commit_state = reviewer_state = "pass"
        verdict_state = "pass"
    elif status == TaskStatus.NEEDS_FIX:
        worker_state = cursor_state = tests_state = commit_state = "pass"
        reviewer_state = "pass"
        verdict_state = "fail"
    elif status == TaskStatus.PRODUCT_DECISION:
        worker_state = cursor_state = tests_state = commit_state = reviewer_state = "pass"
        verdict_state = "pending"
    elif status == TaskStatus.FAILED:
        if system_status == SystemStatus.STALE:
            worker_state = "stale"
        else:
            worker_state = "fail"

    stages.extend(
        [
            stage("worker", labels[2], worker_state),
            stage("cursor_runtime", labels[3], cursor_state),
            stage("tests", labels[4], tests_state),
            stage("commit_push", labels[5], commit_state),
            stage("reviewer", labels[6], reviewer_state),
            stage("verdict", labels[7], verdict_state),
        ]
    )
    return stages


def redact_secrets(text: str | None) -> str | None:
    if text is None:
        return None
    out = text
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub("[REDACTED]", out)
    parts: list[str] = []
    for token in out.split():
        if token.startswith("ghp_") or (len(token) > 24 and token.isalnum()):
            parts.append("[REDACTED]")
        else:
            parts.append(token)
    return " ".join(parts)


def summarize_error(error: str | None, *, max_len: int = 240) -> str | None:
    if not error:
        return None
    cleaned = redact_secrets(error.strip()) or ""
    cleaned = cleaned.replace("\n", " ").replace("\r", " ")
    if len(cleaned) > max_len:
        return cleaned[: max_len - 3] + "..."
    return cleaned or None


def sanitize_for_json(value: Any) -> Any:
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, dict):
        return {str(k): sanitize_for_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_for_json(v) for v in value]
    if isinstance(value, tuple):
        return [sanitize_for_json(v) for v in value]
    return value
