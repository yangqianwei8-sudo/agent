"""Append-only execution event store and live trace derivation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from autonomous_dev.config import AutonomousDevSettings
from autonomous_dev.state import (
    ExecutionEventRecord,
    ReviewInvocationRecord,
    ReviewInvocationStatus,
    StateStore,
    TaskRecord,
    TaskStatus,
)
from autonomous_dev.status_deriver import (
    WorkerLeaseSnapshot,
    is_heartbeat_fresh,
    is_worker_runtime_stale,
    redact_secrets,
    sanitize_for_json,
)

BLOCKED_PATH_PREFIXES = (
    ".env",
    "backend/cases/",
    "data/cases/",
    "uploads/",
)

CURSOR_PROGRESS_EVENTS = frozenset({"CURSOR_STARTED", "CURSOR_FINISHED"})


class ExecutionEventType(StrEnum):
    TASK_STARTED = "TASK_STARTED"
    ANALYSIS_STARTED = "ANALYSIS_STARTED"
    FILE_READ = "FILE_READ"
    FILE_EDIT = "FILE_EDIT"
    COMMAND_STARTED = "COMMAND_STARTED"
    COMMAND_FINISHED = "COMMAND_FINISHED"
    CURSOR_STARTED = "CURSOR_STARTED"
    CURSOR_FINISHED = "CURSOR_FINISHED"
    TEST_STARTED = "TEST_STARTED"
    TEST_FINISHED = "TEST_FINISHED"
    RUFF_STARTED = "RUFF_STARTED"
    RUFF_FINISHED = "RUFF_FINISHED"
    GIT_DIFF = "GIT_DIFF"
    COMMIT_STARTED = "COMMIT_STARTED"
    COMMIT_CREATED = "COMMIT_CREATED"
    PUSH_STARTED = "PUSH_STARTED"
    PUSH_FINISHED = "PUSH_FINISHED"
    REVIEW_STARTED = "REVIEW_STARTED"
    REVIEW_FINISHED = "REVIEW_FINISHED"
    TASK_FINISHED = "TASK_FINISHED"
    TASK_FAILED = "TASK_FAILED"


class MotionStatus(StrEnum):
    MOVING = "MOVING"
    STALLED = "STALLED"
    STALE = "STALE"
    IDLE = "IDLE"
    REVIEWING = "REVIEWING"


_EVENT_ACTION_ZH: dict[str, str] = {
    "TASK_STARTED": "Worker 已启动任务",
    "ANALYSIS_STARTED": "正在分析任务",
    "FILE_READ": "正在读取 {file}",
    "FILE_EDIT": "正在修改 {file}",
    "COMMAND_STARTED": "正在执行 {cmd}",
    "COMMAND_FINISHED": "命令完成",
    "CURSOR_STARTED": "Cursor Agent 执行中",
    "CURSOR_FINISHED": "Cursor Agent 已完成",
    "TEST_STARTED": "正在运行 {cmd}",
    "TEST_FINISHED": "测试完成",
    "RUFF_STARTED": "正在运行 ruff",
    "RUFF_FINISHED": "ruff 完成",
    "GIT_DIFF": "生成 git diff",
    "COMMIT_STARTED": "正在创建 commit",
    "COMMIT_CREATED": "commit 已创建",
    "PUSH_STARTED": "正在 push",
    "PUSH_FINISHED": "push 已完成",
    "REVIEW_STARTED": "Reviewer 正在审查",
    "REVIEW_FINISHED": "Reviewer 审查完成",
    "TASK_FINISHED": "任务完成",
    "TASK_FAILED": "任务失败",
}


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def sanitize_repo_path(path: str | Path | None, repo_root: Path) -> str | None:
    if path is None:
        return None
    raw = str(path).strip()
    if not raw:
        return None
    raw_norm = raw.replace("\\", "/")
    if raw_norm.lower() in {".env", "./.env"} or raw_norm.lower().endswith("/.env"):
        return None
    p = Path(raw)
    if p.is_absolute():
        try:
            p = p.relative_to(repo_root.resolve())
        except ValueError:
            return None
    rel = str(p).replace("\\", "/").lstrip("./")
    lowered = rel.lower()
    if lowered == "env" or lowered.endswith("/.env") or ".." in rel.split("/"):
        return None
    for prefix in BLOCKED_PATH_PREFIXES:
        if lowered.startswith(prefix):
            return None
    return rel


def sanitize_command(cmd: list[str] | str | None) -> str | None:
    if cmd is None:
        return None
    if isinstance(cmd, list):
        parts = cmd[:12]
        joined = " ".join(parts)
    else:
        joined = cmd
    lowered = joined.lower().strip()
    if lowered.startswith("export ") or " env " in f" {lowered} ":
        return "[REDACTED]"
    for secret_key in (
        "openai_api_key",
        "cursor_api_key",
        "github_token",
        "github_webhook_secret",
        "ghp_",
        "sk-",
    ):
        if secret_key in lowered:
            return "[REDACTED]"
    return redact_secrets(joined)[:200]


def _parse_generation(execution_key: str | None) -> str | None:
    if not execution_key:
        return None
    if "#" not in execution_key:
        return execution_key
    return execution_key.rsplit("#", 1)[-1]


def _parse_diff_stat(text: str) -> dict[str, int | None]:
    files_changed = insertions = deletions = None
    for line in reversed(text.strip().splitlines()):
        if "file changed" in line or "files changed" in line:
            m = re.search(
                r"(\d+) files? changed(?:, (\d+) insertions?\(\+\))?(?:, (\d+) deletions?\(-\))?",
                line,
            )
            if m:
                files_changed = int(m.group(1))
                insertions = int(m.group(2) or 0)
                deletions = int(m.group(3) or 0)
            break
    return {
        "files_changed": files_changed,
        "insertions": insertions,
        "deletions": deletions,
    }


@dataclass
class ExecutionEventRecorder:
    store: StateStore
    task: TaskRecord
    repo_root: Path
    generation: str | None = None

    def __post_init__(self) -> None:
        if self.generation is None:
            self.generation = _parse_generation(self.task.execution_key)

    def record(
        self,
        event_type: ExecutionEventType | str,
        *,
        phase: str | None = None,
        action: str | None = None,
        file_path: str | Path | None = None,
        command_summary: list[str] | str | None = None,
        result_summary: str | None = None,
        status: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ExecutionEventRecord:
        rel_path = sanitize_repo_path(file_path, self.repo_root)
        cmd = sanitize_command(command_summary)
        result = redact_secrets(result_summary)[:500] if result_summary else None
        meta = sanitize_for_json(metadata or {})
        return self.store.append_execution_event(
            task_id=self.task.id,
            issue_number=self.task.issue_number,
            generation=self.generation,
            event_type=str(event_type),
            phase=phase,
            action=action,
            file_path=rel_path,
            command_summary=cmd,
            result_summary=result,
            status=status,
            metadata=meta,
        )

    def task_started(self) -> None:
        self.record(
            ExecutionEventType.TASK_STARTED,
            phase="worker",
            action="Worker 已启动",
        )

    def analysis_started(self) -> None:
        self.record(
            ExecutionEventType.ANALYSIS_STARTED,
            phase="analysis",
            action="正在分析任务",
        )

    def cursor_started(self) -> None:
        self.record(
            ExecutionEventType.CURSOR_STARTED,
            phase="cursor",
            action="Cursor Agent 长任务执行中",
        )

    def cursor_finished(self, *, result_summary: str | None = None) -> None:
        self.record(
            ExecutionEventType.CURSOR_FINISHED,
            phase="cursor",
            action="Cursor Agent 已完成",
            result_summary=result_summary,
            status="ok",
        )

    def test_started(self, cmd: list[str]) -> None:
        self.record(
            ExecutionEventType.TEST_STARTED,
            phase="testing",
            command_summary=cmd,
            action=f"正在运行 {sanitize_command(cmd)}",
        )

    def test_finished(self, *, output: str, passed: bool) -> None:
        summary = redact_secrets(output)[:300]
        passed_n = failed_n = None
        m = re.search(r"(\d+) passed", summary)
        if m:
            passed_n = int(m.group(1))
        m = re.search(r"(\d+) failed", summary)
        if m:
            failed_n = int(m.group(1))
        self.record(
            ExecutionEventType.TEST_FINISHED,
            phase="testing",
            result_summary=summary,
            status="pass" if passed else "fail",
            metadata={
                "passed": passed_n,
                "failed": failed_n,
                "duration_seconds": None,
            },
        )

    def git_diff(self, *, stat_output: str) -> None:
        stats = _parse_diff_stat(stat_output)
        self.record(
            ExecutionEventType.GIT_DIFF,
            phase="git",
            result_summary=redact_secrets(stat_output.strip().splitlines()[-1])[:200]
            if stat_output.strip()
            else None,
            metadata=stats,
        )

    def commit_started(self, message: str) -> None:
        self.record(
            ExecutionEventType.COMMIT_STARTED,
            phase="git",
            action="正在创建 commit",
            result_summary=redact_secrets(message)[:200],
        )

    def commit_created(self, *, sha: str, message: str) -> None:
        self.record(
            ExecutionEventType.COMMIT_CREATED,
            phase="git",
            action="commit 已创建",
            result_summary=redact_secrets(message)[:200],
            metadata={"commit_sha": sha[:40], "message": redact_secrets(message)[:200]},
        )

    def push_started(self) -> None:
        self.record(
            ExecutionEventType.PUSH_STARTED,
            phase="git",
            command_summary=["git", "push", "origin", "main"],
            action="正在 push origin/main",
        )

    def push_finished(self, *, sha: str) -> None:
        self.record(
            ExecutionEventType.PUSH_FINISHED,
            phase="git",
            action="push 已完成",
            status="success",
            metadata={"commit_sha": sha[:40], "push_status": "success"},
        )

    def task_finished(self, *, status: TaskStatus) -> None:
        self.record(
            ExecutionEventType.TASK_FINISHED,
            phase="worker",
            action=f"任务完成 ({status.value})",
            status=status.value,
        )

    def task_failed(self, *, error: str) -> None:
        self.record(
            ExecutionEventType.TASK_FAILED,
            phase="worker",
            action="任务失败",
            result_summary=error,
            status="fail",
        )

    def command_started(self, cmd: list[str], *, phase: str = "command") -> None:
        self.record(
            ExecutionEventType.COMMAND_STARTED,
            phase=phase,
            command_summary=cmd,
            action=f"正在执行 {sanitize_command(cmd)}",
        )

    def command_finished(
        self,
        cmd: list[str],
        *,
        output: str = "",
        ok: bool = True,
        phase: str = "command",
    ) -> None:
        self.record(
            ExecutionEventType.COMMAND_FINISHED,
            phase=phase,
            command_summary=cmd,
            result_summary=output[:300] if output else None,
            status="ok" if ok else "fail",
        )


def record_review_event(
    store: StateStore,
    *,
    task: TaskRecord,
    event_type: ExecutionEventType,
    result_summary: str | None = None,
    status: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    store.append_execution_event(
        task_id=task.id,
        issue_number=task.issue_number,
        generation=_parse_generation(task.execution_key),
        event_type=event_type.value,
        phase="review",
        action="Reviewer 正在审查" if event_type == ExecutionEventType.REVIEW_STARTED else "Reviewer 审查完成",
        result_summary=redact_secrets(result_summary)[:500] if result_summary else None,
        status=status,
        metadata=sanitize_for_json(metadata or {}),
    )


def _event_action_label(event: ExecutionEventRecord) -> str:
    if event.action:
        return redact_secrets(event.action)
    template = _EVENT_ACTION_ZH.get(event.event_type, event.event_type)
    return template.format(
        file=event.file_path or "—",
        cmd=event.command_summary or "—",
    )


def _cursor_in_long_op(events: list[ExecutionEventRecord]) -> bool:
    for event in reversed(events):
        if event.event_type == ExecutionEventType.CURSOR_FINISHED.value:
            return False
        if event.event_type == ExecutionEventType.CURSOR_STARTED.value:
            return True
    return False


def derive_motion_status(
    *,
    primary_task: TaskRecord | None,
    lease: WorkerLeaseSnapshot,
    reviewer_locked: bool,
    active_review: ReviewInvocationRecord | None,
    events: list[ExecutionEventRecord],
    progress_stale_seconds: int,
    cursor_long_op_grace_seconds: int,
    lease_ttl_seconds: int,
    now: datetime | None = None,
) -> MotionStatus:
    now = now or datetime.now(UTC)
    review_active = reviewer_locked or (
        active_review is not None
        and active_review.status in {ReviewInvocationStatus.RUNNING, ReviewInvocationStatus.PENDING}
    )
    if primary_task and primary_task.status == TaskStatus.READY_FOR_REVIEW and review_active:
        return MotionStatus.REVIEWING
    if review_active and primary_task and primary_task.status in {
        TaskStatus.READY_FOR_REVIEW,
        TaskStatus.RUNNING,
    }:
        return MotionStatus.REVIEWING

    if primary_task is None or primary_task.status not in {
        TaskStatus.RUNNING,
        TaskStatus.QUEUED,
    }:
        if review_active:
            return MotionStatus.REVIEWING
        return MotionStatus.IDLE

    if is_worker_runtime_stale(
        primary_task,
        lease,
        lease_ttl_seconds=lease_ttl_seconds,
        now=now,
    ) or not is_heartbeat_fresh(lease, lease_ttl_seconds=lease_ttl_seconds, now=now):
        return MotionStatus.STALE

    progress_times = [_parse_ts(e.created_at) for e in events if _parse_ts(e.created_at)]
    last_progress = max(progress_times) if progress_times else None
    if last_progress is None:
        started = _parse_ts(primary_task.created_at)
        last_progress = started

    seconds_since = int((now - last_progress).total_seconds()) if last_progress else 999999

    if _cursor_in_long_op(events) and seconds_since <= cursor_long_op_grace_seconds:
        return MotionStatus.MOVING

    if seconds_since <= progress_stale_seconds:
        return MotionStatus.MOVING
    return MotionStatus.STALLED


def _latest_test_summary(events: list[ExecutionEventRecord]) -> dict[str, Any] | None:
    for event in events:
        if event.event_type != ExecutionEventType.TEST_FINISHED.value:
            continue
        meta = event.metadata or {}
        return {
            "command": event.command_summary,
            "status": event.status or "unknown",
            "passed": meta.get("passed"),
            "failed": meta.get("failed"),
            "duration_seconds": meta.get("duration_seconds"),
            "finished_at": event.created_at,
            "summary": event.result_summary,
        }
    for event in events:
        if event.event_type == ExecutionEventType.TEST_STARTED.value:
            return {
                "command": event.command_summary,
                "status": "running",
                "passed": None,
                "failed": None,
                "duration_seconds": None,
                "finished_at": None,
                "summary": None,
            }
    return None


def _git_summary(events: list[ExecutionEventRecord]) -> dict[str, Any]:
    git: dict[str, Any] = {
        "files_changed": None,
        "insertions": None,
        "deletions": None,
        "latest_commit_sha": None,
        "latest_commit_message": None,
        "push_status": None,
        "push_time": None,
    }
    for event in events:
        if event.event_type == ExecutionEventType.GIT_DIFF.value and event.metadata:
            git.update({k: event.metadata.get(k) for k in ("files_changed", "insertions", "deletions")})
            break
    for event in events:
        if event.event_type == ExecutionEventType.COMMIT_CREATED.value:
            meta = event.metadata or {}
            git["latest_commit_sha"] = meta.get("commit_sha") or event.result_summary
            git["latest_commit_message"] = meta.get("message")
            break
    for event in events:
        if event.event_type == ExecutionEventType.PUSH_FINISHED.value:
            meta = event.metadata or {}
            git["push_status"] = meta.get("push_status", "success")
            git["push_time"] = event.created_at
            if meta.get("commit_sha"):
                git["latest_commit_sha"] = meta.get("commit_sha")
            break
    return git


def build_execution_trace(
    *,
    settings: AutonomousDevSettings,
    snapshot: dict[str, Any],
    events: list[ExecutionEventRecord],
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    lease_raw = snapshot["lease"]
    lease = WorkerLeaseSnapshot(
        locked=lease_raw.locked,
        owner=lease_raw.owner,
        task_id=lease_raw.task_id,
        issue_number=lease_raw.issue_number,
        acquired_at=lease_raw.acquired_at,
        heartbeat_at=lease_raw.heartbeat_at,
        lease_expires_at=lease_raw.lease_expires_at,
    )
    primary_task = snapshot.get("primary_task")
    active_review = snapshot.get("active_review")
    reviewer_locked = snapshot.get("reviewer_locked", False)

    motion = derive_motion_status(
        primary_task=primary_task,
        lease=lease,
        reviewer_locked=reviewer_locked,
        active_review=active_review,
        events=events,
        progress_stale_seconds=settings.autonomous_progress_stale_seconds,
        cursor_long_op_grace_seconds=settings.cursor_long_op_grace_seconds,
        lease_ttl_seconds=settings.worker_lease_ttl_seconds,
        now=now,
    )

    progress_times = [_parse_ts(e.created_at) for e in events if _parse_ts(e.created_at)]
    last_progress_at = max(progress_times).isoformat() if progress_times else None
    seconds_since_progress = None
    if progress_times:
        seconds_since_progress = max(0, int((now - max(progress_times)).total_seconds()))

    hb_ts = _parse_ts(lease.heartbeat_at)
    seconds_since_heartbeat = (
        max(0, int((now - hb_ts).total_seconds())) if hb_ts else None
    )

    recent_files: list[str] = []
    for event in events:
        if event.file_path and event.file_path not in recent_files:
            recent_files.append(event.file_path)
        if len(recent_files) >= 20:
            break

    latest = events[0] if events else None
    current_file = None
    current_command = None
    current_phase = None
    current_action = None
    if latest:
        current_phase = latest.phase
        current_action = _event_action_label(latest)
        current_file = latest.file_path
        current_command = latest.command_summary
    if motion == MotionStatus.MOVING and _cursor_in_long_op(events):
        current_action = "Cursor Agent 长任务执行中（无细粒度 telemetry）"
        current_phase = "cursor"

    trace_events = [
        {
            "at": e.created_at,
            "event_type": e.event_type,
            "phase": e.phase,
            "action": _event_action_label(e),
            "file_path": e.file_path,
            "command_summary": e.command_summary,
            "result_summary": e.result_summary,
            "status": e.status,
        }
        for e in events[:100]
    ]

    return sanitize_for_json(
        {
            "motion_status": motion.value,
            "current_action": current_action,
            "current_phase": current_phase,
            "current_file": current_file,
            "current_command": current_command,
            "last_progress_at": last_progress_at,
            "seconds_since_last_progress": seconds_since_progress,
            "last_heartbeat_at": lease.heartbeat_at,
            "seconds_since_last_heartbeat": seconds_since_heartbeat,
            "recent_files": recent_files,
            "latest_test": _latest_test_summary(events),
            "git": _git_summary(events),
            "events": trace_events,
        }
    )
