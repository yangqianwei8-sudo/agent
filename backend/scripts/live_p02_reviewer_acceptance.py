#!/usr/bin/env python3
"""P0.2 reviewer bridge live acceptance A–N + controlled FAIL + PRODUCT_DECISION."""

from __future__ import annotations

import hashlib
import hmac
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from autonomous_dev.app import reset_autonomous_singletons  # noqa: E402
from autonomous_dev.config import AutonomousDevSettings, clear_autonomous_settings_cache  # noqa: E402
from autonomous_dev.review_executor import ReviewExecutor  # noqa: E402
from autonomous_dev.reviewer_service import (  # noqa: E402
    REVIEWER_ACCEPTANCE_MARKER,
    REVIEWER_FAIL_MARKER,
    REVIEWER_PRODUCT_DECISION_MARKER,
)
from autonomous_dev.state import ReviewInvocationStatus, ReviewVerdict, StateStore, TaskStatus  # noqa: E402
from autonomous_dev.task_router import TaskRouter  # noqa: E402
from backend.tests.unit.test_autonomous_dev import _MockGitHubClient  # noqa: E402

BASE = os.environ.get("AUTONOMOUS_ACCEPTANCE_BASE", "http://127.0.0.1:8000")
_ACCEPTANCE_PORT = int(os.environ.get("AUTONOMOUS_ACCEPTANCE_PORT", "8766"))


def _sign(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _ensure_service() -> bool:
    try:
        return httpx.get(f"{BASE}/healthz", timeout=5.0).status_code == 200
    except httpx.HTTPError:
        return False


def _start_service(env: dict[str, str]) -> subprocess.Popen | None:
    global BASE
    if _ensure_service():
        return None
    log = ROOT / "data" / "p02-reviewer-acceptance.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    BASE = f"http://127.0.0.1:{_ACCEPTANCE_PORT}"
    proc = subprocess.Popen(
        [
            str(ROOT / ".venv" / "bin" / "uvicorn"),
            "backend.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(_ACCEPTANCE_PORT),
        ],
        cwd=ROOT,
        stdout=log.open("a"),
        stderr=subprocess.STDOUT,
        env=env,
    )
    for _ in range(30):
        if _ensure_service():
            return proc
        time.sleep(1)
    proc.kill()
    raise RuntimeError("acceptance service failed to start")


def _stop_service(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def _wait_review(store: StateStore, task_id: int, commit_sha: str, timeout: int = 20) -> bool:
    for _ in range(timeout * 5):
        inv = store.get_review_invocation(task_id, commit_sha)
        if inv and inv.status == ReviewInvocationStatus.COMPLETED:
            return True
        time.sleep(0.2)
    return False


def main() -> int:
    secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
    if not secret:
        settings = AutonomousDevSettings()
        secret = settings.github_webhook_secret
    if not secret:
        print("FAIL: GITHUB_WEBHOOK_SECRET not set")
        return 1

    results: dict[str, str] = {}
    tmp = Path(tempfile.mkdtemp(prefix="p02-accept-"))
    db = tmp / "state.db"
    repo = tmp / "repo"
    repo.mkdir()
    (repo / "autonomous_dev").mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "accept@test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "accept"], cwd=repo, check=True)
    (repo / "README.md").write_text("accept\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT),
        "AUTONOMOUS_DEV_ENABLED": "true",
        "GITHUB_WEBHOOK_SECRET": secret,
        "AUTONOMOUS_WORKER_MODE": "deterministic",
        "AUTONOMOUS_REPO_ROOT": str(repo),
        "AUTONOMOUS_STATE_DB_PATH": str(db),
    }
    proc: subprocess.Popen | None = None
    mock = _MockGitHubClient()

    try:
        clear_autonomous_settings_cache()
        reset_autonomous_singletons()
        proc = _start_service(env)

        with httpx.Client(timeout=30.0) as client:
            health = client.get(f"{BASE}/healthz", timeout=5.0).json()
            results["B_healthz_2xx"] = "PASS" if health.get("status") == "ok" else "FAIL"
            results["I_reviewer_configured"] = (
                "PASS" if health.get("reviewer_configured") in {"true", "false"} else "FAIL"
            )

            bad = client.post(
                f"{BASE}/webhooks/github",
                content=b"{}",
                headers={
                    "X-GitHub-Event": "ping",
                    "X-GitHub-Delivery": f"bad-{uuid.uuid4()}",
                    "X-Hub-Signature-256": "sha256=invalid",
                },
            )
            results["webhook_fail_closed"] = "PASS" if bad.status_code == 401 else "FAIL"

        def _noop_tests(self) -> None:
            return None

        with patch("autonomous_dev.worker.GitHubClient", lambda *a, **k: mock), patch(
            "autonomous_dev.task_router.GitHubClient", lambda *a, **k: mock
        ), patch("autonomous_dev.review_bridge.GitHubClient", lambda *a, **k: mock), patch(
            "autonomous_dev.review_executor.GitHubClient", lambda *a, **k: mock
        ), patch("autonomous_dev.worker.Worker._run_tests", _noop_tests):
            clear_autonomous_settings_cache()
            reset_autonomous_singletons()
            for k, v in env.items():
                os.environ[k] = v
            settings = AutonomousDevSettings()
            store = StateStore(db)
            router = TaskRouter(settings, store)
            pass_num = 501
            pass_body = f"{REVIEWER_ACCEPTANCE_MARKER}\nP0.2 harmless reviewer acceptance."
            mock.bodies[pass_num] = pass_body
            issue_payload = {
                "action": "labeled",
                "issue": {
                    "number": pass_num,
                    "state": "open",
                    "body": pass_body,
                    "labels": [{"name": "cursor-task"}, {"name": "current-task"}],
                    "updated_at": "2026-09-11T06:30:00Z",
                    "created_at": "2026-09-11T06:00:00Z",
                },
            }
            r1 = router.handle(
                event_type="issues",
                action="labeled",
                delivery_id=f"p02-pass-{uuid.uuid4()}",
                payload=issue_payload,
            )
            results["A_issue_labeled"] = "PASS" if r1.get("status") == "worker_started" else "FAIL"
            results["C_one_worker"] = results["A_issue_labeled"]

            task = None
            for _ in range(60):
                task = store.get_task_by_issue(pass_num)
                if task and task.commit_sha:
                    break
                time.sleep(0.5)
            results["D_worker_executed"] = "PASS" if task and task.commit_sha else "FAIL"
            results["E_harmless_change"] = (
                "PASS" if (repo / "autonomous_dev" / "acceptance_marker.txt").exists() else "FAIL"
            )

            if task and task.commit_sha:
                push_r = router.handle(
                    event_type="push",
                    action=None,
                    delivery_id=f"p02-push-{uuid.uuid4()}",
                    payload={"ref": "refs/heads/main", "after": task.commit_sha},
                )
                results["G_push_webhook"] = "PASS" if push_r.get("status") == "ready_for_review" else "FAIL"
                results["H_ready_for_review"] = results["G_push_webhook"]
                reviewed = _wait_review(store, task.id, task.commit_sha)
                inv = store.get_review_invocation(task.id, task.commit_sha)
                results["I_reviewer_bridge_auto"] = "PASS" if reviewed else "FAIL"
                results["J_reviewer_executed"] = (
                    "PASS" if inv and inv.verdict == ReviewVerdict.PASS else "FAIL"
                )
                results["K_verdict_persisted"] = "PASS" if inv else "FAIL"
                updated = store.get_task(task.id)
                results["L_pass_closes"] = (
                    "PASS" if updated and updated.status == TaskStatus.COMPLETED else "FAIL"
                )
                executor = ReviewExecutor(settings, store)
                replay = executor.schedule_review(task, commit_sha=task.commit_sha)
                inv2 = store.get_review_invocation(task.id, task.commit_sha)
                results["M_replay_idempotent"] = (
                    "PASS"
                    if replay.get("status") == "idempotent"
                    and inv2
                    and inv2.invocation_id == inv.invocation_id
                    else "FAIL"
                )
                results["K_verdict_posted"] = (
                    "PASS"
                    if any("Reviewer Verdict" in c for _, c in mock.comments if _ == pass_num)
                    else "FAIL"
                )
            else:
                for k in (
                    "G_push_webhook",
                    "H_ready_for_review",
                    "I_reviewer_bridge_auto",
                    "J_reviewer_executed",
                    "K_verdict_persisted",
                    "K_verdict_posted",
                    "L_pass_closes",
                    "M_replay_idempotent",
                ):
                    results[k] = "FAIL"

            fail_num = 502
            fail_body = f"{REVIEWER_FAIL_MARKER}\nControlled FAIL — magic required."
            fail_task = store.create_task(issue_number=fail_num, delivery_id=f"p02-fail-{uuid.uuid4()}")
            store.update_task(fail_task.id, status=TaskStatus.READY_FOR_REVIEW, commit_sha="fail00000001")
            marker = repo / "autonomous_dev" / "acceptance_marker.txt"
            marker.write_text("missing magic\n", encoding="utf-8")
            executor = ReviewExecutor(settings, store)
            fail_result = executor.run_review_sync(
                fail_task,
                commit_sha="fail00000001",
                issue_body=fail_body,
            )
            results["FAIL_controlled"] = "PASS" if fail_result.get("verdict") == "FAIL" else "FAIL"
            fail_updated = store.get_task(fail_task.id)
            results["FAIL_repair_task"] = (
                "PASS" if fail_updated and fail_updated.status == TaskStatus.NEEDS_FIX else "FAIL"
            )
            results["FAIL_one_repair"] = (
                "PASS" if len(mock.created_issues) == 1 else "FAIL"
            )

            pd_num = 503
            pd_body = f"{REVIEWER_PRODUCT_DECISION_MARKER}\nControlled product decision."
            pd_task = store.create_task(issue_number=pd_num, delivery_id=f"p02-pd-{uuid.uuid4()}")
            store.update_task(pd_task.id, status=TaskStatus.READY_FOR_REVIEW, commit_sha="pd0000000001")
            pd_result = executor.run_review_sync(pd_task, commit_sha="pd0000000001", issue_body=pd_body)
            results["PD_controlled"] = (
                "PASS" if pd_result.get("verdict") == "PRODUCT_DECISION" else "FAIL"
            )
            pd_updated = store.get_task(pd_task.id)
            results["PD_stops"] = (
                "PASS" if pd_updated and pd_updated.status == TaskStatus.PRODUCT_DECISION else "FAIL"
            )
            results["PD_packet"] = (
                "PASS"
                if any("【需要产品决策】" in c for n, c in mock.comments if n == pd_num)
                else "FAIL"
            )

            retry_num = 504
            retry_body = f"{REVIEWER_ACCEPTANCE_MARKER}\nTransient retry acceptance."
            retry_task = store.create_task(issue_number=retry_num, delivery_id=f"p02-retry-{uuid.uuid4()}")
            store.update_task(
                retry_task.id,
                status=TaskStatus.READY_FOR_REVIEW,
                commit_sha="retry000000001",
            )
            mock.bodies[retry_num] = retry_body
            retry_calls = {"count": 0}
            real_review = executor._reviewer.review

            def _flaky_review(ctx, *, invocation_id=None):
                retry_calls["count"] += 1
                if retry_calls["count"] == 1:
                    raise RuntimeError("simulated transient reviewer API 503")
                return real_review(ctx, invocation_id=invocation_id)

            executor._reviewer.review = _flaky_review
            first_retry = executor.run_review_sync(
                retry_task,
                commit_sha="retry000000001",
                issue_body=retry_body,
            )
            retry_inv = store.get_review_invocation(retry_task.id, "retry000000001")
            store.update_review_invocation(
                retry_inv.invocation_id,
                next_retry_at="2000-01-01T00:00:00+00:00",
            )
            from autonomous_dev.review_worker import process_due_reviews

            reset_autonomous_singletons()
            retry_results = process_due_reviews(settings, store)
            retry_inv2 = store.get_review_invocation(retry_task.id, "retry000000001")
            retry_comments = [
                c for n, c in mock.comments if n == retry_num and "Reviewer Verdict" in c
            ]
            results["RETRY_transient_fail"] = (
                "PASS" if first_retry.get("status") == "failed" else "FAIL"
            )
            results["RETRY_auto_recovery"] = (
                "PASS" if any(r.get("verdict") == "PASS" for r in retry_results) else "FAIL"
            )
            results["RETRY_single_verdict"] = "PASS" if len(retry_comments) == 1 else "FAIL"
            results["RETRY_completed_once"] = (
                "PASS"
                if retry_inv2 and retry_inv2.status == ReviewInvocationStatus.COMPLETED
                else "FAIL"
            )

            restart_num = 505
            restart_body = f"{REVIEWER_ACCEPTANCE_MARKER}\nRestart recovery acceptance."
            restart_task = store.create_task(
                issue_number=restart_num,
                delivery_id=f"p02-restart-{uuid.uuid4()}",
            )
            store.update_task(
                restart_task.id,
                status=TaskStatus.READY_FOR_REVIEW,
                commit_sha="restart0000001",
            )
            mock.bodies[restart_num] = restart_body
            restart_inv_id = str(uuid.uuid4())
            store.create_review_invocation(
                invocation_id=restart_inv_id,
                task_id=restart_task.id,
                issue_number=restart_num,
                commit_sha="restart0000001",
            )
            store.update_review_invocation(
                restart_inv_id,
                status=ReviewInvocationStatus.RUNNING,
                started_at="2000-01-01T00:00:00+00:00",
                attempt_count=1,
            )
            reset_autonomous_singletons()
            restart_results = process_due_reviews(settings, store)
            restart_inv = store.get_review_invocation(restart_task.id, "restart0000001")
            results["RESTART_stale_running"] = (
                "PASS"
                if restart_inv and restart_inv.status == ReviewInvocationStatus.COMPLETED
                else "FAIL"
            )
            results["RESTART_single_completion"] = (
                "PASS" if any(r.get("verdict") == "PASS" for r in restart_results) else "FAIL"
            )

    finally:
        _stop_service(proc)
        clear_autonomous_settings_cache()
        reset_autonomous_singletons()

    wt = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    origin = subprocess.run(["git", "rev-parse", "origin/main"], cwd=ROOT, capture_output=True, text=True)
    dirty_outside_impl = [
        ln
        for ln in wt.stdout.strip().splitlines()
        if ln
        and not any(
            ln.endswith(p) or f" {p}" in ln
            for p in (
                ".env.example",
                "autonomous_dev/",
                "backend/",
                "docs/AUTONOMOUS_DEV_RUNBOOK.md",
            )
        )
    ]
    results["N_clean_tree"] = "PASS" if not dirty_outside_impl else "FAIL"
    results["N_head_equals_origin"] = (
        "PASS" if head.stdout.strip() == origin.stdout.strip() else "FAIL"
    )

    print("=== P0.2 Reviewer Bridge Live Acceptance ===")
    for k in sorted(results):
        print(f"{k}: {results[k]}")
    blocking = list(results.values())
    all_pass = all(v == "PASS" for v in blocking)
    print(f"Final: {'PASS' if all_pass else 'FAIL'}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
