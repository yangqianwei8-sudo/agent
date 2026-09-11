#!/usr/bin/env python3
"""Live A→B handoff acceptance — reviewer PASS auto-activates next task."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from autonomous_dev.config import get_autonomous_settings  # noqa: E402
from autonomous_dev.github_client import GitHubClient  # noqa: E402
from autonomous_dev.reviewer_service import REVIEWER_ACCEPTANCE_MARKER  # noqa: E402
from autonomous_dev.state import StateStore, TaskStatus  # noqa: E402
from autonomous_dev.task_handoff import TaskHandoffEngine  # noqa: E402

BASE = os.environ.get("AUTONOMOUS_ACCEPTANCE_BASE", "http://127.0.0.1:8080")


def _sign(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _post(client: httpx.Client, event: str, delivery: str, payload: dict, secret: str) -> dict:
    body = json.dumps(payload).encode()
    return client.post(
        f"{BASE}/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": delivery,
            "X-Hub-Signature-256": _sign(body, secret),
            "Content-Type": "application/json",
        },
        timeout=120.0,
    ).json()


def _main_sha(repo: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def run_direct(settings, store: StateStore, issue_a: int, issue_b: int) -> tuple[dict[str, str], dict[str, float]]:
    """Direct handoff path — real GitHub labels + sealed PASS + handoff engine."""
    github = GitHubClient(settings)
    timings: dict[str, float] = {}
    results: dict[str, str] = {}
    t0 = time.monotonic()

    github.set_issue_labels(issue_b, {"cursor-task"})
    github.sync_completed(issue_a)
    try:
        github.close_issue(issue_a, reason="Handoff acceptance A sealed")
    except Exception:
        pass

    commit_sha = _main_sha(settings.repo_root)
    body = (
        f"{REVIEWER_ACCEPTANCE_MARKER}\n"
        f"[P0-LIVE-ACCEPTANCE]\nHandoff direct acceptance A→B.\n"
        f"NEXT_TASK: [ACCEPT] Handoff task B"
    )
    github.update_issue_body(issue_a, body)
    task_a = store.create_task(
        issue_number=issue_a,
        delivery_id=f"handoff-direct-a-{uuid.uuid4()}",
        execution_key=f"{settings.github_repo}#{issue_a}#direct-{uuid.uuid4().hex[:8]}",
    )
    store.update_task(
        task_a.id,
        status=TaskStatus.COMPLETED,
        commit_sha=commit_sha,
    )

    labels_b_before = github.get_issue_labels(issue_b)
    t_handoff = time.monotonic()
    engine = TaskHandoffEngine(settings, store, github=github)
    handoff_result = engine.perform_handoff(
        store.get_task(task_a.id),
        commit_sha=commit_sha,
        issue_body=body,
        trigger_worker=True,
    )
    timings["handoff_activate_s"] = round(time.monotonic() - t_handoff, 3)
    results["A_sealed"] = "PASS"
    results["handoff_activated"] = "PASS" if handoff_result.get("status") == "activated" else "FAIL"

    handoff = store.get_handoff_by_key(f"handoff:{task_a.id}:{commit_sha[:12]}")
    results["handoff_record"] = "PASS" if handoff and handoff.next_issue_number == issue_b else "FAIL"

    labels_b = github.get_issue_labels(issue_b)
    results["B_current_task_label"] = "PASS" if "current-task" in labels_b else "FAIL"

    store.recover_stale_lease()
    t_worker = time.monotonic()
    worker_started = False
    for _ in range(25):
        task_b = store.get_task_by_issue(issue_b)
        lease = store.get_lease()
        if task_b and task_b.status in {TaskStatus.QUEUED, TaskStatus.RUNNING}:
            worker_started = True
            break
        if lease.locked and lease.issue_number == issue_b:
            worker_started = True
            break
        if not store.is_locked():
            engine.recover_pending_handoffs()
        time.sleep(1)
    timings["B_worker_start_s"] = round(time.monotonic() - t_worker, 2)
    results["B_worker_within_20s"] = (
        "PASS" if worker_started and timings["B_worker_start_s"] <= 20 else "FAIL"
    )

    replay = engine.perform_handoff(
        store.get_task(task_a.id),
        commit_sha=commit_sha,
        trigger_worker=False,
    )
    results["replay_no_duplicate"] = "PASS" if replay.get("status") == "idempotent" else "FAIL"
    results["B_labels_before"] = ",".join(sorted(labels_b_before))
    results["B_labels_after"] = ",".join(sorted(labels_b))
    timings["total_s"] = round(time.monotonic() - t0, 2)
    return results, timings


def run_webhook(settings, store: StateStore, issue_a: int, issue_b: int) -> tuple[dict[str, str], dict[str, float]]:
    secret = settings.github_webhook_secret
    timings: dict[str, float] = {}
    results: dict[str, str] = {}
    t0 = time.monotonic()
    with httpx.Client(timeout=30.0) as client:
        issue_a_body = (
            f"{REVIEWER_ACCEPTANCE_MARKER}\n[P0-LIVE-ACCEPTANCE]\n"
            f"Handoff acceptance task A.\nNEXT_TASK: [ACCEPT] Handoff task B"
        )
        payload_a = {
            "action": "labeled",
            "issue": {
                "number": issue_a,
                "state": "open",
                "title": "[ACCEPT] Handoff task A",
                "body": issue_a_body,
                "labels": [{"name": "cursor-task"}, {"name": "current-task"}],
                "updated_at": "2026-09-11T10:00:00Z",
                "created_at": "2026-09-11T09:00:00Z",
            },
        }
        r_a = _post(client, "issues", f"handoff-a-{uuid.uuid4()}", payload_a, secret)
        results["A_worker_started"] = "PASS" if r_a.get("status") == "worker_started" else "FAIL"

        for _ in range(120):
            task_a = store.get_task_by_issue(issue_a)
            if task_a and task_a.status == TaskStatus.READY_FOR_REVIEW and task_a.commit_sha:
                break
            time.sleep(1)
        task_a = store.get_task_by_issue(issue_a)
        results["A_ready_for_review"] = (
            "PASS" if task_a and task_a.status == TaskStatus.READY_FOR_REVIEW else "FAIL"
        )
        timings["A_to_ready_for_review_s"] = round(time.monotonic() - t0, 2)

        if not task_a or not task_a.commit_sha:
            return results, timings

        t_review = time.monotonic()
        for _ in range(180):
            task_a = store.get_task_by_issue(issue_a)
            if task_a and task_a.status == TaskStatus.COMPLETED:
                break
            time.sleep(1)
        results["A_reviewer_pass_sealed"] = (
            "PASS" if task_a and task_a.status == TaskStatus.COMPLETED else "FAIL"
        )
        timings["A_review_pass_s"] = round(time.monotonic() - t_review, 2)

        t_handoff = time.monotonic()
        worker_started = False
        for _ in range(25):
            dash = client.get(f"{BASE}/autonomous/status.json").json()
            motion = dash.get("execution_trace", {}).get("motion_status")
            handoff_info = dash.get("handoff") or {}
            task_b = store.get_task_by_issue(issue_b)
            if task_b and task_b.status in {TaskStatus.QUEUED, TaskStatus.RUNNING}:
                worker_started = True
                break
            if motion == "MOVING" and handoff_info.get("next_issue_number") == issue_b:
                worker_started = True
                break
            time.sleep(1)
        timings["B_worker_start_s"] = round(time.monotonic() - t_handoff, 2)
        results["B_worker_within_20s"] = (
            "PASS" if worker_started and timings["B_worker_start_s"] <= 20 else "FAIL"
        )

        dash = client.get(f"{BASE}/autonomous/status.json").json()
        results["dashboard_motion"] = dash.get("execution_trace", {}).get("motion_status") or "NONE"
        results["dashboard_not_idle"] = (
            "PASS" if dash.get("system_status") != "IDLE" else "FAIL"
        )
    return results, timings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--direct", action="store_true", help="Direct handoff path (reviewer PASS only)")
    args = parser.parse_args()

    settings = get_autonomous_settings()
    if not settings.github_webhook_secret and not args.direct:
        print("FAIL: GITHUB_WEBHOOK_SECRET not set")
        return 1

    store = StateStore(settings.state_db_path)
    issue_a = int(os.environ.get("HANDOFF_ISSUE_A", "25"))
    issue_b = int(os.environ.get("HANDOFF_ISSUE_B", "26"))

    if args.direct:
        results, timings = run_direct(settings, store, issue_a, issue_b)
    else:
        results, timings = run_webhook(settings, store, issue_a, issue_b)

    print("=== Live Handoff Acceptance ===")
    for k, v in results.items():
        print(f"{k}: {v}")
    print("=== Timings (seconds) ===")
    for k, v in timings.items():
        print(f"{k}: {v}")
    failed = [k for k, v in results.items() if v == "FAIL"]
    if failed:
        print(f"Final: FAIL ({', '.join(failed)})")
        return 1
    print("Final: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
