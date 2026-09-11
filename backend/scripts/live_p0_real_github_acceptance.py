#!/usr/bin/env python3
"""Real GitHub webhook → Sealos production acceptance (no simulated webhooks)."""

from __future__ import annotations

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
from autonomous_dev.github_auth import resolve_github_auth  # noqa: E402
from autonomous_dev.github_client import LABEL_READY_FOR_REVIEW  # noqa: E402
from autonomous_dev.state import StateStore, TaskStatus  # noqa: E402

PUBLIC_BASE = os.environ.get(
    "AUTONOMOUS_WEBHOOK_PUBLIC_URL", "https://ynboesvphjna.sealosbja.site"
)


def _gh_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _get_labels(client: httpx.Client, repo: str, token: str, number: int) -> set[str]:
    url = f"https://api.github.com/repos/{repo}/issues/{number}"
    resp = client.get(url, headers=_gh_headers(token))
    resp.raise_for_status()
    return {lbl["name"] for lbl in resp.json().get("labels", [])}


def _create_sandbox_issue(client: httpx.Client, repo: str, token: str) -> int:
    url = f"https://api.github.com/repos/{repo}/issues"
    resp = client.post(
        url,
        headers=_gh_headers(token),
        json={
            "title": f"P0 real webhook acceptance {uuid.uuid4().hex[:8]}",
            "body": "Harmless real GitHub webhook → Sealos production acceptance.",
            "labels": ["cursor-task"],
        },
    )
    resp.raise_for_status()
    return resp.json()["number"]


def _add_label(client: httpx.Client, repo: str, token: str, number: int, label: str) -> None:
    url = f"https://api.github.com/repos/{repo}/issues/{number}/labels"
    resp = client.post(url, headers=_gh_headers(token), json=[label])
    resp.raise_for_status()


def _real_delivery_in_db(store: StateStore, issue_num: int) -> bool:
    with store._conn() as conn:  # noqa: SLF001 — acceptance reads delivery audit trail
        rows = conn.execute(
            """
            SELECT delivery_id, status FROM webhook_deliveries
            WHERE event_type = 'issues' AND status = 'processed'
            ORDER BY received_at DESC LIMIT 50
            """
        ).fetchall()
    for row in rows:
        delivery_id = row["delivery_id"]
        if delivery_id.startswith("p0-live-") or delivery_id.startswith("route-"):
            continue
        with store._conn() as conn:  # noqa: SLF001
            detail = conn.execute(
                "SELECT payload_json FROM webhook_deliveries WHERE delivery_id = ?",
                (delivery_id,),
            ).fetchone()
        if detail and f'"number": {issue_num}' in detail["payload_json"]:
            return True
    return False


def main() -> int:
    settings = get_autonomous_settings()
    auth = resolve_github_auth()
    if auth.mode == "none":
        print("FAIL: GitHub token not configured")
        return 1

    store = StateStore(settings.state_db_path)
    results: dict[str, str] = {}

    with httpx.Client(timeout=60.0) as client:
        results["A_public_healthz"] = (
            "PASS"
            if client.get(f"{PUBLIC_BASE.rstrip('/')}/healthz").status_code == 200
            else "FAIL"
        )

        issue_num = int(os.environ.get("REAL_ACCEPTANCE_ISSUE", "0"))
        if issue_num <= 0:
            issue_num = _create_sandbox_issue(client, settings.github_repo, auth.token)
            results["B_sandbox_issue"] = "PASS"
            _add_label(client, settings.github_repo, auth.token, issue_num, "current-task")
            results["C_real_label_event"] = "PASS"
        else:
            results["B_sandbox_issue"] = "SKIP"
            results["C_real_label_event"] = "SKIP"

        for _ in range(60):
            labels = _get_labels(client, settings.github_repo, auth.token, issue_num)
            if "worker-running" in labels:
                break
            time.sleep(2)
        results["D_worker_running_label"] = (
            "PASS" if "worker-running" in _get_labels(client, settings.github_repo, auth.token, issue_num) else "FAIL"
        )

        task = None
        for _ in range(180):
            task = store.get_task_by_issue(issue_num)
            if task and task.commit_sha and len(task.commit_sha) >= 12:
                break
            time.sleep(2)
        results["E_commit_recorded"] = (
            "PASS" if task and task.commit_sha and task.commit_sha != "CURSOR_AGENT_RUNTIME_OK" else "FAIL"
        )

        for _ in range(60):
            task = store.get_task_by_issue(issue_num)
            if task and task.status == TaskStatus.READY_FOR_REVIEW:
                break
            labels = _get_labels(client, settings.github_repo, auth.token, issue_num)
            if LABEL_READY_FOR_REVIEW in labels:
                break
            time.sleep(2)
        labels = _get_labels(client, settings.github_repo, auth.token, issue_num)
        results["F_ready_for_review"] = (
            "PASS" if LABEL_READY_FOR_REVIEW in labels else "FAIL"
        )

        results["G_real_github_delivery"] = (
            "PASS" if _real_delivery_in_db(store, issue_num) else "FAIL"
        )

    head = os.popen("git rev-parse HEAD").read().strip()
    origin = os.popen("git rev-parse origin/main").read().strip()
    wt = os.popen("git status --porcelain").read().strip()
    results["H_head_equals_origin"] = "PASS" if head and head == origin else "FAIL"
    results["I_working_tree_clean"] = "PASS" if wt == "" else "FAIL"

    print("=== Real GitHub Webhook → Sealos Production Acceptance ===")
    for k, v in results.items():
        print(f"{k}: {v}")
    blocking = list(results.values())
    all_pass = all(v in {"PASS", "SKIP"} for v in blocking)
    print(f"Final: {'PASS' if all_pass else 'FAIL'}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
