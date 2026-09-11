#!/usr/bin/env python3
"""Live Cursor Agent runtime acceptance — uses settings.cursor_model (no hardcoded model)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from autonomous_dev.config import get_autonomous_settings  # noqa: E402
from autonomous_dev.worker import (  # noqa: E402
    CURSOR_RUNTIME_OK_MARKER,
    Worker,
)
from autonomous_dev.state import StateStore  # noqa: E402

VENV_PYTHON = ROOT / ".venv" / "bin" / "python"


def _git_status_clean() -> bool:
    proc = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout.strip() == ""


def main() -> int:
    if not VENV_PYTHON.exists():
        print(f"FAIL: venv python missing at {VENV_PYTHON}")
        return 1

    py_version = subprocess.run(
        [str(VENV_PYTHON), "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    print(f"python: {VENV_PYTHON} ({py_version})")

    try:
        import cursor_sdk  # noqa: F401

        print("cursor_sdk import: OK")
    except ImportError as exc:
        print(f"FAIL: cursor_sdk import failed: {exc}")
        return 1

    settings = get_autonomous_settings()
    settings.validate_cursor_sdk_config()
    print(f"cursor_model: {settings.cursor_model}")

    before_dirty = not _git_status_clean()
    if before_dirty:
        print("WARN: working tree not clean before acceptance")

    store = StateStore(settings.state_db_path)
    worker = Worker(settings, store, repo_root=settings.repo_root)
    task = store.create_task(issue_number=2, delivery_id="live-cursor-runtime-acceptance")
    store.try_acquire_lock(2, task.id)

    try:
        result = worker.run_task(
            task,
            issue_body="[CURSOR-RUNTIME-ACCEPTANCE] Issue #2 P0 runtime check",
        )
    finally:
        store.release_lock()

    after_dirty = not _git_status_clean()
    if after_dirty and not before_dirty:
        print("FAIL: acceptance modified working tree")
        return 1

    if result.status.value != "running":
        print(f"FAIL: worker status={result.status} error={result.error}")
        return 1
    if result.commit_sha != CURSOR_RUNTIME_OK_MARKER:
        print(f"FAIL: commit_sha={result.commit_sha!r}, expected {CURSOR_RUNTIME_OK_MARKER}")
        return 1

    print(f"agent result: {CURSOR_RUNTIME_OK_MARKER}")
    print("Final: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
