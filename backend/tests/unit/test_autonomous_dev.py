"""P0 autonomous dev infrastructure tests."""

from __future__ import annotations

import hashlib
import hmac
import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from autonomous_dev.app import reset_autonomous_singletons
from autonomous_dev.config import AutonomousDevSettings, clear_autonomous_settings_cache
from autonomous_dev.execution_identity import compute_execution_key
from autonomous_dev.github_client import LABEL_READY_FOR_REVIEW, GitHubClient, GitHubClientError
from autonomous_dev.github_webhook import WebhookVerificationError, verify_github_signature
from autonomous_dev.review_bridge import (
    OpenAIApiReviewAdapter,
    ReviewBridge,
    ReviewTriggerAdapter,
    ReviewTriggerResult,
)
from autonomous_dev.review_executor import ReviewExecutor
from autonomous_dev.reviewer_service import (
    REVIEWER_ACCEPTANCE_MARKER,
    REVIEWER_FAIL_MARKER,
    REVIEWER_PRODUCT_DECISION_MARKER,
    ReviewerService,
)
from autonomous_dev.state import (
    DeliveryStatus,
    ReviewInvocationStatus,
    ReviewVerdict,
    StateStore,
    TaskStatus,
)
from autonomous_dev.task_router import TaskRouter
from autonomous_dev.worker import (
    CURSOR_RUNTIME_ACCEPTANCE_MARKER,
    CURSOR_RUNTIME_OK_MARKER,
    PRODUCT_DECISION_MARKER,
    Worker,
)
from fastapi.testclient import TestClient

from backend.main import app


class _MockGitHubClient:
    def __init__(self, *args, **kwargs) -> None:
        self.labels: dict[int, set[str]] = {}
        self.comments: list[tuple[int, str]] = []
        self.bodies: dict[int, str] = {}
        self.created_issues: list[dict] = []
        self.closed: list[int] = []
        self._next_issue = 9000

    def sync_worker_running(self, issue_number: int) -> None:
        self.labels[issue_number] = {"cursor-task", "worker-running"}

    def sync_ready_for_review(self, issue_number: int) -> None:
        self.labels[issue_number] = {"cursor-task", LABEL_READY_FOR_REVIEW}

    def sync_needs_fix(self, issue_number: int) -> None:
        self.labels[issue_number] = {"cursor-task", "needs-fix"}

    def sync_product_decision(self, issue_number: int) -> None:
        self.labels[issue_number] = {"cursor-task", "product-decision"}

    def sync_completed(self, issue_number: int) -> None:
        self.labels[issue_number] = {"cursor-task", "completed"}

    def add_comment(self, issue_number: int, body: str) -> None:
        self.comments.append((issue_number, body))

    def set_issue_labels(self, issue_number: int, labels: set[str]) -> None:
        self.labels[issue_number] = set(labels)

    def get_issue_labels(self, issue_number: int) -> set[str]:
        return self.labels.get(issue_number, set())

    def get_issue_body(self, issue_number: int) -> str:
        return self.bodies.get(issue_number, "")

    def close_issue(self, issue_number: int, *, reason: str = "") -> None:
        self.closed.append(issue_number)

    def create_issue(self, *, title: str, body: str, labels: set[str] | None = None) -> int:
        num = self._next_issue
        self._next_issue += 1
        self.created_issues.append({"number": num, "title": title, "body": body, "labels": labels})
        if labels:
            self.labels[num] = set(labels)
        self.bodies[num] = body
        return num

    def update_issue_body(self, issue_number: int, body: str) -> None:
        self.bodies[issue_number] = body

    def remove_label(self, issue_number: int, label: str) -> None:
        self.labels.get(issue_number, set()).discard(label)

    def find_open_issue_by_title_prefix(self, prefix: str) -> int | None:
        for issue in self.created_issues:
            if prefix in issue["title"]:
                return issue["number"]
        return None


@pytest.fixture(autouse=True)
def _mock_github_client(monkeypatch: pytest.MonkeyPatch):
    mock = _MockGitHubClient()

    def _factory(*args, **kwargs):
        return mock

    monkeypatch.setattr("autonomous_dev.worker.GitHubClient", _factory)
    monkeypatch.setattr("autonomous_dev.task_router.GitHubClient", _factory)
    monkeypatch.setattr("autonomous_dev.review_bridge.GitHubClient", _factory)
    monkeypatch.setattr("autonomous_dev.review_executor.GitHubClient", _factory)
    return mock


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
            "updated_at": "2026-09-11T02:00:00Z",
            "created_at": "2026-09-10T12:00:00Z",
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

    class FakeAdapter(ReviewTriggerAdapter):
        def trigger(self, task, *, commit_sha: str) -> ReviewTriggerResult:
            return ReviewTriggerResult(triggered=True, adapter="fake", detail="ok")

    bridge = ReviewBridge(adapters=[FakeAdapter()])
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


def test_execution_key_from_payload(infra_env):
    repo, db = infra_env
    settings = AutonomousDevSettings()
    payload = _issue_payload(number=7)
    key = compute_execution_key(settings, payload)
    assert key == f"{settings.github_repo}#7#2026-09-11T02:00:00Z"


def test_duplicate_active_execution_blocked(infra_env):
    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    router = TaskRouter(settings, store)
    payload = _issue_payload(number=55)
    execution_key = compute_execution_key(settings, payload)
    store.create_task(
        issue_number=55,
        delivery_id="first",
        execution_key=execution_key,
        status=TaskStatus.RUNNING,
    )
    result = router.handle(
        event_type="issues",
        action="labeled",
        delivery_id="second-delivery",
        payload=payload,
    )
    assert result["status"] == "ignored"
    assert result["reason"] == "duplicate active execution"


def test_lease_blocks_second_worker(infra_env):
    repo, db = infra_env
    store = StateStore(db)
    assert store.try_acquire_lease(1, 10, owner="worker-10", ttl_seconds=60)
    assert not store.try_acquire_lease(2, 11, owner="worker-11", ttl_seconds=60)
    store.release_lease("worker-10")
    assert store.try_acquire_lease(2, 11, owner="worker-11", ttl_seconds=60)


def test_stale_lease_recovery(infra_env):
    repo, db = infra_env
    store = StateStore(db)
    assert store.try_acquire_lease(1, 10, owner="worker-10", ttl_seconds=1)
    import time

    time.sleep(1.1)
    assert store.recover_stale_lease()
    assert not store.is_locked()
    assert store.try_acquire_lease(2, 11, owner="worker-11", ttl_seconds=60)


def test_stale_lease_fails_running_task(infra_env):
    """Stale lease recovery atomically fails the linked RUNNING execution."""
    repo, db = infra_env
    store = StateStore(db)
    running = store.create_task(
        issue_number=20,
        delivery_id="stale-run",
        execution_key="repo#20#gen1",
        status=TaskStatus.RUNNING,
    )
    assert store.try_acquire_lease(20, running.id, owner="worker-stale", ttl_seconds=1)
    import time

    time.sleep(1.1)
    assert store.recover_stale_lease()
    updated = store.get_task(running.id)
    assert updated.status == TaskStatus.FAILED
    assert updated.error == "stale worker lease recovered"
    assert store.get_active_execution("repo#20#gen1") is None


def test_heartbeat_extends_lease(infra_env):
    repo, db = infra_env
    store = StateStore(db)
    assert store.try_acquire_lease(1, 10, owner="worker-10", ttl_seconds=2)
    import time

    time.sleep(1)
    assert store.heartbeat_lease("worker-10", ttl_seconds=5)
    time.sleep(1.5)
    assert store.is_locked()


def test_github_client_fail_closed_without_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GITHUB_TOKEN", "")
    monkeypatch.setenv("GITHUB_AUTH_MODE", "pat")
    clear_autonomous_settings_cache()
    client = GitHubClient(AutonomousDevSettings())
    with pytest.raises(GitHubClientError, match="not configured"):
        client.set_issue_labels(1, {"cursor-task"})


def test_push_correlation_no_fallback(infra_env):
    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    router = TaskRouter(settings, store)
    store.create_task(issue_number=8, delivery_id="unrelated", status=TaskStatus.RUNNING)
    result = router.handle(
        event_type="push",
        action=None,
        delivery_id="push-unrelated",
        payload={"ref": "refs/heads/main", "after": "deadbeef999999"},
    )
    assert result["status"] == "ignored"
    assert result["reason"] == "no correlated task"


def test_push_correlation_via_commit_message_before_db_commit(infra_env):
    """Push may arrive before worker records commit_sha — correlate via issue # in message."""
    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    router = TaskRouter(settings, store)
    task = store.create_task(issue_number=10, delivery_id="race-task")
    store.update_task(task.id, status=TaskStatus.RUNNING)
    result = router.handle(
        event_type="push",
        action=None,
        delivery_id="push-race-001",
        payload={
            "ref": "refs/heads/main",
            "after": "abc123def4567890",
            "commits": [
                {"message": "fix: production acceptance for issue #10"},
            ],
        },
    )
    assert result["status"] == "ready_for_review"
    updated = store.get_task(task.id)
    assert updated is not None
    assert updated.status == TaskStatus.READY_FOR_REVIEW
    assert updated.commit_sha == "abc123def4567890"


def test_lease_acquire_failed_marks_task_failed(infra_env, monkeypatch: pytest.MonkeyPatch):
    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    router = TaskRouter(settings, store)
    monkeypatch.setattr(store, "try_acquire_lease", lambda *args, **kwargs: False)
    payload = _issue_payload(number=50)
    result = router.handle(
        event_type="issues",
        action="labeled",
        delivery_id="lease-fail-001",
        payload=payload,
    )
    assert result["status"] == "ignored"
    assert result["reason"] == "lease acquire failed"
    task = store.get_task_by_issue(50)
    assert task is not None
    assert task.status == TaskStatus.FAILED


def test_push_correlation_idempotent(infra_env):
    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    router = TaskRouter(settings, store)
    task = store.create_task(issue_number=9, delivery_id="push-idem")
    store.update_task(task.id, status=TaskStatus.READY_FOR_REVIEW, commit_sha="abc123def456")
    result = router.handle(
        event_type="push",
        action=None,
        delivery_id="push-idem-dup",
        payload={"ref": "refs/heads/main", "after": "abc123def4567890"},
    )
    assert result["status"] == "ready_for_review"
    assert result.get("idempotent") is True


def test_reviewer_pass_completes_task(infra_env):
    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    router = TaskRouter(settings, store)
    task = store.create_task(issue_number=12, delivery_id="review-pass")
    store.update_task(task.id, status=TaskStatus.READY_FOR_REVIEW, commit_sha="sha")
    result = router.seal_review_pass(12)
    assert result["status"] == "completed"
    updated = store.get_task(task.id)
    assert updated is not None
    assert updated.status == TaskStatus.COMPLETED


def test_labeled_existing_open_issue_activates(infra_env):
    """Router must activate when current-task is added to an already-open issue."""
    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    router_svc = TaskRouter(settings, store)
    store.create_task(
        issue_number=77,
        delivery_id="prior-run",
        execution_key=f"{settings.github_repo}#77#2026-09-10T12:00:00Z",
        status=TaskStatus.COMPLETED,
    )
    payload = _issue_payload(number=77, labels=["cursor-task", "current-task"])
    payload["issue"]["updated_at"] = "2026-09-11T06:00:00Z"
    result = router_svc.handle(
        event_type="issues",
        action="labeled",
        delivery_id="labeled-existing-77",
        payload=payload,
    )
    assert result["status"] == "worker_started"
    assert result["issue_number"] == 77


def test_failed_task_status_roundtrip(infra_env):
    repo, db = infra_env
    store = StateStore(db)
    task = store.create_task(issue_number=88, delivery_id="failed-1")
    updated = store.update_task(task.id, status=TaskStatus.FAILED, error="simulated")
    assert updated.status == TaskStatus.FAILED
    fetched = store.get_task_by_issue(88)
    assert fetched is not None
    assert fetched.status == TaskStatus.FAILED


def test_review_trigger_dedup_metadata(infra_env):
    repo, db = infra_env
    store = StateStore(db)
    task = store.create_task(issue_number=13, delivery_id="review-meta")
    store.record_review_trigger(task.id)
    store.record_review_trigger(task.id)
    ts, count = store.get_review_trigger(task.id)
    assert ts is not None
    assert count == 2


def test_reviewer_pass_completes_task_via_executor(infra_env, _mock_github_client):
    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    marker = repo / "autonomous_dev" / "acceptance_marker.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("acceptance ok\n", encoding="utf-8")
    task = store.create_task(issue_number=20, delivery_id="rev-pass")
    store.update_task(task.id, status=TaskStatus.READY_FOR_REVIEW, commit_sha="abc123def456")
    _mock_github_client.bodies[20] = f"{REVIEWER_ACCEPTANCE_MARKER}\nHarmless acceptance."
    executor = ReviewExecutor(settings, store)
    result = executor.run_review_sync(
        task,
        commit_sha="abc123def456",
        issue_body=_mock_github_client.bodies[20],
    )
    assert result["verdict"] == "PASS"
    updated = store.get_task(task.id)
    assert updated is not None
    assert updated.status == TaskStatus.COMPLETED
    inv = store.get_review_invocation(task.id, "abc123def456")
    assert inv is not None
    assert inv.verdict == ReviewVerdict.PASS
    assert inv.status == ReviewInvocationStatus.COMPLETED


def test_reviewer_fail_creates_repair_issue(infra_env, _mock_github_client):
    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    marker = repo / "autonomous_dev" / "acceptance_marker.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("missing magic\n", encoding="utf-8")
    task = store.create_task(issue_number=21, delivery_id="rev-fail")
    store.update_task(task.id, status=TaskStatus.READY_FOR_REVIEW, commit_sha="fail12345678")
    body = f"{REVIEWER_FAIL_MARKER}\nControlled fail acceptance."
    _mock_github_client.bodies[21] = body
    executor = ReviewExecutor(settings, store)
    result = executor.run_review_sync(task, commit_sha="fail12345678", issue_body=body)
    assert result["verdict"] == "FAIL"
    updated = store.get_task(task.id)
    assert updated is not None
    assert updated.status == TaskStatus.NEEDS_FIX
    assert len(_mock_github_client.created_issues) == 1
    repair = _mock_github_client.created_issues[0]
    assert repair["labels"] == {"cursor-task", "current-task"}


def test_reviewer_product_decision_packet(infra_env, _mock_github_client):
    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    task = store.create_task(issue_number=22, delivery_id="rev-pd")
    store.update_task(task.id, status=TaskStatus.READY_FOR_REVIEW, commit_sha="pd1234567890")
    body = f"{REVIEWER_PRODUCT_DECISION_MARKER}\nProduct ambiguity test."
    _mock_github_client.bodies[22] = body
    executor = ReviewExecutor(settings, store)
    result = executor.run_review_sync(task, commit_sha="pd1234567890", issue_body=body)
    assert result["verdict"] == "PRODUCT_DECISION"
    updated = store.get_task(task.id)
    assert updated is not None
    assert updated.status == TaskStatus.PRODUCT_DECISION
    comments = [c for n, c in _mock_github_client.comments if n == 22]
    assert any("【需要产品决策】" in c for c in comments)


def test_reviewer_exactly_once_idempotent(infra_env, _mock_github_client):
    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    marker = repo / "autonomous_dev" / "acceptance_marker.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("ok\n", encoding="utf-8")
    task = store.create_task(issue_number=23, delivery_id="rev-idem")
    store.update_task(task.id, status=TaskStatus.READY_FOR_REVIEW, commit_sha="idem12345678")
    body = f"{REVIEWER_ACCEPTANCE_MARKER}\nAcceptance."
    _mock_github_client.bodies[23] = body
    executor = ReviewExecutor(settings, store)
    executor.run_review_sync(task, commit_sha="idem12345678", issue_body=body)
    first_count = len(_mock_github_client.created_issues)
    outcome = executor.schedule_review(task, commit_sha="idem12345678")
    assert outcome["status"] == "idempotent"
    assert len(_mock_github_client.created_issues) == first_count


def test_reviewer_lock_blocks_concurrent(infra_env):
    repo, db = infra_env
    store = StateStore(db)
    assert store.try_acquire_reviewer_lock(1, owner="r1", ttl_seconds=60)
    assert not store.try_acquire_reviewer_lock(2, owner="r2", ttl_seconds=60)
    store.release_reviewer_lock("r1")
    assert store.try_acquire_reviewer_lock(2, owner="r2", ttl_seconds=60)


def test_openai_adapter_credential_blocker(infra_env, monkeypatch: pytest.MonkeyPatch):
    repo, db = infra_env
    monkeypatch.setenv("AUTONOMOUS_WORKER_MODE", "cursor_sdk")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("LLM_API_KEY", "")
    clear_autonomous_settings_cache()
    settings = AutonomousDevSettings()
    store = StateStore(db)
    task = store.create_task(issue_number=24, delivery_id="cred-block")
    store.update_task(task.id, status=TaskStatus.READY_FOR_REVIEW, commit_sha="cred12345678")
    adapter = OpenAIApiReviewAdapter(settings, store)
    result = adapter.trigger(task, commit_sha="cred12345678")
    assert result.triggered is False
    assert "credential blocker" in result.detail.lower()


def test_llm_api_key_does_not_configure_reviewer(infra_env, monkeypatch: pytest.MonkeyPatch):
    """Worker LLM credentials must not substitute the independent OpenAI reviewer."""
    repo, db = infra_env
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("LLM_API_KEY", "sk-test-worker-only-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setenv("LLM_MODEL", "deepseek-chat")
    clear_autonomous_settings_cache()
    settings = AutonomousDevSettings()
    assert settings.resolve_reviewer_credentials() is None


def test_openai_adapter_schedules_in_deterministic_mode(infra_env):
    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    task = store.create_task(issue_number=25, delivery_id="sched")
    store.update_task(task.id, status=TaskStatus.READY_FOR_REVIEW, commit_sha="sch123456789")
    adapter = OpenAIApiReviewAdapter(settings, store)
    result = adapter.trigger(task, commit_sha="sch123456789")
    assert result.triggered is True
    assert result.adapter == "openai_api"


def test_push_triggers_reviewer_bridge(infra_env, _mock_github_client):
    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    marker = repo / "autonomous_dev" / "acceptance_marker.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("ok\n", encoding="utf-8")
    router = TaskRouter(settings, store)
    task = store.create_task(issue_number=26, delivery_id="push-rev")
    store.update_task(task.id, status=TaskStatus.RUNNING)
    _mock_github_client.bodies[26] = f"{REVIEWER_ACCEPTANCE_MARKER}\nPush review test."
    router.handle(
        event_type="push",
        action=None,
        delivery_id="push-rev-001",
        payload={
            "ref": "refs/heads/main",
            "after": "rev1234567890",
            "commits": [{"message": "chore: issue #26"}],
        },
    )
    import time

    for _ in range(20):
        inv = store.get_review_invocation(task.id, "rev1234567890")
        if inv and inv.status == ReviewInvocationStatus.COMPLETED:
            break
        time.sleep(0.1)
    inv = store.get_review_invocation(task.id, "rev1234567890")
    assert inv is not None
    assert inv.verdict == ReviewVerdict.PASS


def test_reviewer_service_deterministic_pass(infra_env):
    repo, db = infra_env
    settings = AutonomousDevSettings()
    svc = ReviewerService(settings, repo_root=repo)
    ctx = svc.gather_context(
        issue_number=1,
        issue_body=f"{REVIEWER_ACCEPTANCE_MARKER}\ntest",
        commit_sha="abc123",
    )
    result = svc.review(ctx)
    assert result.verdict == "PASS"


def test_reviewer_evidence_includes_pytest_and_ruff(infra_env, monkeypatch: pytest.MonkeyPatch):
    repo, db = infra_env
    settings = AutonomousDevSettings()
    svc = ReviewerService(settings, repo_root=repo)

    def fake_report(root, commit_sha, *, force=False):
        return {
            "commit_sha": commit_sha,
            "generated_at": "2026-09-11T00:00:00Z",
            "source": "test",
            "pytest_focused": {"passed": True, "command": "pytest focused", "output_tail": "ok"},
            "pytest_full": {"passed": True, "command": "pytest -q", "output_tail": "ok"},
            "ruff": {"passed": True, "command": "ruff check .", "output_tail": "ok"},
            "secret_scan": {"passed": True, "findings": []},
            "overall_passed": True,
        }

    monkeypatch.setattr(
        "autonomous_dev.reviewer_service.generate_acceptance_report",
        fake_report,
    )
    monkeypatch.setattr("autonomous_dev.reviewer_service.load_report", lambda *a, **k: None)
    evidence = svc._gather_test_evidence("abc123def456")
    assert "pytest_focused" in evidence
    assert "ruff" in evidence
    assert "secret_scan" in evidence
    assert "abc123def456" in evidence


def test_failed_review_becomes_retryable(
    infra_env, _mock_github_client, monkeypatch: pytest.MonkeyPatch
):
    repo, db = infra_env
    monkeypatch.setenv("REVIEW_RETRY_BACKOFF_SECONDS", "0")
    monkeypatch.setattr(
        "autonomous_dev.review_worker.schedule_review_processing",
        lambda *args, **kwargs: None,
    )
    clear_autonomous_settings_cache()
    settings = AutonomousDevSettings()
    store = StateStore(db)
    task = store.create_task(issue_number=30, delivery_id="retry-fail")
    store.update_task(task.id, status=TaskStatus.READY_FOR_REVIEW, commit_sha="retry1234567")
    invocation_id = "inv-retry-fail"
    store.create_review_invocation(
        invocation_id=invocation_id,
        task_id=task.id,
        issue_number=30,
        commit_sha="retry1234567",
    )
    store.update_review_invocation(
        invocation_id,
        status=ReviewInvocationStatus.FAILED,
        error="transient API 503",
        attempt_count=1,
        next_retry_at="2000-01-01T00:00:00+00:00",
    )
    executor = ReviewExecutor(settings, store)
    outcome = executor.schedule_review(task, commit_sha="retry1234567")
    assert outcome["status"] == "retry_scheduled"
    inv = store.get_review_invocation(task.id, "retry1234567")
    assert inv is not None
    assert inv.status == ReviewInvocationStatus.PENDING


def test_transient_failure_retries_to_single_verdict(
    infra_env, _mock_github_client, monkeypatch: pytest.MonkeyPatch
):
    repo, db = infra_env
    monkeypatch.setenv("REVIEW_RETRY_BACKOFF_SECONDS", "0")
    clear_autonomous_settings_cache()
    settings = AutonomousDevSettings()
    store = StateStore(db)
    marker = repo / "autonomous_dev" / "acceptance_marker.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("ok\n", encoding="utf-8")
    task = store.create_task(issue_number=31, delivery_id="transient-retry")
    store.update_task(task.id, status=TaskStatus.READY_FOR_REVIEW, commit_sha="trans12345678")
    body = f"{REVIEWER_ACCEPTANCE_MARKER}\nTransient retry test."
    _mock_github_client.bodies[31] = body

    calls = {"count": 0}
    real_review = ReviewerService.review

    def flaky_review(self, ctx, *, invocation_id=None):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("simulated transient reviewer API 503")
        return real_review(self, ctx, invocation_id=invocation_id)

    reviewer = ReviewerService(settings, repo_root=repo)
    monkeypatch.setattr(reviewer, "review", flaky_review.__get__(reviewer, ReviewerService))
    executor = ReviewExecutor(settings, store, reviewer=reviewer)

    first = executor.run_review_sync(task, commit_sha="trans12345678", issue_body=body)
    assert first["status"] == "failed"
    assert first.get("retryable") == "true"
    inv = store.get_review_invocation(task.id, "trans12345678")
    assert inv is not None
    assert inv.status == ReviewInvocationStatus.FAILED

    store.update_review_invocation(
        inv.invocation_id,
        next_retry_at="2000-01-01T00:00:00+00:00",
    )
    reset_autonomous_singletons()
    from autonomous_dev.review_worker import process_due_reviews

    results = process_due_reviews(settings, store)
    assert any(r.get("verdict") == "PASS" for r in results)
    inv2 = store.get_review_invocation(task.id, "trans12345678")
    assert inv2 is not None
    assert inv2.status == ReviewInvocationStatus.COMPLETED
    verdict_comments = [
        c for n, c in _mock_github_client.comments if n == 31 and "Reviewer Verdict" in c
    ]
    assert len(verdict_comments) == 1


def test_stale_running_review_recovered(
    infra_env, _mock_github_client, monkeypatch: pytest.MonkeyPatch
):
    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    marker = repo / "autonomous_dev" / "acceptance_marker.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("ok\n", encoding="utf-8")
    task = store.create_task(issue_number=32, delivery_id="stale-running")
    store.update_task(task.id, status=TaskStatus.READY_FOR_REVIEW, commit_sha="stale123456789")
    body = f"{REVIEWER_ACCEPTANCE_MARKER}\nStale recovery test."
    _mock_github_client.bodies[32] = body
    invocation_id = "inv-stale-running"
    store.create_review_invocation(
        invocation_id=invocation_id,
        task_id=task.id,
        issue_number=32,
        commit_sha="stale123456789",
    )
    store.update_review_invocation(
        invocation_id,
        status=ReviewInvocationStatus.RUNNING,
        started_at="2000-01-01T00:00:00+00:00",
        attempt_count=1,
    )
    from autonomous_dev.review_worker import process_due_reviews

    reset_autonomous_singletons()
    results = process_due_reviews(settings, store)
    assert results
    inv = store.get_review_invocation(task.id, "stale123456789")
    assert inv is not None
    assert inv.status == ReviewInvocationStatus.COMPLETED
    assert inv.verdict == ReviewVerdict.PASS


def test_review_worker_no_deadlock_on_transient_kick(
    infra_env, _mock_github_client, monkeypatch: pytest.MonkeyPatch
):
    """Transient kick re-entering process_due_reviews must not deadlock."""
    repo, db = infra_env
    monkeypatch.setenv("REVIEW_RETRY_BACKOFF_SECONDS", "0")
    clear_autonomous_settings_cache()
    settings = AutonomousDevSettings()
    store = StateStore(db)
    marker = repo / "autonomous_dev" / "acceptance_marker.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("ok\n", encoding="utf-8")
    task = store.create_task(issue_number=33, delivery_id="deadlock-kick")
    store.update_task(task.id, status=TaskStatus.READY_FOR_REVIEW, commit_sha="deadlock123456")
    body = f"{REVIEWER_ACCEPTANCE_MARKER}\nDeadlock kick test."
    _mock_github_client.bodies[33] = body

    calls = {"count": 0}
    real_review = ReviewerService.review

    def flaky_review(self, ctx, *, invocation_id=None):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("simulated transient reviewer API 503")
        return real_review(self, ctx, invocation_id=invocation_id)

    reviewer = ReviewerService(settings, repo_root=repo)
    monkeypatch.setattr(reviewer, "review", flaky_review.__get__(reviewer, ReviewerService))
    executor = ReviewExecutor(settings, store, reviewer=reviewer)
    reset_autonomous_singletons()
    monkeypatch.setattr(
        "autonomous_dev.review_worker._get_executor",
        lambda _s, _st: executor,
    )
    executor.schedule_review(task, commit_sha="deadlock123456")
    inv = store.get_review_invocation(task.id, "deadlock123456")
    assert inv is not None
    assert inv.status == ReviewInvocationStatus.COMPLETED
    assert inv.verdict == ReviewVerdict.PASS


def test_finalize_obsolete_review_invocations(infra_env):
    _, db = infra_env
    store = StateStore(db)
    task = store.create_task(issue_number=99001, delivery_id="fake-acceptance")
    store.update_task(task.id, status=TaskStatus.NEEDS_FIX, commit_sha="fake00000001")
    invocation_id = "inv-obsolete"
    store.create_review_invocation(
        invocation_id=invocation_id,
        task_id=task.id,
        issue_number=99001,
        commit_sha="fake00000001",
    )
    store.update_review_invocation(
        invocation_id,
        status=ReviewInvocationStatus.RUNNING,
        started_at="2000-01-01T00:00:00+00:00",
    )
    finalized = store.finalize_obsolete_review_invocations()
    assert invocation_id in finalized
    inv = store.get_review_invocation(task.id, "fake00000001")
    assert inv is not None
    assert inv.status == ReviewInvocationStatus.COMPLETED
    assert inv.verdict == ReviewVerdict.FAIL


def test_verdict_persisted_when_github_apply_fails(
    infra_env, _mock_github_client, monkeypatch: pytest.MonkeyPatch
):
    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    marker = repo / "autonomous_dev" / "acceptance_marker.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("ok\n", encoding="utf-8")
    task = store.create_task(issue_number=34, delivery_id="github-apply-fail")
    store.update_task(task.id, status=TaskStatus.READY_FOR_REVIEW, commit_sha="ghfail1234567")
    body = f"{REVIEWER_ACCEPTANCE_MARKER}\nGitHub apply fail test."
    _mock_github_client.bodies[34] = body

    def boom_sync_completed(self, issue_number: int) -> None:
        raise GitHubClientError("simulated GitHub 404")

    monkeypatch.setattr(
        "autonomous_dev.github_client.GitHubClient.sync_completed",
        boom_sync_completed,
    )
    executor = ReviewExecutor(settings, store)
    result = executor.run_review_sync(task, commit_sha="ghfail1234567", issue_body=body)
    assert result["status"] == "completed"
    assert result["verdict"] == "PASS"
    inv = store.get_review_invocation(task.id, "ghfail1234567")
    assert inv is not None
    assert inv.status == ReviewInvocationStatus.COMPLETED
    assert inv.verdict == ReviewVerdict.PASS


def _mock_github_issues(monkeypatch: pytest.MonkeyPatch, issues: list[dict]) -> None:
    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> list[dict]:
            return issues

    class _FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, *, headers, params):
            return _FakeResponse()

    monkeypatch.setattr("autonomous_dev.watchdog.httpx.Client", _FakeClient)
    monkeypatch.setattr("autonomous_dev.watchdog.resolve_github_token", lambda: "test-token")


def test_watchdog_recovers_failed_new_generation(infra_env, monkeypatch: pytest.MonkeyPatch):
    """FAILED prior task + fresh current-task generation -> starts exactly one Worker."""
    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    old_key = f"{settings.github_repo}#20#2026-09-10T12:00:00Z"
    store.create_task(
        issue_number=20,
        delivery_id="prior-failed",
        execution_key=old_key,
        status=TaskStatus.FAILED,
    )
    issue = {
        "number": 20,
        "state": "open",
        "body": "reactivated",
        "labels": [{"name": "cursor-task"}, {"name": "current-task"}],
        "updated_at": "2026-09-11T08:00:00Z",
        "created_at": "2026-09-10T12:00:00Z",
    }
    _mock_github_issues(monkeypatch, [issue])
    handle_calls: list[str] = []

    def _track_handle(self, **kwargs):
        handle_calls.append(kwargs["delivery_id"])
        return {"status": "worker_started", "task_id": 99, "issue_number": 20}

    monkeypatch.setattr(TaskRouter, "handle", _track_handle)
    from autonomous_dev.watchdog import _scan_current_tasks

    _scan_current_tasks(settings)
    assert len(handle_calls) == 1


def test_watchdog_same_generation_active_no_duplicate(infra_env, monkeypatch: pytest.MonkeyPatch):
    """Same generation active -> no duplicate Worker."""
    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    payload = _issue_payload(number=20)
    execution_key = compute_execution_key(settings, payload)
    store.create_task(
        issue_number=20,
        delivery_id="active-run",
        execution_key=execution_key,
        status=TaskStatus.RUNNING,
    )
    issue = payload["issue"]
    _mock_github_issues(monkeypatch, [issue])
    handle_calls: list[str] = []

    def _track_handle(self, **kwargs):
        handle_calls.append(kwargs["delivery_id"])
        return {"status": "worker_started"}

    monkeypatch.setattr(TaskRouter, "handle", _track_handle)
    from autonomous_dev.watchdog import _scan_current_tasks

    _scan_current_tasks(settings)
    assert handle_calls == []


def test_watchdog_stale_lease_recovers_current_task(infra_env, monkeypatch: pytest.MonkeyPatch):
    """Stale lease + current-task -> recovers once."""
    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    assert store.try_acquire_lease(20, 1, owner="stale-worker", ttl_seconds=1)
    import time

    time.sleep(1.1)
    issue = {
        "number": 20,
        "state": "open",
        "body": "recover me",
        "labels": [{"name": "cursor-task"}, {"name": "current-task"}],
        "updated_at": "2026-09-11T09:00:00Z",
        "created_at": "2026-09-10T12:00:00Z",
    }
    _mock_github_issues(monkeypatch, [issue])
    handle_calls: list[str] = []

    def _track_handle(self, **kwargs):
        handle_calls.append(kwargs["delivery_id"])
        return {"status": "worker_started", "task_id": 2, "issue_number": 20}

    monkeypatch.setattr(TaskRouter, "handle", _track_handle)
    from autonomous_dev.watchdog import _scan_current_tasks

    _scan_current_tasks(settings)
    assert len(handle_calls) == 1


def test_startup_scan_triggers_missed_current_task(infra_env, monkeypatch: pytest.MonkeyPatch):
    """Startup scan triggers missed current-task promptly."""
    issue = {
        "number": 21,
        "state": "open",
        "body": "missed webhook",
        "labels": [{"name": "cursor-task"}, {"name": "current-task"}],
        "updated_at": "2026-09-11T10:00:00Z",
        "created_at": "2026-09-11T09:00:00Z",
    }
    _mock_github_issues(monkeypatch, [issue])
    scan_calls: list[str] = []

    def _track_scan(s):
        scan_calls.append("scan")

    monkeypatch.setattr("autonomous_dev.watchdog._scan_current_tasks", _track_scan)
    monkeypatch.setattr("autonomous_dev.watchdog._stop.wait", lambda _timeout: False)
    from autonomous_dev.watchdog import _startup_tick

    _startup_tick()
    assert scan_calls == ["scan"]


def test_watchdog_completed_terminal_same_generation(infra_env, monkeypatch: pytest.MonkeyPatch):
    """Completed remains terminal for the same generation."""
    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    payload = _issue_payload(number=20)
    execution_key = compute_execution_key(settings, payload)
    store.create_task(
        issue_number=20,
        delivery_id="completed-run",
        execution_key=execution_key,
        status=TaskStatus.COMPLETED,
    )
    _mock_github_issues(monkeypatch, [payload["issue"]])
    handle_calls: list[str] = []

    def _track_handle(self, **kwargs):
        handle_calls.append(kwargs["delivery_id"])
        return {"status": "worker_started"}

    monkeypatch.setattr(TaskRouter, "handle", _track_handle)
    from autonomous_dev.watchdog import _scan_current_tasks

    _scan_current_tasks(settings)
    assert handle_calls == []


def test_watchdog_completed_new_generation_recovers(infra_env, monkeypatch: pytest.MonkeyPatch):
    """Completed with new generation (explicit reactivation) is recoverable."""
    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    old_key = f"{settings.github_repo}#20#2026-09-10T12:00:00Z"
    store.create_task(
        issue_number=20,
        delivery_id="old-completed",
        execution_key=old_key,
        status=TaskStatus.COMPLETED,
    )
    issue = {
        "number": 20,
        "state": "open",
        "body": "reactivated after completion",
        "labels": [{"name": "cursor-task"}, {"name": "current-task"}],
        "updated_at": "2026-09-11T11:00:00Z",
        "created_at": "2026-09-10T12:00:00Z",
    }
    _mock_github_issues(monkeypatch, [issue])
    handle_calls: list[str] = []

    def _track_handle(self, **kwargs):
        handle_calls.append(kwargs["delivery_id"])
        return {"status": "worker_started", "task_id": 3, "issue_number": 20}

    monkeypatch.setattr(TaskRouter, "handle", _track_handle)
    from autonomous_dev.watchdog import _scan_current_tasks

    _scan_current_tasks(settings)
    assert len(handle_calls) == 1


def test_watchdog_product_decision_terminal(infra_env, monkeypatch: pytest.MonkeyPatch):
    """Product-decision remains terminal for the same generation."""
    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    payload = _issue_payload(number=22)
    execution_key = compute_execution_key(settings, payload)
    store.create_task(
        issue_number=22,
        delivery_id="pd-run",
        execution_key=execution_key,
        status=TaskStatus.PRODUCT_DECISION,
    )
    _mock_github_issues(monkeypatch, [payload["issue"]])
    handle_calls: list[str] = []

    def _track_handle(self, **kwargs):
        handle_calls.append(kwargs["delivery_id"])
        return {"status": "worker_started"}

    monkeypatch.setattr(TaskRouter, "handle", _track_handle)
    from autonomous_dev.watchdog import _scan_current_tasks

    _scan_current_tasks(settings)
    assert handle_calls == []


def test_watchdog_webhook_race_one_worker(infra_env, monkeypatch: pytest.MonkeyPatch):
    """Webhook + watchdog race -> one Worker only (same generation)."""
    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    payload = _issue_payload(number=20)
    execution_key = compute_execution_key(settings, payload)
    running = store.create_task(
        issue_number=20,
        delivery_id="webhook-first",
        execution_key=execution_key,
        status=TaskStatus.RUNNING,
    )
    store.try_acquire_lease(20, running.id, owner="worker-1", ttl_seconds=300)
    _mock_github_issues(monkeypatch, [payload["issue"]])
    handle_calls: list[str] = []

    def _track_handle(self, **kwargs):
        handle_calls.append(kwargs["delivery_id"])
        return {"status": "worker_started"}

    monkeypatch.setattr(TaskRouter, "handle", _track_handle)
    from autonomous_dev.watchdog import _scan_current_tasks

    _scan_current_tasks(settings)
    assert handle_calls == []


def test_old_failed_execution_remains_historical(infra_env):
    """Old FAILED execution remains historical/auditable after recovery."""
    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    old_key = f"{settings.github_repo}#20#2026-09-10T12:00:00Z"
    old_task = store.create_task(
        issue_number=20,
        delivery_id="historical-failed",
        execution_key=old_key,
        status=TaskStatus.FAILED,
    )
    store.update_task(old_task.id, error="simulated failure")
    new_key = f"{settings.github_repo}#20#2026-09-11T08:00:00Z"
    from autonomous_dev.watchdog import is_current_task_recoverable

    assert is_current_task_recoverable(store, execution_key=new_key, issue_number=20)
    assert store.get_task(old_task.id) is not None
    assert store.get_task(old_task.id).status == TaskStatus.FAILED
    assert store.get_task(old_task.id).error == "simulated failure"


def test_watchdog_ready_for_review_new_generation_recovers(
    infra_env, monkeypatch: pytest.MonkeyPatch
):
    """READY_FOR_REVIEW old generation + fresh current-task generation -> recovers once."""
    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    old_key = f"{settings.github_repo}#20#2026-09-11T09:01:57Z"
    prior = store.create_task(
        issue_number=20,
        delivery_id="prior-r4r",
        execution_key=old_key,
        status=TaskStatus.READY_FOR_REVIEW,
    )
    store.update_task(prior.id, commit_sha="abc1234567890")
    issue = {
        "number": 20,
        "state": "open",
        "body": "reactivated after review fail",
        "labels": [{"name": "cursor-task"}, {"name": "current-task"}, {"name": "needs-fix"}],
        "updated_at": "2026-09-11T10:05:28Z",
        "created_at": "2026-09-11T08:12:36Z",
    }
    _mock_github_issues(monkeypatch, [issue])
    handle_calls: list[str] = []

    def _track_handle(self, **kwargs):
        handle_calls.append(kwargs["delivery_id"])
        return {"status": "worker_started", "task_id": 5, "issue_number": 20}

    monkeypatch.setattr(TaskRouter, "handle", _track_handle)
    from autonomous_dev.watchdog import _scan_current_tasks

    _scan_current_tasks(settings)
    assert len(handle_calls) == 1


def test_watchdog_ready_for_review_same_generation_blocked(
    infra_env, monkeypatch: pytest.MonkeyPatch
):
    """READY_FOR_REVIEW same generation -> no duplicate Worker."""
    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    payload = _issue_payload(number=20)
    execution_key = compute_execution_key(settings, payload)
    same_gen = store.create_task(
        issue_number=20,
        delivery_id="r4r-same-gen",
        execution_key=execution_key,
        status=TaskStatus.READY_FOR_REVIEW,
    )
    store.update_task(same_gen.id, commit_sha="def1234567890")
    _mock_github_issues(monkeypatch, [payload["issue"]])
    handle_calls: list[str] = []

    def _track_handle(self, **kwargs):
        handle_calls.append(kwargs["delivery_id"])
        return {"status": "worker_started"}

    monkeypatch.setattr(TaskRouter, "handle", _track_handle)
    from autonomous_dev.watchdog import _scan_current_tasks

    _scan_current_tasks(settings)
    assert handle_calls == []


def test_watchdog_recovers_needs_fix_new_generation(infra_env, monkeypatch: pytest.MonkeyPatch):
    """NEEDS_FIX prior task + fresh generation (issue #20 scenario) -> recovers."""
    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    old_key = f"{settings.github_repo}#20#2026-09-10T12:00:00Z"
    store.create_task(
        issue_number=20,
        delivery_id="prior-needs-fix",
        execution_key=old_key,
        status=TaskStatus.NEEDS_FIX,
    )
    issue = {
        "number": 20,
        "state": "open",
        "body": "reactivated after review fail",
        "labels": [{"name": "cursor-task"}, {"name": "current-task"}],
        "updated_at": "2026-09-11T12:00:00Z",
        "created_at": "2026-09-10T12:00:00Z",
    }
    _mock_github_issues(monkeypatch, [issue])
    handle_calls: list[str] = []

    def _track_handle(self, **kwargs):
        handle_calls.append(kwargs["delivery_id"])
        return {"status": "worker_started", "task_id": 4, "issue_number": 20}

    monkeypatch.setattr(TaskRouter, "handle", _track_handle)
    from autonomous_dev.watchdog import _scan_current_tasks

    _scan_current_tasks(settings)
    assert len(handle_calls) == 1


def test_derive_system_status_running(infra_env):
    repo, db = infra_env
    store = StateStore(db)
    settings = AutonomousDevSettings()
    from autonomous_dev.status_deriver import WorkerLeaseSnapshot, derive_system_status

    task = store.create_task(
        issue_number=1,
        delivery_id="run-1",
        execution_key="repo#1#gen1",
        status=TaskStatus.RUNNING,
    )
    now = datetime.now(UTC)
    future = (now + timedelta(seconds=120)).isoformat()
    lease = WorkerLeaseSnapshot(
        locked=True,
        owner=f"worker-{task.id}",
        task_id=task.id,
        issue_number=1,
        acquired_at=now.isoformat(),
        heartbeat_at=now.isoformat(),
        lease_expires_at=future,
    )
    status = derive_system_status(
        primary_task=task,
        lease=lease,
        reviewer_locked=False,
        active_review=None,
        lease_ttl_seconds=settings.worker_lease_ttl_seconds,
        now=now,
    )
    assert status.value == "RUNNING"


def test_worker_running_label_stale_heartbeat_is_stale(infra_env, monkeypatch):
    repo, db = infra_env
    store = StateStore(db)
    settings = AutonomousDevSettings()
    from autonomous_dev.dashboard import build_dashboard_payload
    from autonomous_dev.status_deriver import SystemStatus

    task = store.create_task(
        issue_number=2,
        delivery_id="stale-label",
        status=TaskStatus.RUNNING,
    )
    past = (datetime.now(UTC) - timedelta(seconds=600)).isoformat()
    store.try_acquire_lease(2, task.id, owner=f"worker-{task.id}", ttl_seconds=1)
    with store._conn() as conn:  # noqa: SLF001
        conn.execute(
            "UPDATE worker_lock SET heartbeat_at = ?, lease_expires_at = ? WHERE id = 1",
            (past, past),
        )
    monkeypatch.setattr(
        "autonomous_dev.dashboard._fetch_issue_summary",
        lambda *a, **k: (None, ["cursor-task", "worker-running"]),
    )
    payload = build_dashboard_payload(settings, store)
    assert payload["system_status"] == SystemStatus.STALE.value


def test_active_task_no_lease_not_running(infra_env):
    repo, db = infra_env
    store = StateStore(db)
    settings = AutonomousDevSettings()
    from autonomous_dev.status_deriver import WorkerLeaseSnapshot, derive_system_status

    task = store.create_task(
        issue_number=3,
        delivery_id="no-lease",
        status=TaskStatus.RUNNING,
    )
    lease = WorkerLeaseSnapshot(False, None, None, None, None, None, None)
    status = derive_system_status(
        primary_task=task,
        lease=lease,
        reviewer_locked=False,
        active_review=None,
        lease_ttl_seconds=settings.worker_lease_ttl_seconds,
    )
    assert status.value in {"STALE", "FAILED"}
    assert status.value != "RUNNING"


def test_derive_system_status_reviewing(infra_env):
    repo, db = infra_env
    store = StateStore(db)
    settings = AutonomousDevSettings()
    from autonomous_dev.status_deriver import WorkerLeaseSnapshot, derive_system_status

    task = store.update_task(
        store.create_task(issue_number=4, delivery_id="rev").id,
        status=TaskStatus.READY_FOR_REVIEW,
        commit_sha="abc123def456",
    )
    lease = WorkerLeaseSnapshot(False, None, None, None, None, None, None)
    status = derive_system_status(
        primary_task=task,
        lease=lease,
        reviewer_locked=False,
        active_review=None,
        lease_ttl_seconds=settings.worker_lease_ttl_seconds,
    )
    assert status.value == "REVIEWING"


def test_derive_system_status_waiting_user(infra_env):
    repo, db = infra_env
    store = StateStore(db)
    settings = AutonomousDevSettings()
    from autonomous_dev.status_deriver import WorkerLeaseSnapshot, derive_system_status

    task = store.update_task(
        store.create_task(issue_number=5, delivery_id="pd").id,
        status=TaskStatus.PRODUCT_DECISION,
        error="Need owner input",
    )
    lease = WorkerLeaseSnapshot(False, None, None, None, None, None, None)
    status = derive_system_status(
        primary_task=task,
        lease=lease,
        reviewer_locked=False,
        active_review=None,
        lease_ttl_seconds=settings.worker_lease_ttl_seconds,
    )
    assert status.value == "WAITING_USER"


def test_derive_system_status_idle(infra_env):
    settings = AutonomousDevSettings()
    from autonomous_dev.status_deriver import WorkerLeaseSnapshot, derive_system_status

    lease = WorkerLeaseSnapshot(False, None, None, None, None, None, None)
    status = derive_system_status(
        primary_task=None,
        lease=lease,
        reviewer_locked=False,
        active_review=None,
        lease_ttl_seconds=settings.worker_lease_ttl_seconds,
        recent_failed_task=None,
    )
    assert status.value == "IDLE"


def test_derive_system_status_failed(infra_env):
    repo, db = infra_env
    store = StateStore(db)
    settings = AutonomousDevSettings()
    from autonomous_dev.status_deriver import WorkerLeaseSnapshot, derive_system_status

    task = store.update_task(
        store.create_task(issue_number=6, delivery_id="fail").id,
        status=TaskStatus.FAILED,
        error="unrecoverable",
    )
    lease = WorkerLeaseSnapshot(False, None, None, None, None, None, None)
    status = derive_system_status(
        primary_task=task,
        lease=lease,
        reviewer_locked=False,
        active_review=None,
        lease_ttl_seconds=settings.worker_lease_ttl_seconds,
    )
    assert status.value == "FAILED"


def test_dashboard_secret_redaction(infra_env):
    repo, db = infra_env
    store = StateStore(db)
    settings = AutonomousDevSettings()
    from autonomous_dev.dashboard import build_dashboard_payload

    task = store.create_task(issue_number=7, delivery_id="sec")
    store.update_task(
        task.id,
        status=TaskStatus.NEEDS_FIX,
        error="auth failed ghp_abc123secret token invalid",
    )
    payload = build_dashboard_payload(settings, store)
    dumped = json.dumps(payload)
    assert "ghp_" not in dumped
    assert "[REDACTED]" in dumped


def test_dashboard_current_task_selection(infra_env):
    repo, db = infra_env
    store = StateStore(db)
    settings = AutonomousDevSettings()
    from autonomous_dev.dashboard import build_dashboard_payload

    store.create_task(issue_number=8, delivery_id="old", status=TaskStatus.COMPLETED)
    running = store.create_task(issue_number=9, delivery_id="cur", status=TaskStatus.RUNNING)
    store.try_acquire_lease(9, running.id, owner=f"worker-{running.id}", ttl_seconds=300)
    payload = build_dashboard_payload(settings, store)
    assert payload["current_task"]["task_id"] == running.id


def test_dashboard_recent_activity_ordering(infra_env):
    repo, db = infra_env
    store = StateStore(db)
    from autonomous_dev.dashboard import build_recent_activity

    store.record_delivery(
        delivery_id="d-old",
        event_type="issues",
        action="labeled",
        payload={"issue": {"number": 1}},
        status=DeliveryStatus.PROCESSED,
    )
    store.record_delivery(
        delivery_id="d-new",
        event_type="push",
        action=None,
        payload={"ref": "refs/heads/main"},
        status=DeliveryStatus.RECEIVED,
    )
    activity = build_recent_activity(store, limit=10)
    times = [a["at"] for a in activity if a["at"]]
    assert times == sorted(times, reverse=True)


def test_dashboard_completed_history(infra_env):
    repo, db = infra_env
    store = StateStore(db)
    settings = AutonomousDevSettings()
    from autonomous_dev.dashboard import build_dashboard_payload

    store.update_task(
        store.create_task(issue_number=10, delivery_id="done").id,
        status=TaskStatus.COMPLETED,
        commit_sha="deadbeef123456",
    )
    payload = build_dashboard_payload(settings, store)
    assert payload["recent_completed"][0]["issue_number"] == 10


def test_autonomous_status_json_endpoint(infra_env):
    client = TestClient(app)
    resp = client.get("/autonomous/status.json")
    assert resp.status_code == 200
    data = resp.json()
    assert "system_status" in data
    assert "runtime_evidence" in data


def test_autonomous_status_html_smoke(infra_env):
    client = TestClient(app)
    resp = client.get("/autonomous/status")
    assert resp.status_code == 200
    assert "自主开发 Dashboard" in resp.text
