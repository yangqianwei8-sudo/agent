"""P0 autonomous dev infrastructure tests."""

from __future__ import annotations

import hashlib
import hmac
import json
import subprocess
from pathlib import Path

import pytest
from autonomous_dev.app import reset_autonomous_singletons
from autonomous_dev.config import AutonomousDevSettings, clear_autonomous_settings_cache
from autonomous_dev.github_webhook import WebhookVerificationError, verify_github_signature
from autonomous_dev.review_bridge import ReviewBridge
from autonomous_dev.state import StateStore, TaskStatus
from autonomous_dev.task_router import TaskRouter
from autonomous_dev.worker import (
    CURSOR_RUNTIME_ACCEPTANCE_MARKER,
    CURSOR_RUNTIME_OK_MARKER,
    PRODUCT_DECISION_MARKER,
    Worker,
)
from fastapi.testclient import TestClient

from backend.main import app


@pytest.fixture(autouse=True)
def _reset_autonomous_singletons():
    clear_autonomous_settings_cache()
    reset_autonomous_singletons()
    yield
    clear_autonomous_settings_cache()
    reset_autonomous_singletons()


@pytest.fixture
def infra_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    clear_autonomous_settings_cache()
    reset_autonomous_singletons()
    db = tmp_path / "state.db"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "autonomous_dev").mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    (repo / "README.md").write_text("test\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    monkeypatch.setenv("AUTONOMOUS_DEV_ENABLED", "true")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "test-secret")
    monkeypatch.setenv("AUTONOMOUS_WORKER_MODE", "deterministic")
    monkeypatch.setenv("AUTONOMOUS_REPO_ROOT", str(repo))
    monkeypatch.setenv("AUTONOMOUS_STATE_DB_PATH", str(db))
    clear_autonomous_settings_cache()
    reset_autonomous_singletons()
    yield repo, db
    clear_autonomous_settings_cache()
    reset_autonomous_singletons()


def _sign(body: bytes, secret: str = "test-secret") -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _issue_payload(*, number: int = 2, labels: list[str] | None = None, body: str = "") -> dict:
    label_objs = [{"name": n} for n in (labels or ["cursor-task", "current-task"])]
    return {
        "action": "labeled",
        "issue": {
            "number": number,
            "state": "open",
            "body": body,
            "labels": label_objs,
        },
    }


def test_webhook_signature_verification(infra_env):
    body = b'{"ok":true}'
    verify_github_signature(body, _sign(body), "test-secret")
    with pytest.raises(WebhookVerificationError):
        verify_github_signature(body, "sha256=deadbeef", "test-secret")


def test_duplicate_delivery_idempotency(infra_env):
    client = TestClient(app)
    payload = _issue_payload()
    body = json.dumps(payload).encode()
    headers = {
        "X-GitHub-Event": "issues",
        "X-GitHub-Delivery": "dup-001",
        "X-Hub-Signature-256": _sign(body),
        "Content-Type": "application/json",
    }
    r1 = client.post("/webhooks/github", content=body, headers=headers)
    r2 = client.post("/webhooks/github", content=body, headers=headers)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r2.json()["status"] == "duplicate"


def test_invalid_signature_rejected(infra_env):
    client = TestClient(app)
    body = json.dumps(_issue_payload()).encode()
    res = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "issues",
            "X-GitHub-Delivery": "bad-sig-1",
            "X-Hub-Signature-256": "sha256=invalid",
        },
    )
    assert res.status_code == 401


def test_issue_routing_starts_worker(infra_env):
    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    router_svc = TaskRouter(settings, store)
    payload = _issue_payload(number=42)
    result = router_svc.handle(
        event_type="issues",
        action="labeled",
        delivery_id="route-001",
        payload=payload,
    )
    assert result["status"] == "worker_started"
    assert store.is_locked() or store.get_task_by_issue(42) is not None


def test_single_current_task_lock(infra_env):
    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    router_svc = TaskRouter(settings, store)
    task = store.create_task(issue_number=1, delivery_id="t1")
    assert store.try_acquire_lock(1, task.id)
    payload = _issue_payload(number=99)
    result = router_svc.handle(
        event_type="issues",
        action="labeled",
        delivery_id="route-002",
        payload=payload,
    )
    assert result["status"] == "ignored"
    assert result["reason"] == "concurrent worker blocked"


def test_push_correlation(infra_env):
    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    router_svc = TaskRouter(settings, store)
    task = store.create_task(issue_number=7, delivery_id="push-task")
    store.update_task(task.id, status=TaskStatus.RUNNING, commit_sha="abc123def456")
    result = router_svc.handle(
        event_type="push",
        action=None,
        delivery_id="push-001",
        payload={"ref": "refs/heads/main", "after": "abc123def4567890"},
    )
    assert result["status"] == "ready_for_review"
    updated = store.get_task(task.id)
    assert updated is not None
    assert updated.status == TaskStatus.READY_FOR_REVIEW


def test_product_decision_pause(infra_env, monkeypatch: pytest.MonkeyPatch):
    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    worker = Worker(settings, store, repo_root=repo)

    def fake_tests() -> None:
        return None

    monkeypatch.setattr(worker, "_run_tests", fake_tests)
    monkeypatch.setattr(worker, "_git_fetch", lambda: None)
    monkeypatch.setattr(worker, "_ensure_clean_or_resolve", lambda: None)

    task = store.create_task(issue_number=5, delivery_id="pd-1")
    store.try_acquire_lock(5, task.id)
    result = worker.run_task(
        task,
        issue_body=f"Need owner input {PRODUCT_DECISION_MARKER}",
    )
    assert result.status == TaskStatus.PRODUCT_DECISION
    assert result.product_decision is not None


def test_worker_git_completion_verification(infra_env, monkeypatch: pytest.MonkeyPatch):
    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    worker = Worker(settings, store, repo_root=repo)

    monkeypatch.setattr(worker, "_run_tests", lambda: None)
    monkeypatch.setattr(worker, "_git_fetch", lambda: None)
    monkeypatch.setattr(worker, "_ensure_clean_or_resolve", lambda: None)

    task = store.create_task(issue_number=3, delivery_id="git-1")
    store.try_acquire_lock(3, task.id)
    result = worker.run_task(task, issue_body="harmless")
    assert result.status == TaskStatus.RUNNING
    assert result.commit_sha is not None
    marker = repo / "autonomous_dev" / "acceptance_marker.txt"
    assert marker.exists()


def test_state_transitions(infra_env):
    repo, db = infra_env
    store = StateStore(db)
    task = store.create_task(issue_number=10, delivery_id="st-1")
    assert task.status == TaskStatus.QUEUED
    running = store.update_task(task.id, status=TaskStatus.RUNNING)
    assert running.status == TaskStatus.RUNNING
    done = store.update_task(
        running.id,
        status=TaskStatus.READY_FOR_REVIEW,
        commit_sha="deadbeef",
    )
    assert done.status == TaskStatus.READY_FOR_REVIEW


def test_review_bridge_adapter(infra_env):
    repo, db = infra_env
    store = StateStore(db)
    task = store.create_task(issue_number=11, delivery_id="rb-1")
    task = store.update_task(task.id, status=TaskStatus.READY_FOR_REVIEW, commit_sha="abc")
    bridge = ReviewBridge()
    results = bridge.notify_ready_for_review(task, commit_sha="abc123")
    assert results
    assert any(r.triggered for r in results)


def test_healthz_endpoint(infra_env):
    client = TestClient(app)
    res = client.get("/healthz")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_cursor_model_from_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CURSOR_MODEL", "custom-model-x")
    clear_autonomous_settings_cache()
    settings = AutonomousDevSettings()
    assert settings.cursor_model == "custom-model-x"


def test_cursor_sdk_config_validation_missing_api_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AUTONOMOUS_WORKER_MODE", "cursor_sdk")
    monkeypatch.setenv("CURSOR_API_KEY", "")
    monkeypatch.setenv("CURSOR_MODEL", "composer-2")
    clear_autonomous_settings_cache()
    settings = AutonomousDevSettings()
    with pytest.raises(RuntimeError, match="CURSOR_API_KEY"):
        settings.validate_cursor_sdk_config()


def test_cursor_sdk_config_validation_missing_model(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AUTONOMOUS_WORKER_MODE", "cursor_sdk")
    monkeypatch.setenv("CURSOR_API_KEY", "test-key")
    monkeypatch.setenv("CURSOR_MODEL", "   ")
    clear_autonomous_settings_cache()
    settings = AutonomousDevSettings()
    with pytest.raises(RuntimeError, match="CURSOR_MODEL"):
        settings.validate_cursor_sdk_config()


def test_deterministic_mode_skips_cursor_validation(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AUTONOMOUS_WORKER_MODE", "deterministic")
    monkeypatch.setenv("CURSOR_API_KEY", "")
    monkeypatch.setenv("CURSOR_MODEL", "")
    clear_autonomous_settings_cache()
    settings = AutonomousDevSettings()
    settings.validate_cursor_sdk_config()


def test_cursor_sdk_passes_model_to_agent_options(
    infra_env, monkeypatch: pytest.MonkeyPatch
):
    repo, db = infra_env
    monkeypatch.setenv("AUTONOMOUS_WORKER_MODE", "cursor_sdk")
    monkeypatch.setenv("CURSOR_API_KEY", "test-cursor-key")
    monkeypatch.setenv("CURSOR_MODEL", "composer-2-test")
    clear_autonomous_settings_cache()
    settings = AutonomousDevSettings()
    store = StateStore(db)
    worker = Worker(settings, store, repo_root=repo)

    captured: dict[str, str] = {}

    class FakeResult:
        status = "completed"
        result = CURSOR_RUNTIME_OK_MARKER

    def fake_prompt(prompt, options):
        captured["model"] = options.model
        captured["api_key"] = options.api_key
        return FakeResult()

    monkeypatch.setattr("cursor_sdk.Agent.prompt", fake_prompt)

    task = store.create_task(issue_number=99, delivery_id="cursor-model-1")
    store.try_acquire_lock(99, task.id)
    result = worker.run_task(
        task,
        issue_body=f"{CURSOR_RUNTIME_ACCEPTANCE_MARKER} connectivity check",
    )
    assert captured["model"] == "composer-2-test"
    assert captured["api_key"] == "test-cursor-key"
    assert result.commit_sha == CURSOR_RUNTIME_OK_MARKER


def test_cursor_sdk_missing_api_key_fail_closed(infra_env, monkeypatch: pytest.MonkeyPatch):
    repo, db = infra_env
    monkeypatch.setenv("AUTONOMOUS_WORKER_MODE", "cursor_sdk")
    monkeypatch.setenv("CURSOR_API_KEY", "")
    monkeypatch.setenv("CURSOR_MODEL", "composer-2")
    clear_autonomous_settings_cache()
    settings = AutonomousDevSettings()
    store = StateStore(db)
    worker = Worker(settings, store, repo_root=repo)

    task = store.create_task(issue_number=100, delivery_id="cursor-no-key")
    store.try_acquire_lock(100, task.id)
    result = worker.run_task(task, issue_body="test")
    assert result.status == TaskStatus.NEEDS_FIX
    assert "CURSOR_API_KEY" in (result.error or "")


def test_cursor_sdk_missing_model_fail_closed(infra_env, monkeypatch: pytest.MonkeyPatch):
    repo, db = infra_env
    monkeypatch.setenv("AUTONOMOUS_WORKER_MODE", "cursor_sdk")
    monkeypatch.setenv("CURSOR_API_KEY", "test-key")
    monkeypatch.setenv("CURSOR_MODEL", "")
    clear_autonomous_settings_cache()
    settings = AutonomousDevSettings()
    store = StateStore(db)
    worker = Worker(settings, store, repo_root=repo)

    task = store.create_task(issue_number=101, delivery_id="cursor-no-model")
    store.try_acquire_lock(101, task.id)
    result = worker.run_task(task, issue_body="test")
    assert result.status == TaskStatus.NEEDS_FIX
    assert "CURSOR_MODEL" in (result.error or "")


def test_cursor_sdk_secrets_not_in_error(infra_env, monkeypatch: pytest.MonkeyPatch):
    repo, db = infra_env
    secret = "super-secret-cursor-key-xyz"
    monkeypatch.setenv("AUTONOMOUS_WORKER_MODE", "cursor_sdk")
    monkeypatch.setenv("CURSOR_API_KEY", secret)
    monkeypatch.setenv("CURSOR_MODEL", "composer-2")
    clear_autonomous_settings_cache()
    settings = AutonomousDevSettings()
    store = StateStore(db)
    worker = Worker(settings, store, repo_root=repo)

    def fake_prompt(prompt, options):
        raise RuntimeError("simulated agent failure")

    monkeypatch.setattr("cursor_sdk.Agent.prompt", fake_prompt)

    task = store.create_task(issue_number=102, delivery_id="cursor-secret")
    store.try_acquire_lock(102, task.id)
    result = worker.run_task(
        task,
        issue_body=f"{CURSOR_RUNTIME_ACCEPTANCE_MARKER} secret check",
    )
    assert result.status == TaskStatus.NEEDS_FIX
    assert secret not in (result.error or "")
    updated = store.get_task(task.id)
    assert updated is not None
    assert secret not in (updated.error or "")
