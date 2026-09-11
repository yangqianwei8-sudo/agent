"""Persistent delivery / task / lease lock / execution identity state (SQLite)."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any


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


class StateStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
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
                INSERT OR IGNORE INTO worker_lock (id, locked) VALUES (1, 0);
                """
            )
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
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM worker_lock WHERE id = 1").fetchone()
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

    def recover_stale_lease(self) -> bool:
        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM worker_lock WHERE id = 1").fetchone()
            if not row or not row["locked"]:
                return False
            expires = row["lease_expires_at"]
            if expires and expires >= now:
                return False
            conn.execute(
                """
                UPDATE worker_lock
                SET locked = 0, issue_number = NULL, task_id = NULL, locked_at = NULL,
                    owner = NULL, acquired_at = NULL, heartbeat_at = NULL, lease_expires_at = NULL
                WHERE id = 1
                """
            )
            return conn.total_changes > 0

    def try_acquire_lease(
        self,
        issue_number: int,
        task_id: int,
        *,
        owner: str,
        ttl_seconds: int = 300,
    ) -> bool:
        self.recover_stale_lease()
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
                self.recover_stale_lease()
                return False
        return True

    def try_acquire_lock(self, issue_number: int, task_id: int) -> bool:
        return self.try_acquire_lease(
            issue_number, task_id, owner=f"legacy-{task_id}", ttl_seconds=300
        )

    def release_lock(self) -> None:
        self.release_lease()

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
