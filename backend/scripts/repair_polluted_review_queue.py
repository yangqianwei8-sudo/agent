#!/usr/bin/env python3
"""Revert bogus orphan-push reconciliations and unblock reviewer for real tasks."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from autonomous_dev.config import get_autonomous_settings  # noqa: E402
from autonomous_dev.state import StateStore, TaskStatus  # noqa: E402

# Legitimate ready-for-review rows to preserve (issue_number, commit_sha prefix).
KEEP_READY = {
    (24, "ec457517"),
}


def main() -> int:
    settings = get_autonomous_settings()
    store = StateStore(settings.state_db_path)
    reverted = 0
    finalized = 0

    with store._lock, store._conn() as conn:  # noqa: SLF001
        rows = conn.execute(
            """
            SELECT id, issue_number, status, commit_sha
            FROM task_executions
            WHERE status = ?
            """,
            (TaskStatus.READY_FOR_REVIEW.value,),
        ).fetchall()
        for row in rows:
            sha = (row["commit_sha"] or "")[:8]
            keep = any(
                row["issue_number"] == issue and sha.startswith(prefix)
                for issue, prefix in KEEP_READY
            )
            if keep:
                continue
            conn.execute(
                """
                UPDATE task_executions
                SET status = ?, error = ?, updated_at = datetime('now')
                WHERE id = ?
                """,
                (
                    TaskStatus.FAILED.value,
                    "bogus orphan reconcile reverted",
                    row["id"],
                ),
            )
            reverted += 1

        conn.execute(
            """
            UPDATE review_invocations
            SET status = 'completed', verdict = 'skip', completed_at = datetime('now')
            WHERE invocation_id IN (
              SELECT ri.invocation_id
              FROM review_invocations ri
              JOIN task_executions te ON te.id = ri.task_id
              WHERE ri.status IN ('pending', 'running')
                AND te.status != 'ready-for-review'
            )
            """
        )
        finalized += conn.total_changes

        conn.execute(
            """
            UPDATE reviewer_lock
            SET locked = 0, task_id = NULL, owner = NULL,
                acquired_at = NULL, lease_expires_at = NULL
            WHERE id = 1
            """
        )

    store.finalize_obsolete_review_invocations()
    print(f"reverted_tasks={reverted} finalized_invocations={finalized}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
