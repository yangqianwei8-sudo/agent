#!/usr/bin/env python3
"""Worker controlled acceptance — cursor_sdk branch without legal business execution."""

from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from autonomous_dev.config import get_autonomous_settings  # noqa: E402
from autonomous_dev.state import StateStore, TaskStatus  # noqa: E402
from autonomous_dev.task_router import TaskRouter  # noqa: E402
from autonomous_dev.worker import CURSOR_RUNTIME_ACCEPTANCE_MARKER, CURSOR_RUNTIME_OK_MARKER  # noqa: E402

VENV_PYTHON = ROOT / ".venv" / "bin" / "python"


def main() -> int:
    if not VENV_PYTHON.exists():
        print(f"FAIL: venv python missing at {VENV_PYTHON}")
        return 1

    settings = get_autonomous_settings()
    if settings.autonomous_worker_mode != "cursor_sdk":
        print(f"FAIL: expected AUTONOMOUS_WORKER_MODE=cursor_sdk, got {settings.autonomous_worker_mode}")
        return 1

    settings.validate_cursor_sdk_config()
    print(f"worker_mode: {settings.autonomous_worker_mode}")
    print(f"cursor_model: {settings.cursor_model}")

    store = StateStore(settings.state_db_path)
    router = TaskRouter(settings, store)
    issue_num = 2
    delivery_id = f"controlled-worker-{uuid.uuid4()}"
    issue_body = (
        f"{CURSOR_RUNTIME_ACCEPTANCE_MARKER}\n"
        "P0 worker controlled acceptance — do not execute legal business tasks."
    )

    before = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()

    task = store.create_task(issue_number=issue_num, delivery_id=delivery_id)
    if not store.try_acquire_lock(issue_num, task.id):
        print("FAIL: could not acquire worker lock")
        return 1

    result = router.run_worker_sync(task, issue_body=issue_body)
    store.release_lock()

    after = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()

    if not before and after:
        print("FAIL: worker controlled acceptance modified working tree")
        return 1

    updated = store.get_task(task.id)
    checks = {
        "worker_status_running": result.status == TaskStatus.RUNNING,
        "commit_sha_marker": result.commit_sha == CURSOR_RUNTIME_OK_MARKER,
        "store_status_running": updated is not None and updated.status == TaskStatus.RUNNING,
        "issue_context_read": issue_num == task.issue_number,
    }

    print("=== Worker Controlled Acceptance ===")
    for name, ok in checks.items():
        print(f"{name}: {'PASS' if ok else 'FAIL'}")

    if all(checks.values()):
        print("Final: PASS")
        return 0
    print(f"error: {result.error}")
    print("Final: FAIL")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
