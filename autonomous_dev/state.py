"""Persistent delivery / task / lock state (SQLite)."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
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
    created_at: str
    updated_at: str


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
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(issue_number, delivery_id)
                );
                CREATE TABLE IF NOT EXISTS worker_lock (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    locked INTEGER NOT NULL DEFAULT 0,
                    issue_number INTEGER,
                    task_id INTEGER,
                    locked_at TEXT
                );
                INSERT OR IGNORE INTO worker_lock (id, locked) VALUES (1, 0);
                """
            )
            cols = {
                row[1]
                for row in conn.execute("PRAGMA table_info(webhook_deliveries)").fetchall()
            }
            if "action" not in cols:
                conn.execute("ALTER TABLE webhook_deliveries ADD COLUMN action TEXT")

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
        """Returns False if delivery_id already exists (idempotent skip)."""
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
        status: TaskStatus = TaskStatus.QUEUED,
    ) -> TaskRecord:
        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO task_executions (issue_number, status, delivery_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(issue_number, delivery_id) DO NOTHING
                """,
                (issue_number, status.value, delivery_id, now, now),
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
            conn.execute(
                """
                UPDATE task_executions
                SET status = ?, commit_sha = ?, error = ?, updated_at = ?
                WHERE id = ?
                """,
                (new_status, new_commit, new_error, now, task_id),
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

    def try_acquire_lock(self, issue_number: int, task_id: int) -> bool:
        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT locked FROM worker_lock WHERE id = 1").fetchone()
            if row and row["locked"]:
                return False
            conn.execute(
                """
                UPDATE worker_lock SET locked = 1, issue_number = ?, task_id = ?, locked_at = ?
                WHERE id = 1 AND locked = 0
                """,
                (issue_number, task_id, now),
            )
            return conn.total_changes > 0

    def release_lock(self) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "UPDATE worker_lock SET locked = 0, issue_number = NULL, task_id = NULL, locked_at = NULL WHERE id = 1"
            )

    def is_locked(self) -> bool:
        with self._conn() as conn:
            row = conn.execute("SELECT locked FROM worker_lock WHERE id = 1").fetchone()
            return bool(row and row["locked"])

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> TaskRecord:
        return TaskRecord(
            id=row["id"],
            issue_number=row["issue_number"],
            status=TaskStatus(row["status"]),
            delivery_id=row["delivery_id"],
            commit_sha=row["commit_sha"],
            error=row["error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
