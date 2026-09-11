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

CURSOR_PROGRESS_EVENTS = frozenset(
    {
        "CURSOR_STARTED",
        "CURSOR_FINISHED",
        "CURSOR_PROGRESS",
        "FILE_READ",
        "FILE_EDIT",
        "FILE_CREATE",
        "FILE_DELETE",
        "COMMAND_STARTED",
        "COMMAND_FINISHED",
    }
)

FILE_EVENT_TYPES = frozenset(
    {"FILE_READ", "FILE_EDIT", "FILE_CREATE", "FILE_DELETE"}
)


class ExecutionEventType(StrEnum):
    TASK_STARTED = "TASK_STARTED"
    ANALYSIS_STARTED = "ANALYSIS_STARTED"
    FILE_READ = "FILE_READ"
    FILE_EDIT = "FILE_EDIT"
    FILE_CREATE = "FILE_CREATE"
    FILE_DELETE = "FILE_DELETE"
    COMMAND_STARTED = "COMMAND_STARTED"
    COMMAND_FINISHED = "COMMAND_FINISHED"
    CURSOR_STARTED = "CURSOR_STARTED"
    CURSOR_PROGRESS = "CURSOR_PROGRESS"
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
    WAITING = "WAITING"
    REVIEWING = "REVIEWING"
    STALLED = "STALLED"
    STALE = "STALE"
    IDLE = "IDLE"
    FAILED = "FAILED"


class TelemetryLevel(StrEnum):
    FULL = "FULL"
    PARTIAL = "PARTIAL"
    BOUNDARY_ONLY = "BOUNDARY_ONLY"


_PHASE_LABELS: dict[str, str] = {
    "worker": "Worker",
    "analysis": "Analysis",
    "cursor": "Cursor",
    "testing": "Testing",
    "git": "Git",
    "review": "Reviewer",
    "command": "Command",
    "ruff": "Ruff",
}


_EVENT_DISPLAY: dict[str, str] = {
    "TASK_STARTED": "TASK_START",
    "ANALYSIS_STARTED": "ANALYSIS",
    "CURSOR_STARTED": "CURSOR_START",
    "CURSOR_PROGRESS": "CURSOR_PROGRESS",
    "CURSOR_FINISHED": "CURSOR_FINISH",
    "FILE_READ": "FILE_READ",
    "FILE_EDIT": "FILE_EDIT",
    "FILE_CREATE": "FILE_CREATE",
    "FILE_DELETE": "FILE_DELETE",
    "COMMAND_STARTED": "CMD_START",
    "COMMAND_FINISHED": "CMD_FINISH",
    "TEST_STARTED": "TEST_START",
    "TEST_FINISHED": "TEST_PASS",
    "RUFF_STARTED": "RUFF_START",
    "RUFF_FINISHED": "RUFF_FINISH",
    "GIT_DIFF": "GIT_DIFF",
    "COMMIT_STARTED": "COMMIT_START",
    "COMMIT_CREATED": "COMMIT",
    "PUSH_STARTED": "PUSH_START",
    "PUSH_FINISHED": "PUSH",
    "REVIEW_STARTED": "REVIEW_START",
    "REVIEW_FINISHED": "REVIEW_PASS",
    "TASK_FINISHED": "TASK_FINISH",
    "TASK_FAILED": "TASK_FAIL",
}


_EVENT_ACTION_ZH: dict[str, str] = {
    "TASK_STARTED": "Worker 已启动任务",
    "ANALYSIS_STARTED": "正在分析任务",
    "FILE_READ": "正在读取 {file}",
    "FILE_EDIT": "正在修改 {file}",
    "COMMAND_STARTED": "正在执行 {cmd}",
    "COMMAND_FINISHED": "命令完成",
    "CURSOR_STARTED": "Cursor Agent 执行中",
    "CURSOR_PROGRESS": "Cursor 工具进展",
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
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
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

    def cursor_progress(
        self,
        *,
        action: str,
        file_path: str | Path | None = None,
        command_summary: list[str] | str | None = None,
        result_summary: str | None = None,
    ) -> None:
        self.record(
            ExecutionEventType.CURSOR_PROGRESS,
            phase="cursor",
            action=action,
            file_path=file_path,
            command_summary=command_summary,
            result_summary=result_summary,
        )

    def file_edit(self, path: str | Path) -> None:
        self.record(
            ExecutionEventType.FILE_EDIT,
            phase="cursor",
            file_path=path,
            action=f"正在修改 {sanitize_repo_path(path, self.repo_root) or '[path]'}",
        )

    def ruff_started(self, cmd: list[str]) -> None:
        self.record(
            ExecutionEventType.RUFF_STARTED,
            phase="ruff",
            command_summary=cmd,
            action=f"正在运行 {sanitize_command(cmd)}",
        )

    def ruff_finished(self, *, output: str, passed: bool) -> None:
        self.record(
            ExecutionEventType.RUFF_FINISHED,
            phase="ruff",
            result_summary=redact_secrets(output)[:300],
            status="pass" if passed else "fail",
        )

    def test_finished(self, *, output: str, passed: bool) -> None:
        summary = redact_secrets(output)[:300]
        passed_n = failed_n = None
        duration = None
        m = re.search(r"(\d+) passed", summary)
        if m:
            passed_n = int(m.group(1))
        m = re.search(r"(\d+) failed", summary)
        if m:
            failed_n = int(m.group(1))
        m = re.search(r"(\d+(?:\.\d+)?)s", summary)
        if m:
            try:
                duration = float(m.group(1))
            except ValueError:
                duration = None
        self.record(
            ExecutionEventType.TEST_FINISHED,
            phase="testing",
            result_summary=summary,
            status="pass" if passed else "fail",
            metadata={
                "passed": passed_n,
                "failed": failed_n,
                "duration_seconds": duration,
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


def _cursor_started_at(events: list[ExecutionEventRecord]) -> datetime | None:
    for event in reversed(events):
        if event.event_type == ExecutionEventType.CURSOR_STARTED.value:
            return _parse_ts(event.created_at)
    return None


def _has_recent_failure(events: list[ExecutionEventRecord], *, within_seconds: int, now: datetime) -> bool:
    for event in events:
        if event.event_type not in {
            ExecutionEventType.TASK_FAILED.value,
            ExecutionEventType.TEST_FINISHED.value,
            ExecutionEventType.RUFF_FINISHED.value,
        }:
            continue
        ts = _parse_ts(event.created_at)
        if ts is None:
            continue
        if (now - ts).total_seconds() > within_seconds:
            continue
        if event.event_type == ExecutionEventType.TASK_FAILED.value:
            return True
        if event.status == "fail":
            return True
    return False


def _derive_telemetry_level(events: list[ExecutionEventRecord]) -> TelemetryLevel:
    types = {e.event_type for e in events}
    if types & FILE_EVENT_TYPES:
        return TelemetryLevel.FULL
    if ExecutionEventType.CURSOR_PROGRESS.value in types or (
        ExecutionEventType.COMMAND_STARTED.value in types
        and ExecutionEventType.CURSOR_STARTED.value in types
    ):
        return TelemetryLevel.PARTIAL
    return TelemetryLevel.BOUNDARY_ONLY


def derive_motion_status(
    *,
    primary_task: TaskRecord | None,
    lease: WorkerLeaseSnapshot,
    reviewer_locked: bool,
    active_review: ReviewInvocationRecord | None,
    events: list[ExecutionEventRecord],
    progress_stale_seconds: int,
    cursor_long_op_grace_seconds: int,
    cursor_long_op_suspect_seconds: int,
    lease_ttl_seconds: int,
    now: datetime | None = None,
) -> MotionStatus:
    now = now or datetime.now(UTC)
    review_running = reviewer_locked or (
        active_review is not None
        and active_review.status == ReviewInvocationStatus.RUNNING
    )
    review_pending = active_review is not None and active_review.status in {
        ReviewInvocationStatus.PENDING,
        ReviewInvocationStatus.FAILED,
    }

    if primary_task and primary_task.status in {TaskStatus.FAILED, TaskStatus.NEEDS_FIX}:
        if _has_recent_failure(events, within_seconds=600, now=now) or primary_task.error:
            return MotionStatus.FAILED

    if primary_task is None or primary_task.status not in {
        TaskStatus.RUNNING,
        TaskStatus.QUEUED,
        TaskStatus.READY_FOR_REVIEW,
    }:
        if review_running:
            return MotionStatus.REVIEWING
        return MotionStatus.IDLE

    if primary_task.status in {TaskStatus.RUNNING, TaskStatus.QUEUED} and (
        is_worker_runtime_stale(
            primary_task,
            lease,
            lease_ttl_seconds=lease_ttl_seconds,
            now=now,
        )
        or not is_heartbeat_fresh(lease, lease_ttl_seconds=lease_ttl_seconds, now=now)
    ):
        return MotionStatus.STALE

    if review_running:
        return MotionStatus.REVIEWING

    if primary_task.status == TaskStatus.READY_FOR_REVIEW:
        if review_pending or not review_running:
            return MotionStatus.WAITING

    progress_times = [_parse_ts(e.created_at) for e in events if _parse_ts(e.created_at)]
    last_progress = max(progress_times) if progress_times else _parse_ts(primary_task.created_at)
    seconds_since = (
        int((now - last_progress).total_seconds()) if last_progress else 999999
    )

    cursor_long = _cursor_in_long_op(events)
    if cursor_long:
        cursor_started = _cursor_started_at(events)
        cursor_elapsed = (
            int((now - cursor_started).total_seconds()) if cursor_started else seconds_since
        )
        if cursor_elapsed <= cursor_long_op_grace_seconds:
            return MotionStatus.MOVING
        if cursor_elapsed <= cursor_long_op_suspect_seconds:
            return MotionStatus.MOVING
        return MotionStatus.STALLED

    if seconds_since <= progress_stale_seconds:
        return MotionStatus.MOVING
    return MotionStatus.STALLED


def _latest_ruff_summary(events: list[ExecutionEventRecord]) -> dict[str, Any] | None:
    for event in events:
        if event.event_type != ExecutionEventType.RUFF_FINISHED.value:
            continue
        return {
            "command": event.command_summary,
            "status": event.status or "unknown",
            "finished_at": event.created_at,
            "summary": event.result_summary,
        }
    for event in events:
        if event.event_type == ExecutionEventType.RUFF_STARTED.value:
            return {
                "command": event.command_summary,
                "status": "running",
                "finished_at": None,
                "summary": None,
            }
    return None


def _code_changes_summary(events: list[ExecutionEventRecord]) -> dict[str, Any]:
    modified: list[str] = []
    added: list[str] = []
    deleted: list[str] = []
    for event in events:
        if not event.file_path:
            continue
        path = event.file_path
        if event.event_type == ExecutionEventType.FILE_EDIT.value and path not in modified:
            modified.append(path)
        elif event.event_type == ExecutionEventType.FILE_CREATE.value and path not in added:
            added.append(path)
        elif event.event_type == ExecutionEventType.FILE_DELETE.value and path not in deleted:
            deleted.append(path)
        elif event.event_type == ExecutionEventType.FILE_READ.value:
            continue
    return {
        "modified_files": modified[:20],
        "added_files": added[:20],
        "deleted_files": deleted[:20],
    }


def _derive_current_phase(events: list[ExecutionEventRecord], motion: MotionStatus) -> str | None:
    if motion == MotionStatus.REVIEWING:
        return "Reviewer"
    if motion == MotionStatus.WAITING:
        return "Waiting"
    if motion == MotionStatus.IDLE:
        return None
    for event in events:
        phase = event.phase
        et = event.event_type
        if et == ExecutionEventType.CURSOR_STARTED.value and _cursor_in_long_op(events):
            return "Cursor"
        if et in {ExecutionEventType.FILE_EDIT.value, ExecutionEventType.FILE_READ.value}:
            return "Editing"
        if et in {ExecutionEventType.TEST_STARTED.value, ExecutionEventType.TEST_FINISHED.value}:
            return "Testing"
        if et in {ExecutionEventType.RUFF_STARTED.value, ExecutionEventType.RUFF_FINISHED.value}:
            return "Ruff"
        if et in {
            ExecutionEventType.GIT_DIFF.value,
            ExecutionEventType.COMMIT_STARTED.value,
            ExecutionEventType.COMMIT_CREATED.value,
        }:
            return "Git"
        if et in {ExecutionEventType.PUSH_STARTED.value, ExecutionEventType.PUSH_FINISHED.value}:
            return "Push"
        if phase and phase in _PHASE_LABELS:
            return _PHASE_LABELS[phase]
    return None


def _format_elapsed(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    h, rem = divmod(max(0, seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _event_display_type(event_type: str, *, status: str | None = None) -> str:
    if event_type == ExecutionEventType.TEST_FINISHED.value and status == "fail":
        return "TEST_FAIL"
    if event_type == ExecutionEventType.RUFF_FINISHED.value and status == "fail":
        return "RUFF_FAIL"
    if event_type == ExecutionEventType.REVIEW_FINISHED.value and status == "fail":
        return "REVIEW_FAIL"
    return _EVENT_DISPLAY.get(event_type, event_type)


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
        "push_at": None,
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
            git["push_at"] = event.created_at
            if meta.get("commit_sha"):
                git["latest_commit_sha"] = meta.get("commit_sha")
            break
    return git


def build_execution_trace(
    *,
    settings: AutonomousDevSettings,
    snapshot: dict[str, Any],
    events: list[ExecutionEventRecord],
    trace_task: TaskRecord | None = None,
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
    primary_task = trace_task or snapshot.get("primary_task")
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
        cursor_long_op_suspect_seconds=settings.cursor_long_op_suspect_seconds,
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

    started_at = primary_task.created_at if primary_task else None
    elapsed_seconds = None
    elapsed_display = None
    if primary_task:
        start = _parse_ts(primary_task.created_at)
        if start:
            elapsed_seconds = max(0, int((now - start).total_seconds()))
            elapsed_display = _format_elapsed(elapsed_seconds)

    cursor_long = _cursor_in_long_op(events)
    cursor_started = _cursor_started_at(events)
    cursor_elapsed_seconds = None
    if cursor_long and cursor_started:
        cursor_elapsed_seconds = max(0, int((now - cursor_started).total_seconds()))

    telemetry_level = _derive_telemetry_level(events)
    current_phase = _derive_current_phase(events, motion)

    recent_files: list[str] = []
    for event in events:
        if event.file_path and event.file_path not in recent_files:
            recent_files.append(event.file_path)
        if len(recent_files) >= 20:
            break

    latest = events[0] if events else None
    current_file = None
    current_command = None
    current_action = None
    long_running = False
    if latest:
        current_action = _event_action_label(latest)
        current_file = latest.file_path
        current_command = latest.command_summary

    if cursor_long:
        long_running = True
        if telemetry_level == TelemetryLevel.BOUNDARY_ONLY:
            current_action = "Cursor Agent 长任务执行中"
            current_file = None
        elif current_file is None and telemetry_level != TelemetryLevel.FULL:
            current_action = "Cursor Agent 长任务执行中"
    elif motion == MotionStatus.WAITING:
        current_action = "等待 Reviewer"
        current_phase = "Waiting"
    elif motion == MotionStatus.STALLED:
        current_action = current_action or "长时间无新的开发进展"
    elif motion == MotionStatus.STALE:
        current_action = "Worker heartbeat 已中断"
    elif motion == MotionStatus.FAILED:
        current_action = current_action or "任务执行失败"

    code_changes = _code_changes_summary(events)

    trace_events = [
        {
            "at": e.created_at,
            "event_type": e.event_type,
            "display_type": _event_display_type(e.event_type, status=e.status),
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
            "started_at": started_at,
            "elapsed_seconds": elapsed_seconds,
            "elapsed_display": elapsed_display,
            "last_progress_at": last_progress_at,
            "seconds_since_last_progress": seconds_since_progress,
            "last_heartbeat_at": lease.heartbeat_at,
            "seconds_since_last_heartbeat": seconds_since_heartbeat,
            "long_running": long_running,
            "cursor_elapsed_seconds": cursor_elapsed_seconds,
            "cursor_elapsed_display": _format_elapsed(cursor_elapsed_seconds),
            "telemetry_level": telemetry_level.value,
            "display_timezone": "Asia/Shanghai",
            "recent_files": recent_files,
            "code_changes": code_changes,
            "latest_test": _latest_test_summary(events),
            "latest_ruff": _latest_ruff_summary(events),
            "git": _git_summary(events),
            "events": trace_events,
        }
    )
