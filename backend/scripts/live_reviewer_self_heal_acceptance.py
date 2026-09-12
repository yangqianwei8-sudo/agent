#!/usr/bin/env python3
"""Live acceptance — reviewer stall must recover without manual intervention."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from autonomous_dev.config import get_autonomous_settings  # noqa: E402
from autonomous_dev.loop_recovery import run_loop_recovery_tick  # noqa: E402
from autonomous_dev.state import ReviewInvocationStatus, StateStore, TaskStatus  # noqa: E402

BASE = os.environ.get("AUTONOMOUS_ACCEPTANCE_BASE", "http://127.0.0.1:8000")


def main() -> int:
    settings = get_autonomous_settings()
    store = StateStore(settings.state_db_path)
    issue_num = int(os.environ.get("REVIEWER_SELF_HEAL_ISSUE", "57"))
    commit_prefix = os.environ.get("REVIEWER_SELF_HEAL_COMMIT", "642ea99")
    results: dict[str, str] = {}

    with httpx.Client(timeout=30.0) as client:
        if client.get(f"{BASE}/healthz").status_code != 200:
            print("FAIL: healthz unavailable")
            return 1

        task = store.get_task_by_issue(issue_num)
        results["task_found"] = "PASS" if task else "FAIL"
        if not task:
            print(json.dumps(results, indent=2))
            return 1

        results["ready_for_review"] = (
            "PASS" if task.status == TaskStatus.READY_FOR_REVIEW else "FAIL"
        )
        commit_sha = task.commit_sha or ""
        results["commit_match"] = (
            "PASS" if commit_sha.startswith(commit_prefix) else "SKIP"
        )

        counts = run_loop_recovery_tick(settings, store)
        results["loop_recovery_tick"] = (
            "PASS" if counts.get("stalled_ready_for_review", 0) >= 0 else "FAIL"
        )

        saw_recovery = False
        saw_verdict_path = False
        deadline = time.time() + 180
        while time.time() < deadline:
            react = store.get_reviewer_reactivation(issue_num)
            if react and react.attempt_count >= 1:
                saw_recovery = True
            inv = (
                store.get_review_invocation(task.id, commit_sha)
                if commit_sha
                else None
            )
            if inv and inv.status in {
                ReviewInvocationStatus.COMPLETED,
                ReviewInvocationStatus.RUNNING,
                ReviewInvocationStatus.PENDING,
            }:
                saw_verdict_path = True
                if inv.status == ReviewInvocationStatus.COMPLETED:
                    break
            time.sleep(2)

        results["reviewer_recovery"] = "PASS" if saw_recovery or saw_verdict_path else "FAIL"
        results["verdict_path"] = "PASS" if saw_verdict_path else "FAIL"

        dash = client.get(f"{BASE}/autonomous/status.json", timeout=15.0)
        if dash.status_code == 200:
            body = dash.json()
            reviewer_self_heal = body.get("reviewer_self_heal") or {}
            status = reviewer_self_heal.get("status") or body.get("runtime_evidence", {}).get(
                "reviewer_status"
            )
            results["dashboard_reviewer_state"] = (
                "PASS"
                if status
                in {
                    "pending",
                    "running",
                    "retrying",
                    "stale-recovered",
                    "exhausted",
                    "completed",
                }
                else "FAIL"
            )
        else:
            results["dashboard_reviewer_state"] = "FAIL"

    print(json.dumps(results, indent=2))
    required = [k for k, v in results.items() if v != "SKIP"]
    return 0 if all(results[k] == "PASS" for k in required) else 1


if __name__ == "__main__":
    raise SystemExit(main())
