#!/usr/bin/env python3
"""Live acceptance — technical needs-fix self-heal without manual label edits."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from autonomous_dev.config import get_autonomous_settings  # noqa: E402
from autonomous_dev.state import StateStore, TaskStatus  # noqa: E402
from autonomous_dev.worker_failure import FAILURE_COMMENT_MARKER  # noqa: E402
from autonomous_dev.worker_self_heal import SELF_HEAL_ACCEPTANCE_MARKER  # noqa: E402

BASE = os.environ.get("AUTONOMOUS_ACCEPTANCE_BASE", "http://127.0.0.1:8000")


def _sign(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _post(client: httpx.Client, event: str, delivery: str, payload: dict, secret: str) -> httpx.Response:
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
        timeout=60.0,
    )


def main() -> int:
    settings = get_autonomous_settings()
    secret = settings.github_webhook_secret
    if not secret:
        print("FAIL: GITHUB_WEBHOOK_SECRET not set")
        return 1

    store = StateStore(settings.state_db_path)
    issue_num = int(os.environ.get("SELF_HEAL_ACCEPTANCE_ISSUE", "63"))
    results: dict[str, str] = {}

    with httpx.Client(timeout=30.0) as client:
        if client.get(f"{BASE}/healthz").status_code != 200:
            print("FAIL: healthz unavailable")
            return 1

        issue_payload = {
            "action": "labeled",
            "issue": {
                "number": issue_num,
                "state": "open",
                "title": "Live self-heal acceptance",
                "body": (
                    f"{SELF_HEAL_ACCEPTANCE_MARKER}\n"
                    "Force one technical startup failure then expect automatic recovery."
                ),
                "labels": [{"name": "cursor-task"}, {"name": "current-task"}],
                "updated_at": "2026-09-12T05:00:00Z",
                "created_at": "2026-09-12T04:00:00Z",
            },
        }
        first = _post(client, "issues", f"self-heal-first-{uuid.uuid4()}", issue_payload, secret)
        results["first_webhook"] = "PASS" if first.json().get("status") == "worker_started" else "FAIL"

        saw_needs_fix = False
        saw_retry = False
        saw_running = False
        saw_failure_reason = False
        saw_failure_comment = False
        failure_execution_key: str | None = None
        deadline = time.time() + 180
        while time.time() < deadline:
            task = store.get_task_by_issue(issue_num)
            react = store.get_worker_reactivation(issue_num)
            if task and task.status == TaskStatus.NEEDS_FIX:
                saw_needs_fix = True
                if task.execution_key:
                    failure_execution_key = task.execution_key
                    failure = store.get_execution_failure(task.execution_key)
                    if failure and failure.error_message:
                        saw_failure_reason = True
                    if failure and failure.comment_posted_at:
                        saw_failure_comment = True
            if react and react.attempt_count >= 1:
                saw_retry = True
            if task and task.status in {TaskStatus.RUNNING, TaskStatus.READY_FOR_REVIEW}:
                if saw_needs_fix and (react is None or react.attempt_count >= 2):
                    saw_running = True
                    break
            time.sleep(2)

        results["needs_fix_observed"] = "PASS" if saw_needs_fix else "FAIL"
        results["self_heal_retry"] = "PASS" if saw_retry else "FAIL"
        results["recovered_running"] = "PASS" if saw_running else "FAIL"
        results["failure_reason_persisted"] = "PASS" if saw_failure_reason else "FAIL"
        results["failure_comment_emitted"] = "PASS" if saw_failure_comment else "FAIL"

        dash = client.get(f"{BASE}/autonomous/status.json", timeout=15.0)
        if dash.status_code == 200:
            dash_json = dash.json()
            self_heal = dash_json.get("self_heal") or {}
            worker_failure = dash_json.get("worker_failure") or {}
            results["dashboard_self_heal"] = (
                "PASS"
                if self_heal.get("status") in {"pending", "scheduled", "running"} or saw_running
                else "FAIL"
            )
            results["dashboard_failure_reason"] = (
                "PASS" if worker_failure.get("message") or saw_failure_reason else "FAIL"
            )
        else:
            results["dashboard_self_heal"] = "FAIL"
            results["dashboard_failure_reason"] = "FAIL"

        if not saw_failure_comment and failure_execution_key:
            try:
                from autonomous_dev.github_auth import resolve_github_token

                token = resolve_github_token()
                if token:
                    headers = {
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    }
                    url = (
                        f"https://api.github.com/repos/{settings.github_repo}/issues/"
                        f"{issue_num}/comments"
                    )
                    with httpx.Client(timeout=15.0) as gh:
                        resp = gh.get(url, headers=headers)
                        if resp.status_code == 200:
                            saw_failure_comment = any(
                                FAILURE_COMMENT_MARKER in str(c.get("body") or "")
                                and failure_execution_key in str(c.get("body") or "")
                                for c in resp.json()
                            )
                            results["failure_comment_emitted"] = (
                                "PASS" if saw_failure_comment else "FAIL"
                            )
            except Exception:
                pass

    print(json.dumps(results, indent=2))
    return 0 if all(v == "PASS" for v in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
