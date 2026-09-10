#!/usr/bin/env python3
"""Live acceptance A–M for event-driven autonomous dev loop."""

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
from autonomous_dev.worker import PRODUCT_DECISION_MARKER  # noqa: E402

BASE = os.environ.get("AUTONOMOUS_ACCEPTANCE_BASE", "http://127.0.0.1:8000")


def _sign(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _post(
    client: httpx.Client,
    event: str,
    delivery: str,
    payload: dict,
    secret: str,
    *,
    bad_sig: bool = False,
) -> httpx.Response:
    body = json.dumps(payload).encode()
    sig = "sha256=bad" if bad_sig else _sign(body, secret)
    return client.post(
        f"{BASE}/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": delivery,
            "X-Hub-Signature-256": sig,
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
    issue_base = int(os.environ.get("AUTONOMOUS_ACCEPTANCE_ISSUE_BASE", "88000"))
    results: dict[str, str] = {}

    with httpx.Client(timeout=15.0) as client:
        results["A_healthz"] = "PASS" if client.get(f"{BASE}/healthz").status_code == 200 else "FAIL"

        results["L_bad_signature"] = (
            "PASS"
            if _post(client, "ping", f"bad-{uuid.uuid4()}", {"zen": "x"}, secret, bad_sig=True).status_code
            == 401
            else "FAIL"
        )

        issue_num = issue_base + 1
        issue_payload = {
            "action": "labeled",
            "issue": {
                "number": issue_num,
                "state": "open",
                "title": "Live acceptance harmless task",
                "body": "Update acceptance marker only.",
                "labels": [{"name": "cursor-task"}, {"name": "current-task"}],
            },
        }
        delivery = f"live-issue-{uuid.uuid4()}"
        first = _post(client, "issues", delivery, issue_payload, secret)
        second = _post(client, "issues", delivery, issue_payload, secret)
        results["B_issue_webhook"] = (
            "PASS" if first.json().get("status") == "worker_started" else "FAIL"
        )
        results["D_duplicate_delivery"] = (
            "PASS" if second.json().get("status") == "duplicate" else "FAIL"
        )

        for _ in range(90):
            task = store.get_task_by_issue(issue_num)
            if task and task.commit_sha:
                break
            time.sleep(1)
        results["C_worker_started"] = (
            "PASS" if store.get_task_by_issue(issue_num) else "FAIL"
        )

        push_payload = {
            "ref": "refs/heads/main",
            "after": store.get_task_by_issue(issue_num).commit_sha or "abc123",
        }
        push_resp = _post(client, "push", f"live-push-{uuid.uuid4()}", push_payload, secret)
        results["I_push_webhook"] = (
            "PASS" if push_resp.json().get("status") == "ready_for_review" else "FAIL"
        )
        task = store.get_task_by_issue(issue_num)
        results["J_ready_for_review"] = (
            "PASS" if task and task.status == TaskStatus.READY_FOR_REVIEW else "FAIL"
        )

        store.try_acquire_lock(issue_base + 99, 9999)
        blocked = _post(
            client,
            "issues",
            f"block-{uuid.uuid4()}",
            {
                "action": "labeled",
                "issue": {
                    "number": issue_base + 99,
                    "state": "open",
                    "body": "x",
                    "labels": [{"name": "cursor-task"}, {"name": "current-task"}],
                },
            },
            secret,
        )
        results["K_concurrent_block"] = (
            "PASS" if blocked.json().get("status") == "ignored" else "FAIL"
        )
        store.release_lock()

        pd_num = issue_base + 2
        _post(
            client,
            "issues",
            f"pd-{uuid.uuid4()}",
            {
                "action": "opened",
                "issue": {
                    "number": pd_num,
                    "state": "open",
                    "body": f"simulate {PRODUCT_DECISION_MARKER}",
                    "labels": [{"name": "cursor-task"}, {"name": "current-task"}],
                },
            },
            secret,
        )
        for _ in range(30):
            pd = store.get_task_by_issue(pd_num)
            if pd and pd.status == TaskStatus.PRODUCT_DECISION:
                break
            time.sleep(1)
        pd = store.get_task_by_issue(pd_num)
        results["M_product_decision"] = (
            "PASS" if pd and pd.status == TaskStatus.PRODUCT_DECISION else "FAIL"
        )

    print("=== Live Acceptance ===")
    for k, v in results.items():
        print(f"{k}: {v}")
    failed = [k for k, v in results.items() if v == "FAIL"]
    if failed:
        print(f"Final: FAIL ({', '.join(failed)})")
        return 1
    print("Final: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
