#!/usr/bin/env python3
"""P0 production live acceptance A–M — real webhooks, GitHub labels, cursor_sdk worker."""

from __future__ import annotations

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
from autonomous_dev.github_auth import resolve_github_auth  # noqa: E402
from autonomous_dev.github_client import (  # noqa: E402
    LABEL_PRODUCT_DECISION,
    LABEL_READY_FOR_REVIEW,
)
from autonomous_dev.state import StateStore, TaskStatus  # noqa: E402
from autonomous_dev.worker import P0_LIVE_ACCEPTANCE_MARKER, PRODUCT_DECISION_MARKER  # noqa: E402

VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
BASE = os.environ.get("AUTONOMOUS_ACCEPTANCE_BASE", "http://127.0.0.1:8000")


def _sign(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _post_webhook(
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


def _gh_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _create_sandbox_issue(client: httpx.Client, repo: str, token: str, title: str) -> dict:
    url = f"https://api.github.com/repos/{repo}/issues"
    resp = client.post(
        url,
        headers=_gh_headers(token),
        json={"title": title, "body": "P0 sandbox acceptance", "labels": ["cursor-task", "current-task"]},
    )
    resp.raise_for_status()
    return resp.json()


def _get_labels(client: httpx.Client, repo: str, token: str, number: int) -> set[str]:
    url = f"https://api.github.com/repos/{repo}/issues/{number}"
    resp = client.get(url, headers=_gh_headers(token))
    resp.raise_for_status()
    return {lbl["name"] for lbl in resp.json().get("labels", [])}


def _ensure_service() -> bool:
    try:
        r = httpx.get(f"{BASE}/healthz", timeout=5.0)
        return r.status_code == 200
    except httpx.HTTPError:
        return False


def _start_service() -> None:
    log = ROOT / "data" / "p0-acceptance-uvicorn.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(
        [str(ROOT / ".venv" / "bin" / "uvicorn"), "backend.main:app", "--host", "127.0.0.1", "--port", "8000"],
        cwd=ROOT,
        stdout=log.open("a"),
        stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
    )
    for _ in range(30):
        if _ensure_service():
            return
        time.sleep(1)
    raise RuntimeError("service failed to start")


def main() -> int:
    settings = get_autonomous_settings()
    secret = settings.github_webhook_secret
    auth = resolve_github_auth()
    if not secret:
        print("FAIL: GITHUB_WEBHOOK_SECRET not set")
        return 1
    if auth.mode == "none":
        print("FAIL: GitHub token not configured")
        return 1

    store = StateStore(settings.state_db_path)
    results: dict[str, str] = {}

    if not _ensure_service():
        _start_service()
    results["A_service_start"] = "PASS" if _ensure_service() else "FAIL"

    with httpx.Client(timeout=60.0) as client:
        results["B_healthz"] = "PASS" if client.get(f"{BASE}/healthz").status_code == 200 else "FAIL"

        results["C_invalid_signature"] = (
            "PASS"
            if _post_webhook(client, "ping", f"bad-{uuid.uuid4()}", {"zen": "x"}, secret, bad_sig=True).status_code
            == 401
            else "FAIL"
        )

        sandbox = _create_sandbox_issue(
            client,
            settings.github_repo,
            auth.token,
            f"P0 live acceptance {uuid.uuid4().hex[:8]}",
        )
        issue_num = sandbox["number"]
        updated_at = sandbox["updated_at"]

        issue_payload = {
            "action": "labeled",
            "issue": {
                "number": issue_num,
                "state": "open",
                "title": sandbox["title"],
                "body": f"{P0_LIVE_ACCEPTANCE_MARKER}\nHarmless P0 acceptance only.",
                "labels": [{"name": "cursor-task"}, {"name": "current-task"}],
                "updated_at": updated_at,
                "created_at": sandbox["created_at"],
            },
        }
        delivery1 = f"p0-live-{uuid.uuid4()}"
        r1 = _post_webhook(client, "issues", delivery1, issue_payload, secret)
        results["D_valid_issue_webhook"] = "PASS" if r1.status_code == 200 else "FAIL"

        closed_payload = dict(issue_payload)
        closed_payload["issue"] = dict(issue_payload["issue"])
        closed_payload["issue"]["state"] = "closed"
        closed_payload["issue"]["labels"] = [{"name": "cursor-task"}]
        r_closed = _post_webhook(
            client, "issues", f"closed-{uuid.uuid4()}", closed_payload, secret
        )
        results["E_open_current_task_only"] = (
            "PASS" if r_closed.json().get("status") == "ignored" else "FAIL"
        )

        results["F_one_worker_starts"] = (
            "PASS" if r1.json().get("status") == "worker_started" else "FAIL"
        )

        r_dup = _post_webhook(client, "issues", delivery1, issue_payload, secret)
        results["G_duplicate_delivery"] = (
            "PASS" if r_dup.json().get("status") == "duplicate" else "FAIL"
        )

        delivery2 = f"p0-live-{uuid.uuid4()}"
        r2 = _post_webhook(client, "issues", delivery2, issue_payload, secret)
        results["H_same_task_new_delivery"] = (
            "PASS" if r2.json().get("status") == "ignored" else "FAIL"
        )

        store.try_acquire_lease(99999, 99999, owner="blocker", ttl_seconds=120)
        block_payload = _issue_payload_like(99002, updated_at)
        r_block = _post_webhook(
            client, "issues", f"block-{uuid.uuid4()}", block_payload, secret
        )
        store.release_lease("blocker")
        results["I_concurrent_block"] = (
            "PASS" if r_block.json().get("status") == "ignored" else "FAIL"
        )

        task = None
        for _ in range(180):
            task = store.get_task_by_issue(issue_num)
            if task and task.commit_sha and len(task.commit_sha) >= 12:
                break
            time.sleep(2)
        results["J_cursor_sdk_runtime"] = (
            "PASS"
            if task and task.commit_sha and task.commit_sha != "CURSOR_AGENT_RUNTIME_OK"
            else "FAIL"
        )
        results["K_harmless_modification"] = (
            "PASS" if (ROOT / "autonomous_dev" / "acceptance_marker.txt").exists() else "FAIL"
        )
        results["L_commit_push"] = "PASS" if task and task.commit_sha else "FAIL"

        if task and task.commit_sha:
            push_resp = _post_webhook(
                client,
                "push",
                f"push-{uuid.uuid4()}",
                {"ref": "refs/heads/main", "after": task.commit_sha},
                secret,
            )
            results["M_push_ready_for_review"] = (
                "PASS" if push_resp.json().get("status") == "ready_for_review" else "FAIL"
            )
            time.sleep(2)
            labels = _get_labels(client, settings.github_repo, auth.token, issue_num)
            results["label_lifecycle"] = (
                "PASS" if LABEL_READY_FOR_REVIEW in labels else "FAIL"
            )
        else:
            results["M_push_ready_for_review"] = "FAIL"
            results["label_lifecycle"] = "FAIL"

        pd_num = _create_sandbox_issue(
            client, settings.github_repo, auth.token, f"P0 product decision {uuid.uuid4().hex[:6]}"
        )["number"]
        pd_payload = {
            "action": "opened",
            "issue": {
                "number": pd_num,
                "state": "open",
                "body": f"simulate {PRODUCT_DECISION_MARKER}",
                "labels": [{"name": "cursor-task"}, {"name": "current-task"}],
                "updated_at": pd_num and sandbox["updated_at"],
                "created_at": sandbox["created_at"],
            },
        }
        _post_webhook(client, "issues", f"pd-{uuid.uuid4()}", pd_payload, secret)
        for _ in range(30):
            pd_task = store.get_task_by_issue(pd_num)
            if pd_task and pd_task.status == TaskStatus.PRODUCT_DECISION:
                break
            time.sleep(1)
        pd_labels = _get_labels(client, settings.github_repo, auth.token, pd_num)
        results["product_decision"] = (
            "PASS" if LABEL_PRODUCT_DECISION in pd_labels else "FAIL"
        )

    store.recover_stale_lease()
    store.try_acquire_lease(1, 1, owner="stale-test", ttl_seconds=1)
    time.sleep(1.2)
    recovered = store.recover_stale_lease()
    results["lease_recovery"] = "PASS" if recovered else "FAIL"

    task = store.get_task_by_issue(issue_num)
    if task and task.status == TaskStatus.READY_FOR_REVIEW:
        seal = httpx.post(
            f"{BASE}/autonomous/review/pass",
            headers={"X-Autonomous-Secret": secret},
            json={"issue_number": issue_num},
            timeout=30.0,
        )
        results["reviewer_completion"] = "PASS" if seal.status_code == 200 else "FAIL"
    else:
        results["reviewer_completion"] = "FAIL"

    from autonomous_dev.watchdog import _tick

    try:
        _tick()
        results["watchdog"] = "PASS"
    except Exception:
        results["watchdog"] = "FAIL"

    deploy_ok = all(
        (ROOT / p).exists()
        for p in (
            "deploy/sealos/deployment.yaml",
            "deploy/sealos/service.yaml",
            "deploy/sealos/ingress.yaml",
            "deploy/sealos/pvc.yaml",
            "deploy/sealos/configmap.yaml",
            "deploy/sealos/secret.example.yaml",
        )
    )
    results["sealos_deployment"] = "PASS" if deploy_ok else "FAIL"

    wt = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True)
    results["working_tree_clean"] = "PASS" if wt.stdout.strip() == "" else "FAIL"

    extra = {
        "exactly_once": results.get("H_same_task_new_delivery", "FAIL"),
        "lease_recovery": results.get("lease_recovery", "FAIL"),
        "label_lifecycle": results.get("label_lifecycle", "FAIL"),
    }

    am_map = {
        "A": results.get("A_service_start"),
        "B": results.get("B_healthz"),
        "C": results.get("C_invalid_signature"),
        "D": results.get("D_valid_issue_webhook"),
        "E": results.get("E_open_current_task_only"),
        "F": results.get("F_one_worker_starts"),
        "G": results.get("G_duplicate_delivery"),
        "H": results.get("H_same_task_new_delivery"),
        "I": results.get("I_concurrent_block"),
        "J": results.get("J_cursor_sdk_runtime"),
        "K": results.get("K_harmless_modification"),
        "L": results.get("L_commit_push"),
        "M": results.get("M_push_ready_for_review"),
    }
    print("=== P0 Production Live Acceptance ===")
    for letter, val in am_map.items():
        print(f"{letter}: {val or 'FAIL'}")
    for k, v in extra.items():
        print(f"{k}: {v}")
    print(f"product_decision: {results.get('product_decision', 'FAIL')}")
    print(f"reviewer_completion: {results.get('reviewer_completion', 'FAIL')}")
    print(f"watchdog: {results.get('watchdog', 'FAIL')}")
    print(f"sealos_deployment: {results.get('sealos_deployment', 'FAIL')}")
    print(f"working_tree_clean: {results.get('working_tree_clean', 'FAIL')}")

    blocking = list(am_map.values()) + [
        results.get("label_lifecycle"),
        results.get("product_decision"),
        results.get("lease_recovery"),
        results.get("reviewer_completion"),
        results.get("watchdog"),
        results.get("sealos_deployment"),
    ]
    all_pass = all(v == "PASS" for v in blocking if v)
    print(f"Final: {'PASS' if all_pass else 'FAIL'}")
    return 0 if all_pass else 1


def _issue_payload_like(number: int, updated_at: str) -> dict:
    return {
        "action": "labeled",
        "issue": {
            "number": number,
            "state": "open",
            "body": "concurrent block test",
            "labels": [{"name": "cursor-task"}, {"name": "current-task"}],
            "updated_at": updated_at,
            "created_at": updated_at,
        },
    }


if __name__ == "__main__":
    raise SystemExit(main())
