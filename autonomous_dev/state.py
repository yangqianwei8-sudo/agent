"""Persistent delivery / task / lease lock / execution identity state (SQLite)."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PROGRESS_EVENT_TYPES = (
    "TASK_STARTED",
    "CURSOR_STARTED",
    "CURSOR_PROGRESS",
    "CURSOR_FINISHED",
    "FILE_READ",
    "FILE_EDIT",
    "FILE_CREATE",
    "FILE_DELETE",
    "COMMAND_STARTED",
    "COMMAND_FINISHED",
    "COMMIT_CREATED",
    "PUSH_FINISHED",
    "TEST_STARTED",
    "TEST_FINISHED",
)


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    READY_FOR_REVIEW = "ready-for-review"
    NEEDS_FIX = "needs-fix"
    PRODUCT_DECISION = "product-decision"
    COMPLETED = "completed"
    FAILED = "failed"


class DeliveryStatus(StrEnum):
    RECEIVED = "received"
    PROCESSED = "processed"
    IGNORED = "ignored"
    FAILED = "failed"


class ReviewVerdict(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    PRODUCT_DECISION = "PRODUCT_DECISION"
    SKIP = "SKIP"


class ReviewInvocationStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class HandoffStatus(StrEnum):
    PENDING = "pending"
    ACTIVATED = "activated"
    WORKER_STARTED = "worker_started"
    STALLED = "stalled"
    WAITING_PRODUCT = "waiting_product"
    COMPLETED = "completed"


class WorkerReactivationStatus(StrEnum):
    PENDING = "pending"
    SCHEDULED = "scheduled"
    RUNNING = "running"
    EXHAUSTED = "exhausted"


class ReviewerReactivationStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    RETRYING = "retrying"
    STALE_RECOVERED = "stale-recovered"
    EXHAUSTED = "exhausted"


@dataclass
class ExecutionEventRecord:
    id: int
    task_id: int | None
    issue_number: int | None
    generation: str | None
    event_type: str
    phase: str | None
    action: str | None
    file_path: str | None
    command_summary: str | None
    result_summary: str | None
    status: str | None
    metadata: dict[str, Any] | None
    created_at: str


@dataclass
class TaskRecord:
    id: int
    issue_number: int
    status: TaskStatus
    delivery_id: str | None
    commit_sha: str | None
    error: str | None
    execution_key: str | None
    created_at: str
    updated_at: str


@dataclass
class LeaseRecord:
    locked: bool
    owner: str | None
    issue_number: int | None
    task_id: int | None
    acquired_at: str | None
    heartbeat_at: str | None
    lease_expires_at: str | None


@dataclass
class HandoffRecord:
    handoff_id: str
    idempotency_key: str
    source_task_id: int
    source_issue_number: int
    commit_sha: str
    status: HandoffStatus
    next_issue_number: int | None
    reason: str | None
    created_at: str
    activated_at: str | None
    worker_started_at: str | None
    retry_count: int = 0


@dataclass
class WorkerReactivationRecord:
    issue_number: int
    task_id: int | None
    attempt_count: int
    next_retry_at: str | None
    last_error: str | None
    status: WorkerReactivationStatus
    last_kick_at: str | None
    updated_at: str


@dataclass
class ExecutionFailureRecord:
    execution_key: str
    task_id: int
    issue_number: int
    stage: str
    error_class: str
    error_message: str
    retry_count: int
    next_action: str
    comment_posted_at: str | None
    created_at: str
    updated_at: str


@dataclass
class ReviewerReactivationRecord:
    issue_number: int
    task_id: int | None
    attempt_count: int
    next_retry_at: str | None
    last_error: str | None
    status: ReviewerReactivationStatus
    last_kick_at: str | None
    updated_at: str


@dataclass
class ReviewInvocationRecord:
    invocation_id: str
    task_id: int
    issue_number: int
    commit_sha: str
    verdict: ReviewVerdict | None
    status: ReviewInvocationStatus
    error: str | None
    created_at: str
    completed_at: str | None
    attempt_count: int = 0
    next_retry_at: str | None = None
    started_at: str | None = None


class StateStore:
    _BUSY_TIMEOUT_MS = 2000

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()

    @classmethod
    def _configure_connection(cls, conn: sqlite3.Connection) -> None:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"PRAGMA busy_timeout={cls._BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA synchronous=NORMAL")

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        self._configure_connection(conn)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    @contextmanager
    def _read_conn(self) -> Iterator[sqlite3.Connection]:
        """Short read-only connection for dashboard / observability."""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        self._configure_connection(conn)
        try:
            yield conn
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS webhook_deliveries (
                    delivery_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    action TEXT,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    processed_at TEXT,
                    error TEXT
                );
                CREATE TABLE IF NOT EXISTS task_executions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    issue_number INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    delivery_id TEXT,
                    commit_sha TEXT,
                    error TEXT,
                    execution_key TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(issue_number, delivery_id)
                );
                CREATE TABLE IF NOT EXISTS worker_lock (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    locked INTEGER NOT NULL DEFAULT 0,
                    issue_number INTEGER,
                    task_id INTEGER,
                    locked_at TEXT,
                    owner TEXT,
                    acquired_at TEXT,
                    heartbeat_at TEXT,
                    lease_expires_at TEXT
                );
                CREATE TABLE IF NOT EXISTS review_triggers (
                    task_id INTEGER PRIMARY KEY,
                    last_review_trigger_at TEXT,
                    trigger_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS review_invocations (
                    invocation_id TEXT PRIMARY KEY,
                    task_id INTEGER NOT NULL,
                    issue_number INTEGER NOT NULL,
                    commit_sha TEXT NOT NULL,
                    verdict TEXT,
                    status TEXT NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    next_retry_at TEXT,
                    started_at TEXT,
                    UNIQUE(task_id, commit_sha)
                );
                CREATE TABLE IF NOT EXISTS reviewer_lock (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    locked INTEGER NOT NULL DEFAULT 0,
                    task_id INTEGER,
                    owner TEXT,
                    acquired_at TEXT,
                    lease_expires_at TEXT
                );
                INSERT OR IGNORE INTO worker_lock (id, locked) VALUES (1, 0);
                INSERT OR IGNORE INTO reviewer_lock (id, locked) VALUES (1, 0);
                CREATE TABLE IF NOT EXISTS execution_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER,
                    issue_number INTEGER,
                    generation TEXT,
                    event_type TEXT NOT NULL,
                    phase TEXT,
                    action TEXT,
                    file_path TEXT,
                    command_summary TEXT,
                    result_summary TEXT,
                    status TEXT,
                    metadata_json TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_execution_events_task
                    ON execution_events(task_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_execution_events_created
                    ON execution_events(created_at DESC);
                CREATE TABLE IF NOT EXISTS task_handoffs (
                    handoff_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    source_task_id INTEGER NOT NULL,
                    source_issue_number INTEGER NOT NULL,
                    commit_sha TEXT NOT NULL,
                    status TEXT NOT NULL,
                    next_issue_number INTEGER,
                    reason TEXT,
                    created_at TEXT NOT NULL,
                    activated_at TEXT,
                    worker_started_at TEXT,
                    retry_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_task_handoffs_status
                    ON task_handoffs(status, created_at DESC);
                CREATE TABLE IF NOT EXISTS worker_reactivations (
                    issue_number INTEGER PRIMARY KEY,
                    task_id INTEGER,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    next_retry_at TEXT,
                    last_error TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    last_kick_at TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reviewer_reactivations (
                    issue_number INTEGER PRIMARY KEY,
                    task_id INTEGER,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    next_retry_at TEXT,
                    last_error TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    last_kick_at TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS execution_failures (
                    execution_key TEXT PRIMARY KEY,
                    task_id INTEGER NOT NULL,
                    issue_number INTEGER NOT NULL,
                    stage TEXT NOT NULL,
                    error_class TEXT NOT NULL,
                    error_message TEXT NOT NULL,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    next_action TEXT NOT NULL,
                    comment_posted_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_execution_failures_issue
                    ON execution_failures(issue_number, updated_at DESC);
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_review_invocations_task "
                "ON review_invocations(task_id, commit_sha)"
            )
            inv_cols = {
                row[1] for row in conn.execute("PRAGMA table_info(review_invocations)").fetchall()
            }
            for col, ddl in (
                ("attempt_count", "ALTER TABLE review_invocations ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0"),
                ("next_retry_at", "ALTER TABLE review_invocations ADD COLUMN next_retry_at TEXT"),
                ("started_at", "ALTER TABLE review_invocations ADD COLUMN started_at TEXT"),
            ):
                if col not in inv_cols:
                    conn.execute(ddl)
            cols = {row[1] for row in conn.execute("PRAGMA table_info(task_executions)").fetchall()}
            if "execution_key" not in cols:
                conn.execute("ALTER TABLE task_executions ADD COLUMN execution_key TEXT")
            lock_cols = {row[1] for row in conn.execute("PRAGMA table_info(worker_lock)").fetchall()}
            for col, ddl in (
                ("owner", "ALTER TABLE worker_lock ADD COLUMN owner TEXT"),
                ("acquired_at", "ALTER TABLE worker_lock ADD COLUMN acquired_at TEXT"),
                ("heartbeat_at", "ALTER TABLE worker_lock ADD COLUMN heartbeat_at TEXT"),
                ("lease_expires_at", "ALTER TABLE worker_lock ADD COLUMN lease_expires_at TEXT"),
            ):
                if col not in lock_cols:
                    conn.execute(ddl)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_task_executions_execution_key ON task_executions(execution_key)"
            )

    def record_delivery(
        self,
        *,
        delivery_id: str,
        event_type: str,
        action: str | None,
        payload: dict[str, Any],
        status: DeliveryStatus,
        error: str | None = None,
    ) -> bool:
        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn() as conn:
            existing = conn.execute(
                "SELECT delivery_id FROM webhook_deliveries WHERE delivery_id = ?",
                (delivery_id,),
            ).fetchone()
            if existing:
                return False
            conn.execute(
                """
                INSERT INTO webhook_deliveries
                (delivery_id, event_type, action, payload_json, status, received_at, processed_at, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    delivery_id,
                    event_type,
                    action,
                    json.dumps(payload, ensure_ascii=False),
                    status.value,
                    now,
                    now if status != DeliveryStatus.RECEIVED else None,
                    error,
                ),
            )
            return True

    def mark_delivery(
        self,
        delivery_id: str,
        *,
        status: DeliveryStatus,
        error: str | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                UPDATE webhook_deliveries
                SET status = ?, processed_at = ?, error = ?
                WHERE delivery_id = ?
                """,
                (status.value, now, error, delivery_id),
            )

    def delivery_exists(self, delivery_id: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM webhook_deliveries WHERE delivery_id = ?",
                (delivery_id,),
            ).fetchone()
            return row is not None

    def create_task(
        self,
        *,
        issue_number: int,
        delivery_id: str | None,
        execution_key: str | None = None,
        status: TaskStatus = TaskStatus.QUEUED,
    ) -> TaskRecord:
        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO task_executions
                (issue_number, status, delivery_id, execution_key, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(issue_number, delivery_id) DO NOTHING
                """,
                (issue_number, status.value, delivery_id, execution_key, now, now),
            )
            if cur.rowcount == 0 and delivery_id:
                row = conn.execute(
                    """
                    SELECT * FROM task_executions
                    WHERE issue_number = ? AND delivery_id = ?
                    """,
                    (issue_number, delivery_id),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM task_executions WHERE id = last_insert_rowid()"
                ).fetchone()
            assert row is not None
            return self._row_to_task(row)

    def update_task(
        self,
        task_id: int,
        *,
        status: TaskStatus | None = None,
        commit_sha: str | None = None,
        error: str | None = None,
        execution_key: str | None = None,
    ) -> TaskRecord:
        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM task_executions WHERE id = ?", (task_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"task {task_id} not found")
            new_status = status.value if status else row["status"]
            new_commit = commit_sha if commit_sha is not None else row["commit_sha"]
            new_error = error if error is not None else row["error"]
            new_exec_key = execution_key if execution_key is not None else row["execution_key"]
            conn.execute(
                """
                UPDATE task_executions
                SET status = ?, commit_sha = ?, error = ?, execution_key = ?, updated_at = ?
                WHERE id = ?
                """,
                (new_status, new_commit, new_error, new_exec_key, now, task_id),
            )
            updated = conn.execute(
                "SELECT * FROM task_executions WHERE id = ?", (task_id,)
            ).fetchone()
            assert updated is not None
            return self._row_to_task(updated)

    def get_task(self, task_id: int) -> TaskRecord | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM task_executions WHERE id = ?", (task_id,)
            ).fetchone()
            return self._row_to_task(row) if row else None

    def get_active_task(self) -> TaskRecord | None:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT * FROM task_executions
                WHERE status IN ('queued', 'running', 'ready-for-review')
                ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
            return self._row_to_task(row) if row else None

    def get_task_by_issue(self, issue_number: int) -> TaskRecord | None:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT * FROM task_executions
                WHERE issue_number = ?
                ORDER BY id DESC LIMIT 1
                """,
                (issue_number,),
            ).fetchone()
            return self._row_to_task(row) if row else None

    def get_task_by_commit(self, commit_sha: str) -> TaskRecord | None:
        prefix = commit_sha[:12]
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT * FROM task_executions
                WHERE commit_sha IS NOT NULL AND commit_sha LIKE ?
                ORDER BY id DESC LIMIT 1
                """,
                (f"{prefix}%",),
            ).fetchone()
            return self._row_to_task(row) if row else None

    def get_running_task_for_issue(self, issue_number: int) -> TaskRecord | None:
        """Latest queued/running task for an issue without a recorded commit."""
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT * FROM task_executions
                WHERE issue_number = ?
                  AND status IN ('queued', 'running')
                  AND (commit_sha IS NULL OR commit_sha = '')
                ORDER BY id DESC LIMIT 1
                """,
                (issue_number,),
            ).fetchone()
            return self._row_to_task(row) if row else None

    def get_active_task_for_issue(self, issue_number: int) -> TaskRecord | None:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT * FROM task_executions
                WHERE issue_number = ?
                  AND status IN ('queued', 'running', 'ready-for-review')
                ORDER BY id DESC LIMIT 1
                """,
                (issue_number,),
            ).fetchone()
            return self._row_to_task(row) if row else None

    def get_worker_reactivation(self, issue_number: int) -> WorkerReactivationRecord | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM worker_reactivations WHERE issue_number = ?",
                (issue_number,),
            ).fetchone()
            return self._row_to_worker_reactivation(row) if row else None

    def list_due_worker_reactivations(
        self,
        *,
        limit: int = 20,
        now_iso: str | None = None,
    ) -> list[WorkerReactivationRecord]:
        now = now_iso or datetime.now(UTC).isoformat()
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM worker_reactivations
                WHERE status IN ('pending', 'scheduled')
                  AND (next_retry_at IS NULL OR next_retry_at <= ?)
                ORDER BY updated_at ASC LIMIT ?
                """,
                (now, limit),
            ).fetchall()
            return [self._row_to_worker_reactivation(r) for r in rows]

    def upsert_worker_reactivation(
        self,
        *,
        issue_number: int,
        task_id: int | None = None,
        attempt_count: int | None = None,
        next_retry_at: str | None = None,
        last_error: str | None = None,
        status: WorkerReactivationStatus | None = None,
        last_kick_at: str | None = None,
        clear_next_retry: bool = False,
    ) -> WorkerReactivationRecord:
        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM worker_reactivations WHERE issue_number = ?",
                (issue_number,),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO worker_reactivations
                    (issue_number, task_id, attempt_count, next_retry_at, last_error,
                     status, last_kick_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        issue_number,
                        task_id,
                        attempt_count or 0,
                        next_retry_at,
                        last_error,
                        (status or WorkerReactivationStatus.PENDING).value,
                        last_kick_at,
                        now,
                    ),
                )
            else:
                new_attempt = attempt_count if attempt_count is not None else row["attempt_count"]
                new_status = (status or WorkerReactivationStatus(row["status"])).value
                new_task = task_id if task_id is not None else row["task_id"]
                new_error = last_error if last_error is not None else row["last_error"]
                new_kick = last_kick_at if last_kick_at is not None else row["last_kick_at"]
                if clear_next_retry:
                    new_next = None
                elif next_retry_at is not None:
                    new_next = next_retry_at
                else:
                    new_next = row["next_retry_at"]
                conn.execute(
                    """
                    UPDATE worker_reactivations
                    SET task_id = ?, attempt_count = ?, next_retry_at = ?, last_error = ?,
                        status = ?, last_kick_at = ?, updated_at = ?
                    WHERE issue_number = ?
                    """,
                    (
                        new_task,
                        new_attempt,
                        new_next,
                        new_error,
                        new_status,
                        new_kick,
                        now,
                        issue_number,
                    ),
                )
            updated = conn.execute(
                "SELECT * FROM worker_reactivations WHERE issue_number = ?",
                (issue_number,),
            ).fetchone()
            assert updated is not None
            return self._row_to_worker_reactivation(updated)

    def get_execution_failure(self, execution_key: str) -> ExecutionFailureRecord | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM execution_failures WHERE execution_key = ?",
                (execution_key,),
            ).fetchone()
            return self._row_to_execution_failure(row) if row else None

    def get_latest_execution_failure_for_issue(
        self,
        issue_number: int,
    ) -> ExecutionFailureRecord | None:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT * FROM execution_failures
                WHERE issue_number = ?
                ORDER BY updated_at DESC LIMIT 1
                """,
                (issue_number,),
            ).fetchone()
            return self._row_to_execution_failure(row) if row else None

    def upsert_execution_failure(
        self,
        *,
        execution_key: str,
        task_id: int,
        issue_number: int,
        stage: str,
        error_class: str,
        error_message: str,
        retry_count: int,
        next_action: str,
    ) -> ExecutionFailureRecord:
        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM execution_failures WHERE execution_key = ?",
                (execution_key,),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO execution_failures
                    (execution_key, task_id, issue_number, stage, error_class,
                     error_message, retry_count, next_action, comment_posted_at,
                     created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                    """,
                    (
                        execution_key,
                        task_id,
                        issue_number,
                        stage,
                        error_class,
                        error_message[:2000],
                        retry_count,
                        next_action[:500],
                        now,
                        now,
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE execution_failures
                    SET task_id = ?, issue_number = ?, stage = ?, error_class = ?,
                        error_message = ?, retry_count = ?, next_action = ?, updated_at = ?
                    WHERE execution_key = ?
                    """,
                    (
                        task_id,
                        issue_number,
                        stage,
                        error_class,
                        error_message[:2000],
                        retry_count,
                        next_action[:500],
                        now,
                        execution_key,
                    ),
                )
            updated = conn.execute(
                "SELECT * FROM execution_failures WHERE execution_key = ?",
                (execution_key,),
            ).fetchone()
            assert updated is not None
            return self._row_to_execution_failure(updated)

    def mark_execution_failure_comment_posted(self, execution_key: str) -> None:
        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                UPDATE execution_failures
                SET comment_posted_at = ?, updated_at = ?
                WHERE execution_key = ? AND comment_posted_at IS NULL
                """,
                (now, now, execution_key),
            )

    def get_reviewer_reactivation(self, issue_number: int) -> ReviewerReactivationRecord | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM reviewer_reactivations WHERE issue_number = ?",
                (issue_number,),
            ).fetchone()
            return self._row_to_reviewer_reactivation(row) if row else None

    def list_due_reviewer_reactivations(
        self,
        *,
        limit: int = 20,
        now_iso: str | None = None,
    ) -> list[ReviewerReactivationRecord]:
        now = now_iso or datetime.now(UTC).isoformat()
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM reviewer_reactivations
                WHERE status IN ('pending', 'running', 'retrying', 'stale-recovered')
                  AND (next_retry_at IS NULL OR next_retry_at <= ?)
                ORDER BY updated_at ASC LIMIT ?
                """,
                (now, limit),
            ).fetchall()
            return [self._row_to_reviewer_reactivation(r) for r in rows]

    def upsert_reviewer_reactivation(
        self,
        *,
        issue_number: int,
        task_id: int | None = None,
        attempt_count: int | None = None,
        next_retry_at: str | None = None,
        last_error: str | None = None,
        status: ReviewerReactivationStatus | None = None,
        last_kick_at: str | None = None,
        clear_next_retry: bool = False,
    ) -> ReviewerReactivationRecord:
        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM reviewer_reactivations WHERE issue_number = ?",
                (issue_number,),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO reviewer_reactivations
                    (issue_number, task_id, attempt_count, next_retry_at, last_error,
                     status, last_kick_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        issue_number,
                        task_id,
                        attempt_count or 0,
                        next_retry_at,
                        last_error,
                        (status or ReviewerReactivationStatus.PENDING).value,
                        last_kick_at,
                        now,
                    ),
                )
            else:
                new_attempt = attempt_count if attempt_count is not None else row["attempt_count"]
                new_status = (status or ReviewerReactivationStatus(row["status"])).value
                new_task = task_id if task_id is not None else row["task_id"]
                new_error = last_error if last_error is not None else row["last_error"]
                new_kick = last_kick_at if last_kick_at is not None else row["last_kick_at"]
                if clear_next_retry:
                    new_next = None
                elif next_retry_at is not None:
                    new_next = next_retry_at
                else:
                    new_next = row["next_retry_at"]
                conn.execute(
                    """
                    UPDATE reviewer_reactivations
                    SET task_id = ?, attempt_count = ?, next_retry_at = ?, last_error = ?,
                        status = ?, last_kick_at = ?, updated_at = ?
                    WHERE issue_number = ?
                    """,
                    (
                        new_task,
                        new_attempt,
                        new_next,
                        new_error,
                        new_status,
                        new_kick,
                        now,
                        issue_number,
                    ),
                )
            updated = conn.execute(
                "SELECT * FROM reviewer_reactivations WHERE issue_number = ?",
                (issue_number,),
            ).fetchone()
            assert updated is not None
            return self._row_to_reviewer_reactivation(updated)

    def get_active_execution(self, execution_key: str) -> TaskRecord | None:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT * FROM task_executions
                WHERE execution_key = ?
                  AND status IN ('queued', 'running', 'ready-for-review')
                ORDER BY id DESC LIMIT 1
                """,
                (execution_key,),
            ).fetchone()
            return self._row_to_task(row) if row else None

    def get_task_by_execution_key(self, execution_key: str) -> TaskRecord | None:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT * FROM task_executions
                WHERE execution_key = ?
                ORDER BY id DESC LIMIT 1
                """,
                (execution_key,),
            ).fetchone()
            return self._row_to_task(row) if row else None

    def record_review_trigger(self, task_id: int) -> None:
        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                INSERT INTO review_triggers (task_id, last_review_trigger_at, trigger_count)
                VALUES (?, ?, 1)
                ON CONFLICT(task_id) DO UPDATE SET
                    last_review_trigger_at = excluded.last_review_trigger_at,
                    trigger_count = review_triggers.trigger_count + 1
                """,
                (task_id, now),
            )

    def get_review_trigger(self, task_id: int) -> tuple[str | None, int]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT last_review_trigger_at, trigger_count FROM review_triggers WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if not row:
                return None, 0
            return row["last_review_trigger_at"], row["trigger_count"]

    def get_stale_ready_for_review_tasks(self, *, older_than_seconds: int) -> list[TaskRecord]:
        cutoff = (datetime.now(UTC) - timedelta(seconds=older_than_seconds)).isoformat()
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT t.* FROM task_executions t
                LEFT JOIN review_triggers r ON r.task_id = t.id
                WHERE t.status = 'ready-for-review'
                  AND (r.last_review_trigger_at IS NULL OR r.last_review_trigger_at < ?)
                ORDER BY t.id ASC
                """,
                (cutoff,),
            ).fetchall()
            return [self._row_to_task(r) for r in rows]

    def get_lease(self) -> LeaseRecord:
        with self._read_conn() as conn:
            return self._lease_from_row(conn.execute("SELECT * FROM worker_lock WHERE id = 1").fetchone())

    @staticmethod
    def _lease_from_row(row: sqlite3.Row | None) -> LeaseRecord:
        if not row:
            return LeaseRecord(False, None, None, None, None, None, None)
        return LeaseRecord(
            locked=bool(row["locked"]),
            owner=row["owner"],
            issue_number=row["issue_number"],
            task_id=row["task_id"],
            acquired_at=row["acquired_at"],
            heartbeat_at=row["heartbeat_at"],
            lease_expires_at=row["lease_expires_at"],
        )

    @staticmethod
    def _lease_is_valid(lease: LeaseRecord, *, now_iso: str) -> bool:
        if not lease.locked:
            return False
        if lease.lease_expires_at and lease.lease_expires_at < now_iso:
            return False
        return True

    def peek_worker_locked(self) -> bool:
        """Read-only lease check — no stale recovery side effects."""
        now_iso = datetime.now(UTC).isoformat()
        return self._lease_is_valid(self.get_lease(), now_iso=now_iso)

    def peek_reviewer_locked(self) -> bool:
        """Read-only reviewer lock check — no stale recovery side effects."""
        now_iso = datetime.now(UTC).isoformat()
        with self._read_conn() as conn:
            row = conn.execute("SELECT * FROM reviewer_lock WHERE id = 1").fetchone()
            if not row or not row["locked"]:
                return False
            expires = row["lease_expires_at"]
            return not (expires and expires < now_iso)

    def load_dashboard_snapshot(
        self,
        *,
        activity_limit: int = 15,
        completed_limit: int = 5,
    ) -> dict[str, Any]:
        """Single short read transaction for dashboard observability."""
        with self._read_conn() as conn:
            lease = self._lease_from_row(
                conn.execute("SELECT * FROM worker_lock WHERE id = 1").fetchone()
            )
            reviewer_row = conn.execute("SELECT * FROM reviewer_lock WHERE id = 1").fetchone()
            now_iso = datetime.now(UTC).isoformat()
            reviewer_locked = bool(
                reviewer_row
                and reviewer_row["locked"]
                and not (
                    reviewer_row["lease_expires_at"]
                    and reviewer_row["lease_expires_at"] < now_iso
                )
            )

            primary_row = conn.execute(
                """
                SELECT * FROM task_executions
                WHERE status IN (
                    'queued', 'running', 'ready-for-review', 'product-decision',
                    'needs-fix'
                )
                ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
            primary_task = self._row_to_task(primary_row) if primary_row else None

            current_row = conn.execute(
                """
                SELECT * FROM task_executions
                WHERE status IN ('queued', 'running', 'ready-for-review')
                ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
            current_task = self._row_to_task(current_row) if current_row else None

            failed_row = conn.execute(
                """
                SELECT * FROM task_executions
                WHERE status = 'failed'
                ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
            recent_failed = self._row_to_task(failed_row) if failed_row else None

            focus_task = current_task or primary_task
            review_task_id = focus_task.id if focus_task else None
            active_review = None
            if review_task_id is not None:
                review_row = conn.execute(
                    """
                    SELECT * FROM review_invocations
                    WHERE task_id = ?
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (review_task_id,),
                ).fetchone()
                if review_row:
                    active_review = self._row_to_review_invocation(review_row)

            delivery_rows = conn.execute(
                """
                SELECT delivery_id, event_type, action, status, received_at, error
                FROM webhook_deliveries
                ORDER BY received_at DESC LIMIT ?
                """,
                (activity_limit,),
            ).fetchall()
            task_rows = conn.execute(
                """
                SELECT id, issue_number, status, updated_at, error
                FROM task_executions
                ORDER BY updated_at DESC LIMIT ?
                """,
                (activity_limit,),
            ).fetchall()
            completed_rows = conn.execute(
                """
                SELECT te.*, ri.verdict AS review_verdict
                FROM task_executions te
                LEFT JOIN review_invocations ri ON ri.task_id = te.id
                WHERE te.status = 'completed'
                ORDER BY te.updated_at DESC
                LIMIT ?
                """,
                (completed_limit,),
            ).fetchall()

        active_handoff = self.get_latest_active_handoff()

        return {
            "lease": lease,
            "worker_locked": self._lease_is_valid(lease, now_iso=now_iso),
            "reviewer_locked": reviewer_locked,
            "primary_task": primary_task,
            "current_task": current_task,
            "recent_failed": recent_failed,
            "active_review": active_review,
            "active_handoff": active_handoff,
            "delivery_rows": [dict(r) for r in delivery_rows],
            "task_rows": [dict(r) for r in task_rows],
            "completed_rows": [dict(r) for r in completed_rows],
        }

    def append_execution_event(
        self,
        *,
        task_id: int | None,
        issue_number: int | None,
        generation: str | None,
        event_type: str,
        phase: str | None = None,
        action: str | None = None,
        file_path: str | None = None,
        command_summary: str | None = None,
        result_summary: str | None = None,
        status: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ExecutionEventRecord:
        now = datetime.now(UTC).isoformat()
        meta_json = json.dumps(metadata or {}, ensure_ascii=False)
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                INSERT INTO execution_events
                (task_id, issue_number, generation, event_type, phase, action,
                 file_path, command_summary, result_summary, status, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    issue_number,
                    generation,
                    event_type,
                    phase,
                    action,
                    file_path,
                    command_summary,
                    result_summary,
                    status,
                    meta_json,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM execution_events WHERE id = last_insert_rowid()"
            ).fetchone()
            assert row is not None
            if task_id:
                lock_row = conn.execute(
                    "SELECT owner FROM worker_lock WHERE id = 1 AND locked = 1 AND task_id = ?",
                    (task_id,),
                ).fetchone()
                if lock_row and lock_row["owner"]:
                    expires = (datetime.now(UTC) + timedelta(seconds=300)).isoformat()
                    conn.execute(
                        """
                        UPDATE worker_lock
                        SET heartbeat_at = ?, lease_expires_at = ?
                        WHERE id = 1 AND owner = ?
                        """,
                        (now, expires, lock_row["owner"]),
                    )
            return self._row_to_execution_event(row)

    def list_execution_events(
        self,
        *,
        task_id: int | None = None,
        limit: int = 50,
    ) -> list[ExecutionEventRecord]:
        with self._read_conn() as conn:
            if task_id is not None:
                rows = conn.execute(
                    """
                    SELECT * FROM execution_events
                    WHERE task_id = ?
                    ORDER BY id DESC LIMIT ?
                    """,
                    (task_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM execution_events
                    ORDER BY id DESC LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            return [self._row_to_execution_event(r) for r in rows]

    @staticmethod
    def _row_to_execution_event(row: sqlite3.Row) -> ExecutionEventRecord:
        meta_raw = row["metadata_json"]
        metadata = None
        if meta_raw:
            try:
                metadata = json.loads(meta_raw)
            except json.JSONDecodeError:
                metadata = None
        return ExecutionEventRecord(
            id=row["id"],
            task_id=row["task_id"],
            issue_number=row["issue_number"],
            generation=row["generation"],
            event_type=row["event_type"],
            phase=row["phase"],
            action=row["action"],
            file_path=row["file_path"],
            command_summary=row["command_summary"],
            result_summary=row["result_summary"],
            status=row["status"],
            metadata=metadata,
            created_at=row["created_at"],
        )

    def recover_stale_lease(
        self,
        *,
        heartbeat_ttl_seconds: int = 300,
        progress_grace_seconds: int = 900,
    ) -> bool:
        now_dt = datetime.now(UTC)
        now = now_dt.isoformat()
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM worker_lock WHERE id = 1").fetchone()
            if not row or not row["locked"]:
                return False
            expires = row["lease_expires_at"]
            heartbeat = row["heartbeat_at"]
            task_id = row["task_id"]
            owner = row["owner"]
            lease_expired = bool(expires and expires < now)
            heartbeat_stale = False
            if heartbeat:
                try:
                    hb_dt = datetime.fromisoformat(heartbeat.replace("Z", "+00:00"))
                    if hb_dt.tzinfo is None:
                        hb_dt = hb_dt.replace(tzinfo=UTC)
                    heartbeat_stale = (now_dt - hb_dt).total_seconds() > heartbeat_ttl_seconds
                except ValueError:
                    heartbeat_stale = True
            else:
                heartbeat_stale = True
            if not lease_expired and not heartbeat_stale:
                return False

            if task_id:
                last_progress = self.get_task_last_progress_at(task_id, conn=conn)
                if last_progress is not None:
                    progress_age = (now_dt - last_progress).total_seconds()
                    if progress_age <= progress_grace_seconds:
                        new_expires = (
                            now_dt + timedelta(seconds=heartbeat_ttl_seconds)
                        ).isoformat()
                        conn.execute(
                            """
                            UPDATE worker_lock
                            SET heartbeat_at = ?, lease_expires_at = ?
                            WHERE id = 1 AND locked = 1
                            """,
                            (now, new_expires),
                        )
                        logger.info(
                            "extended lease for active task=%s progress_age=%.0fs",
                            task_id,
                            progress_age,
                        )
                        return False

            if task_id:
                conn.execute(
                    """
                    UPDATE task_executions
                    SET status = ?, error = ?, updated_at = ?
                    WHERE id = ? AND status IN ('queued', 'running')
                    """,
                    (
                        TaskStatus.FAILED.value,
                        "stale worker lease recovered",
                        now,
                        task_id,
                    ),
                )
            conn.execute(
                """
                UPDATE worker_lock
                SET locked = 0, issue_number = NULL, task_id = NULL, locked_at = NULL,
                    owner = NULL, acquired_at = NULL, heartbeat_at = NULL, lease_expires_at = NULL
                WHERE id = 1
                """
            )
            if owner:
                logger.warning("recovered stale lease owner=%s task=%s", owner, task_id)
            return True

    def get_task_last_progress_at(
        self,
        task_id: int,
        *,
        conn: sqlite3.Connection | None = None,
    ) -> datetime | None:
        query = """
            SELECT created_at FROM execution_events
            WHERE task_id = ? AND event_type IN ({})
            ORDER BY id DESC LIMIT 1
        """.format(",".join("?" * len(_PROGRESS_EVENT_TYPES)))
        params: tuple[int | str, ...] = (task_id, *_PROGRESS_EVENT_TYPES)
        if conn is not None:
            row = conn.execute(query, params).fetchone()
        else:
            with self._read_conn() as read_conn:
                row = read_conn.execute(query, params).fetchone()
        if not row or not row["created_at"]:
            return None
        try:
            dt = datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt
        except ValueError:
            return None

    def list_tasks_for_push_reconcile(
        self,
        *,
        statuses: tuple[TaskStatus, ...],
        limit: int = 20,
    ) -> list[TaskRecord]:
        placeholders = ",".join("?" * len(statuses))
        with self._read_conn() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM task_executions
                WHERE status IN ({placeholders})
                  AND (commit_sha IS NULL OR commit_sha = '')
                ORDER BY updated_at DESC LIMIT ?
                """,
                (*[s.value for s in statuses], limit),
            ).fetchall()
            return [self._row_to_task(r) for r in rows]

    def list_tasks_by_status(self, *statuses: TaskStatus, limit: int = 50) -> list[TaskRecord]:
        placeholders = ",".join("?" * len(statuses))
        with self._read_conn() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM task_executions
                WHERE status IN ({placeholders})
                ORDER BY id DESC LIMIT ?
                """,
                (*[s.value for s in statuses], limit),
            ).fetchall()
            return [self._row_to_task(r) for r in rows]

    def try_acquire_lease(
        self,
        issue_number: int,
        task_id: int,
        *,
        owner: str,
        ttl_seconds: int = 300,
    ) -> bool:
        self.recover_stale_lease(progress_grace_seconds=900)
        now = datetime.now(UTC)
        now_iso = now.isoformat()
        expires = (now + timedelta(seconds=ttl_seconds)).isoformat()
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                UPDATE worker_lock
                SET locked = 1, issue_number = ?, task_id = ?, locked_at = ?,
                    owner = ?, acquired_at = ?, heartbeat_at = ?, lease_expires_at = ?
                WHERE id = 1 AND locked = 0
                """,
                (issue_number, task_id, now_iso, owner, now_iso, now_iso, expires),
            )
            return conn.total_changes > 0

    def heartbeat_lease(self, owner: str, *, ttl_seconds: int = 300) -> bool:
        now = datetime.now(UTC)
        now_iso = now.isoformat()
        expires = (now + timedelta(seconds=ttl_seconds)).isoformat()
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                UPDATE worker_lock
                SET heartbeat_at = ?, lease_expires_at = ?
                WHERE id = 1 AND locked = 1 AND owner = ?
                """,
                (now_iso, expires, owner),
            )
            return conn.total_changes > 0

    def release_lease(self, owner: str | None = None) -> None:
        with self._lock, self._conn() as conn:
            if owner:
                conn.execute(
                    """
                    UPDATE worker_lock
                    SET locked = 0, issue_number = NULL, task_id = NULL, locked_at = NULL,
                        owner = NULL, acquired_at = NULL, heartbeat_at = NULL, lease_expires_at = NULL
                    WHERE id = 1 AND owner = ?
                    """,
                    (owner,),
                )
            else:
                conn.execute(
                    """
                    UPDATE worker_lock
                    SET locked = 0, issue_number = NULL, task_id = NULL, locked_at = NULL,
                        owner = NULL, acquired_at = NULL, heartbeat_at = NULL, lease_expires_at = NULL
                    WHERE id = 1
                    """
                )

    def is_locked(self) -> bool:
        lease = self.get_lease()
        if not lease.locked:
            return False
        if lease.lease_expires_at:
            if lease.lease_expires_at < datetime.now(UTC).isoformat():
                self.recover_stale_lease(progress_grace_seconds=900)
                return False
        return True

    def try_acquire_lock(self, issue_number: int, task_id: int) -> bool:
        return self.try_acquire_lease(
            issue_number, task_id, owner=f"legacy-{task_id}", ttl_seconds=300
        )

    def release_lock(self) -> None:
        self.release_lease()

    def create_review_invocation(
        self,
        *,
        invocation_id: str,
        task_id: int,
        issue_number: int,
        commit_sha: str,
    ) -> ReviewInvocationRecord | None:
        """Insert pending invocation; returns None if (task_id, commit_sha) already exists."""
        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn() as conn:
            existing = conn.execute(
                """
                SELECT * FROM review_invocations
                WHERE task_id = ? AND commit_sha = ?
                """,
                (task_id, commit_sha),
            ).fetchone()
            if existing:
                return self._row_to_review_invocation(existing)
            conn.execute(
                """
                INSERT INTO review_invocations
                (invocation_id, task_id, issue_number, commit_sha, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    invocation_id,
                    task_id,
                    issue_number,
                    commit_sha,
                    ReviewInvocationStatus.PENDING.value,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM review_invocations WHERE invocation_id = ?",
                (invocation_id,),
            ).fetchone()
            assert row is not None
            return self._row_to_review_invocation(row)

    def get_review_invocation(self, task_id: int, commit_sha: str) -> ReviewInvocationRecord | None:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT * FROM review_invocations
                WHERE task_id = ? AND commit_sha = ?
                """,
                (task_id, commit_sha),
            ).fetchone()
            return self._row_to_review_invocation(row) if row else None

    def update_review_invocation(
        self,
        invocation_id: str,
        *,
        status: ReviewInvocationStatus | None = None,
        verdict: ReviewVerdict | None = None,
        error: str | None = None,
        attempt_count: int | None = None,
        next_retry_at: str | None = None,
        started_at: str | None = None,
        clear_next_retry: bool = False,
        clear_started_at: bool = False,
    ) -> ReviewInvocationRecord:
        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM review_invocations WHERE invocation_id = ?",
                (invocation_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"review invocation {invocation_id} not found")
            new_status = status.value if status else row["status"]
            new_verdict = verdict.value if verdict else row["verdict"]
            new_error = error if error is not None else row["error"]
            new_attempt = attempt_count if attempt_count is not None else row["attempt_count"]
            if clear_next_retry:
                new_next_retry = None
            elif next_retry_at is not None:
                new_next_retry = next_retry_at
            else:
                new_next_retry = row["next_retry_at"]
            if clear_started_at:
                new_started_at = None
            elif started_at is not None:
                new_started_at = started_at
            else:
                new_started_at = row["started_at"]
            completed_at = now if status in {
                ReviewInvocationStatus.COMPLETED,
                ReviewInvocationStatus.FAILED,
            } else row["completed_at"]
            if status == ReviewInvocationStatus.PENDING:
                completed_at = None
            conn.execute(
                """
                UPDATE review_invocations
                SET status = ?, verdict = ?, error = ?, completed_at = ?,
                    attempt_count = ?, next_retry_at = ?, started_at = ?
                WHERE invocation_id = ?
                """,
                (
                    new_status,
                    new_verdict,
                    new_error,
                    completed_at,
                    new_attempt,
                    new_next_retry,
                    new_started_at,
                    invocation_id,
                ),
            )
            updated = conn.execute(
                "SELECT * FROM review_invocations WHERE invocation_id = ?",
                (invocation_id,),
            ).fetchone()
            assert updated is not None
            return self._row_to_review_invocation(updated)

    def reset_review_for_retry(self, invocation_id: str) -> ReviewInvocationRecord:
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                UPDATE review_invocations
                SET status = ?, error = NULL, completed_at = NULL,
                    next_retry_at = NULL, started_at = NULL
                WHERE invocation_id = ?
                """,
                (ReviewInvocationStatus.PENDING.value, invocation_id),
            )
            row = conn.execute(
                "SELECT * FROM review_invocations WHERE invocation_id = ?",
                (invocation_id,),
            ).fetchone()
            assert row is not None
            return self._row_to_review_invocation(row)

    def is_review_retryable(
        self,
        record: ReviewInvocationRecord,
        *,
        max_attempts: int,
    ) -> bool:
        if record.status != ReviewInvocationStatus.FAILED:
            return False
        if record.attempt_count >= max_attempts:
            return False
        if record.next_retry_at is None:
            return True
        return record.next_retry_at <= datetime.now(UTC).isoformat()

    def recover_stale_running_reviews(self, *, older_than_seconds: int) -> list[str]:
        cutoff = (datetime.now(UTC) - timedelta(seconds=older_than_seconds)).isoformat()
        recovered: list[str] = []
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                """
                SELECT invocation_id FROM review_invocations
                WHERE status = ?
                  AND started_at IS NOT NULL
                  AND started_at < ?
                """,
                (ReviewInvocationStatus.RUNNING.value, cutoff),
            ).fetchall()
            for row in rows:
                conn.execute(
                    """
                    UPDATE review_invocations
                    SET status = ?, started_at = NULL
                    WHERE invocation_id = ?
                    """,
                    (ReviewInvocationStatus.PENDING.value, row["invocation_id"]),
                )
                recovered.append(row["invocation_id"])
        return recovered

    def finalize_obsolete_review_invocations(self) -> list[str]:
        """Complete invocations whose task left ready-for-review or are acceptance fakes."""
        finalized: list[str] = []
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                """
                SELECT ri.invocation_id, ri.status, te.status AS task_status
                FROM review_invocations ri
                JOIN task_executions te ON te.id = ri.task_id
                WHERE ri.status IN (?, ?, ?)
                  AND (
                    te.status != ?
                    OR te.issue_number >= 10000
                  )
                """,
                (
                    ReviewInvocationStatus.PENDING.value,
                    ReviewInvocationStatus.RUNNING.value,
                    ReviewInvocationStatus.FAILED.value,
                    TaskStatus.READY_FOR_REVIEW.value,
                ),
            ).fetchall()
            verdict_by_task = {
                TaskStatus.COMPLETED.value: ReviewVerdict.PASS.value,
                TaskStatus.NEEDS_FIX.value: ReviewVerdict.FAIL.value,
                TaskStatus.PRODUCT_DECISION.value: ReviewVerdict.PRODUCT_DECISION.value,
            }
            for row in rows:
                verdict = verdict_by_task.get(row["task_status"], ReviewVerdict.SKIP.value)
                conn.execute(
                    """
                    UPDATE review_invocations
                    SET status = ?, verdict = ?, started_at = NULL, next_retry_at = NULL
                    WHERE invocation_id = ?
                    """,
                    (
                        ReviewInvocationStatus.COMPLETED.value,
                        verdict,
                        row["invocation_id"],
                    ),
                )
                finalized.append(row["invocation_id"])
        return finalized

    def get_due_review_invocations(self, *, max_attempts: int) -> list[ReviewInvocationRecord]:
        now = datetime.now(UTC).isoformat()
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT ri.* FROM review_invocations ri
                JOIN task_executions te ON te.id = ri.task_id
                WHERE te.status = ?
                  AND te.issue_number < 10000
                  AND (
                    ri.status = ?
                    OR (
                      ri.status = ?
                      AND ri.attempt_count < ?
                      AND (ri.next_retry_at IS NULL OR ri.next_retry_at <= ?)
                    )
                  )
                ORDER BY te.updated_at DESC, ri.created_at ASC
                """,
                (
                    TaskStatus.READY_FOR_REVIEW.value,
                    ReviewInvocationStatus.PENDING.value,
                    ReviewInvocationStatus.FAILED.value,
                    max_attempts,
                    now,
                ),
            ).fetchall()
            return [self._row_to_review_invocation(r) for r in rows]

    def recover_stale_reviewer_lock(self) -> bool:
        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM reviewer_lock WHERE id = 1").fetchone()
            if not row or not row["locked"]:
                return False
            expires = row["lease_expires_at"]
            if expires and expires >= now:
                return False
            conn.execute(
                """
                UPDATE reviewer_lock
                SET locked = 0, task_id = NULL, owner = NULL,
                    acquired_at = NULL, lease_expires_at = NULL
                WHERE id = 1
                """
            )
            return conn.total_changes > 0

    def recover_orphaned_reviewer_lock(self, *, stall_seconds: int = 45) -> bool:
        """Release reviewer lock held without an active RUNNING invocation."""
        cutoff = (datetime.now(UTC) - timedelta(seconds=stall_seconds)).isoformat()
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM reviewer_lock WHERE id = 1").fetchone()
            if not row or not row["locked"]:
                return False
            acquired = row["acquired_at"]
            if not acquired or acquired > cutoff:
                return False
            task_id = row["task_id"]
            if task_id is not None:
                running = conn.execute(
                    """
                    SELECT 1 FROM review_invocations
                    WHERE task_id = ? AND status = ?
                    """,
                    (task_id, ReviewInvocationStatus.RUNNING.value),
                ).fetchone()
                if running:
                    return False
            conn.execute(
                """
                UPDATE reviewer_lock
                SET locked = 0, task_id = NULL, owner = NULL,
                    acquired_at = NULL, lease_expires_at = NULL
                WHERE id = 1
                """
            )
            return conn.total_changes > 0

    def try_acquire_reviewer_lock(
        self,
        task_id: int,
        *,
        owner: str,
        ttl_seconds: int = 300,
    ) -> bool:
        self.recover_stale_reviewer_lock()
        self.recover_orphaned_reviewer_lock()
        now = datetime.now(UTC)
        now_iso = now.isoformat()
        expires = (now + timedelta(seconds=ttl_seconds)).isoformat()
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                UPDATE reviewer_lock
                SET locked = 1, task_id = ?, owner = ?, acquired_at = ?, lease_expires_at = ?
                WHERE id = 1 AND locked = 0
                """,
                (task_id, owner, now_iso, expires),
            )
            return conn.total_changes > 0

    def release_reviewer_lock(self, owner: str | None = None) -> None:
        with self._lock, self._conn() as conn:
            if owner:
                conn.execute(
                    """
                    UPDATE reviewer_lock
                    SET locked = 0, task_id = NULL, owner = NULL,
                        acquired_at = NULL, lease_expires_at = NULL
                    WHERE id = 1 AND owner = ?
                    """,
                    (owner,),
                )
            else:
                conn.execute(
                    """
                    UPDATE reviewer_lock
                    SET locked = 0, task_id = NULL, owner = NULL,
                        acquired_at = NULL, lease_expires_at = NULL
                    WHERE id = 1
                    """
                )

    def is_reviewer_locked(self) -> bool:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM reviewer_lock WHERE id = 1").fetchone()
            if not row or not row["locked"]:
                return False
            expires = row["lease_expires_at"]
            if expires and expires < datetime.now(UTC).isoformat():
                self.recover_stale_reviewer_lock()
                return False
            return True

    def create_handoff(
        self,
        *,
        idempotency_key: str,
        source_task_id: int,
        source_issue_number: int,
        commit_sha: str,
        status: HandoffStatus,
        next_issue_number: int | None = None,
        reason: str | None = None,
    ) -> HandoffRecord:
        now = datetime.now(UTC).isoformat()
        handoff_id = str(uuid.uuid4())
        with self._lock, self._conn() as conn:
            existing = conn.execute(
                "SELECT * FROM task_handoffs WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing:
                return self._row_to_handoff(existing)
            conn.execute(
                """
                INSERT INTO task_handoffs
                (handoff_id, idempotency_key, source_task_id, source_issue_number,
                 commit_sha, status, next_issue_number, reason, created_at, retry_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    handoff_id,
                    idempotency_key,
                    source_task_id,
                    source_issue_number,
                    commit_sha,
                    status.value,
                    next_issue_number,
                    reason,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM task_handoffs WHERE handoff_id = ?",
                (handoff_id,),
            ).fetchone()
            assert row is not None
            return self._row_to_handoff(row)

    def get_handoff(self, handoff_id: str) -> HandoffRecord | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM task_handoffs WHERE handoff_id = ?",
                (handoff_id,),
            ).fetchone()
            return self._row_to_handoff(row) if row else None

    def get_handoff_by_key(self, idempotency_key: str) -> HandoffRecord | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM task_handoffs WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            return self._row_to_handoff(row) if row else None

    def update_handoff(
        self,
        handoff_id: str,
        *,
        status: HandoffStatus | None = None,
        next_issue_number: int | None = None,
        activated_at: str | None = None,
        worker_started_at: str | None = None,
        retry_count: int | None = None,
        reason: str | None = None,
    ) -> HandoffRecord:
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM task_handoffs WHERE handoff_id = ?",
                (handoff_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"handoff {handoff_id} not found")
            new_status = status.value if status else row["status"]
            new_next = next_issue_number if next_issue_number is not None else row["next_issue_number"]
            new_activated = activated_at if activated_at is not None else row["activated_at"]
            new_worker = worker_started_at if worker_started_at is not None else row["worker_started_at"]
            new_retry = retry_count if retry_count is not None else row["retry_count"]
            new_reason = reason if reason is not None else row["reason"]
            conn.execute(
                """
                UPDATE task_handoffs
                SET status = ?, next_issue_number = ?, activated_at = ?,
                    worker_started_at = ?, retry_count = ?, reason = ?
                WHERE handoff_id = ?
                """,
                (
                    new_status,
                    new_next,
                    new_activated,
                    new_worker,
                    new_retry,
                    new_reason,
                    handoff_id,
                ),
            )
            updated = conn.execute(
                "SELECT * FROM task_handoffs WHERE handoff_id = ?",
                (handoff_id,),
            ).fetchone()
            assert updated is not None
            return self._row_to_handoff(updated)

    def list_active_handoffs(self) -> list[HandoffRecord]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM task_handoffs
                WHERE status IN ('pending', 'activated', 'stalled', 'worker_started')
                ORDER BY created_at DESC
                """
            ).fetchall()
            return [self._row_to_handoff(r) for r in rows]

    def get_latest_active_handoff(self) -> HandoffRecord | None:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT * FROM task_handoffs
                WHERE status IN ('pending', 'activated', 'stalled', 'worker_started', 'waiting_product')
                ORDER BY created_at DESC LIMIT 1
                """
            ).fetchone()
            return self._row_to_handoff(row) if row else None

    @staticmethod
    def _row_to_worker_reactivation(row: sqlite3.Row) -> WorkerReactivationRecord:
        return WorkerReactivationRecord(
            issue_number=row["issue_number"],
            task_id=row["task_id"],
            attempt_count=int(row["attempt_count"]),
            next_retry_at=row["next_retry_at"],
            last_error=row["last_error"],
            status=WorkerReactivationStatus(row["status"]),
            last_kick_at=row["last_kick_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_execution_failure(row: sqlite3.Row) -> ExecutionFailureRecord:
        return ExecutionFailureRecord(
            execution_key=row["execution_key"],
            task_id=int(row["task_id"]),
            issue_number=int(row["issue_number"]),
            stage=row["stage"],
            error_class=row["error_class"],
            error_message=row["error_message"],
            retry_count=int(row["retry_count"]),
            next_action=row["next_action"],
            comment_posted_at=row["comment_posted_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_reviewer_reactivation(row: sqlite3.Row) -> ReviewerReactivationRecord:
        return ReviewerReactivationRecord(
            issue_number=row["issue_number"],
            task_id=row["task_id"],
            attempt_count=int(row["attempt_count"]),
            next_retry_at=row["next_retry_at"],
            last_error=row["last_error"],
            status=ReviewerReactivationStatus(row["status"]),
            last_kick_at=row["last_kick_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_handoff(row: sqlite3.Row) -> HandoffRecord:
        return HandoffRecord(
            handoff_id=row["handoff_id"],
            idempotency_key=row["idempotency_key"],
            source_task_id=row["source_task_id"],
            source_issue_number=row["source_issue_number"],
            commit_sha=row["commit_sha"],
            status=HandoffStatus(row["status"]),
            next_issue_number=row["next_issue_number"],
            reason=row["reason"],
            created_at=row["created_at"],
            activated_at=row["activated_at"],
            worker_started_at=row["worker_started_at"],
            retry_count=int(row["retry_count"]),
        )

    @staticmethod
    def _row_to_review_invocation(row: sqlite3.Row) -> ReviewInvocationRecord:
        keys = row.keys()
        verdict_raw = row["verdict"]
        return ReviewInvocationRecord(
            invocation_id=row["invocation_id"],
            task_id=row["task_id"],
            issue_number=row["issue_number"],
            commit_sha=row["commit_sha"],
            verdict=ReviewVerdict(verdict_raw) if verdict_raw else None,
            status=ReviewInvocationStatus(row["status"]),
            error=row["error"],
            created_at=row["created_at"],
            completed_at=row["completed_at"],
            attempt_count=int(row["attempt_count"]) if "attempt_count" in keys else 0,
            next_retry_at=row["next_retry_at"] if "next_retry_at" in keys else None,
            started_at=row["started_at"] if "started_at" in keys else None,
        )

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> TaskRecord:
        keys = row.keys()
        return TaskRecord(
            id=row["id"],
            issue_number=row["issue_number"],
            status=TaskStatus(row["status"]),
            delivery_id=row["delivery_id"],
            commit_sha=row["commit_sha"],
            error=row["error"],
            execution_key=row["execution_key"] if "execution_key" in keys else None,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
