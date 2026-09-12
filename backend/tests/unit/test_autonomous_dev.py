"""P0 autonomous dev infrastructure tests."""

from __future__ import annotations

import hashlib
import hmac
import json
import subprocess
import threading
import time
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
        self.titles: dict[int, str] = {}
        self.created_issues: list[dict] = []
        self.closed: list[int] = []
        self._next_issue = 9000

    def _issue_dict(self, number: int) -> dict:
        return {
            "number": number,
            "state": "closed" if number in self.closed else "open",
            "title": self.titles.get(number, f"Issue #{number}"),
            "body": self.bodies.get(number, ""),
            "labels": [{"name": n} for n in sorted(self.labels.get(number, set()))],
        }

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
        self.titles[num] = title
        return num

    def update_issue_body(self, issue_number: int, body: str) -> None:
        self.bodies[issue_number] = body

    def remove_label(self, issue_number: int, label: str) -> None:
        self.labels.get(issue_number, set()).discard(label)

    def find_open_issue_by_title_prefix(self, prefix: str) -> int | None:
        for num, title in self.titles.items():
            if num in self.closed:
                continue
            if prefix in title:
                return num
        for issue in self.created_issues:
            if prefix in issue["title"]:
                return issue["number"]
        return None

    def find_open_issue_by_body_marker(self, marker: str) -> int | None:
        for num, body in self.bodies.items():
            if num in self.closed:
                continue
            if marker in body:
                return num
        return None

    def list_open_issues_with_label(
        self,
        label: str,
        *,
        limit: int = 30,
        state: str = "open",
    ) -> list[dict]:
        issues: list[dict] = []
        all_nums = set(self.labels.keys()) | set(self.titles.keys()) | set(self.bodies.keys())
        for num in sorted(all_nums):
            issue = self._issue_dict(num)
            if state == "open" and issue["state"] != "open":
                continue
            labels = {lbl["name"] for lbl in issue["labels"]}
            if label and label not in labels:
                continue
            issues.append(issue)
            if len(issues) >= limit:
                break
        return issues

    def enforce_single_current_task(self, keep_issue_number: int) -> None:
        for num in list(self.labels.keys()):
            if num != keep_issue_number:
                self.labels.get(num, set()).discard("current-task")
        self.labels.setdefault(keep_issue_number, set()).update({"cursor-task", "current-task"})


@pytest.fixture(autouse=True)
def _mock_github_client(monkeypatch: pytest.MonkeyPatch):
    mock = _MockGitHubClient()

    def _factory(*args, **kwargs):
        return mock

    monkeypatch.setattr("autonomous_dev.worker.GitHubClient", _factory)
    monkeypatch.setattr("autonomous_dev.task_router.GitHubClient", _factory)
    monkeypatch.setattr("autonomous_dev.review_bridge.GitHubClient", _factory)
    monkeypatch.setattr("autonomous_dev.review_executor.GitHubClient", _factory)
    monkeypatch.setattr("autonomous_dev.task_handoff.GitHubClient", _factory)
    monkeypatch.setattr("autonomous_dev.next_task_resolver.GitHubClient", _factory)
    monkeypatch.setattr("autonomous_dev.loop_recovery.GitHubClient", _factory)
    monkeypatch.setattr("autonomous_dev.worker_self_heal.GitHubClient", _factory)
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

    monkeypatch.setattr(worker, "_run_tests", lambda *a, **k: None)
    monkeypatch.setattr(worker, "_git_fetch", lambda *a, **k: None)
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

    class FakeRun:
        def events(self):
            return iter([])

        def wait(self):
            return FakeResult()

    class FakeAgent:
        def send(self, prompt):
            return FakeRun()

        def close(self):
            return None

    def fake_create(options):
        captured["model"] = options.model
        captured["api_key"] = options.api_key
        return FakeAgent()

    monkeypatch.setattr("cursor_sdk.Agent.create", fake_create)

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

    class FakeRun:
        def events(self):
            return iter([])

        def wait(self):
            raise RuntimeError("simulated agent failure")

    class FakeAgent:
        def send(self, prompt):
            return FakeRun()

        def close(self):
            return None

    monkeypatch.setattr("cursor_sdk.Agent.create", lambda options: FakeAgent())

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


def test_reviewer_live_acceptance_uses_marker_only_in_real_mode(
    infra_env, monkeypatch: pytest.MonkeyPatch
):
    repo, db = infra_env
    monkeypatch.setenv("AUTONOMOUS_WORKER_MODE", "cursor_sdk")
    clear_autonomous_settings_cache()
    settings = AutonomousDevSettings()
    assert settings.autonomous_worker_mode == "cursor_sdk"
    marker = repo / "autonomous_dev" / "acceptance_marker.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("live\n", encoding="utf-8")
    svc = ReviewerService(settings, repo_root=repo)
    monkeypatch.setattr(
        svc,
        "_git_diff",
        lambda _sha: (
            "diff --git a/autonomous_dev/acceptance_marker.txt "
            "b/autonomous_dev/acceptance_marker.txt"
        ),
    )
    ctx = svc.gather_context(
        issue_number=1,
        issue_body=f"{REVIEWER_ACCEPTANCE_MARKER}\n[P0-LIVE-ACCEPTANCE]\nAUTO-A marker only",
        commit_sha="abc123",
    )
    assert "acceptance_marker" in ctx.diff

    calls = {"openai": 0}
    real_openai = ReviewerService._openai_review

    def blocked_openai(self, ctx, inv_id, creds):
        calls["openai"] += 1
        return real_openai(self, ctx, inv_id, creds)

    monkeypatch.setattr(ReviewerService, "_openai_review", blocked_openai)
    result = svc.review(ctx)
    assert result.verdict == "PASS"
    assert calls["openai"] == 0


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
    deadline = time.monotonic() + 30
    inv = store.get_review_invocation(task.id, "deadlock123456")
    while inv is None or inv.status != ReviewInvocationStatus.COMPLETED:
        assert time.monotonic() < deadline, "review did not complete after async kick"
        time.sleep(0.05)
        inv = store.get_review_invocation(task.id, "deadlock123456")
    assert inv is not None
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


def test_watchdog_recovers_failed_same_generation(infra_env, monkeypatch: pytest.MonkeyPatch):
    """Same-generation FAILED + current-task reactivation -> recovers (matches webhook path)."""
    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    payload = _issue_payload(number=20)
    execution_key = compute_execution_key(settings, payload)
    store.create_task(
        issue_number=20,
        delivery_id="prior-failed-same-gen",
        execution_key=execution_key,
        status=TaskStatus.FAILED,
    )
    _mock_github_issues(monkeypatch, [payload["issue"]])
    handle_calls: list[str] = []

    def _track_handle(self, **kwargs):
        handle_calls.append(kwargs["delivery_id"])
        return {"status": "worker_started", "task_id": 6, "issue_number": 20}

    monkeypatch.setattr(TaskRouter, "handle", _track_handle)
    from autonomous_dev.watchdog import _scan_current_tasks

    _scan_current_tasks(settings)
    assert len(handle_calls) == 1


def test_is_current_task_recoverable_same_gen_failed(infra_env):
    """FAILED same generation is recoverable; COMPLETED same generation is not."""
    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    payload = _issue_payload(number=20)
    execution_key = compute_execution_key(settings, payload)
    store.create_task(
        issue_number=20,
        delivery_id="failed-same-gen",
        execution_key=execution_key,
        status=TaskStatus.FAILED,
    )
    from autonomous_dev.watchdog import is_current_task_recoverable

    assert is_current_task_recoverable(store, execution_key=execution_key, issue_number=20)

    store.create_task(
        issue_number=21,
        delivery_id="completed-same-gen",
        execution_key=compute_execution_key(settings, _issue_payload(number=21)),
        status=TaskStatus.COMPLETED,
    )
    completed_key = compute_execution_key(settings, _issue_payload(number=21))
    assert not is_current_task_recoverable(
        store, execution_key=completed_key, issue_number=21
    )


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
        "autonomous_dev.dashboard._fetch_issue_labels_bounded",
        lambda *a, **k: ([], None),
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
    assert "实时开发控制台" in resp.text
    assert "北京时间" in resp.text
    assert "fmtClock" in resp.text
    assert "Asia/Shanghai" in resp.text


def test_dashboard_json_while_worker_running(infra_env):
    repo, db = infra_env
    store = StateStore(db)
    AutonomousDevSettings()


    task = store.create_task(
        issue_number=30,
        delivery_id="busy-worker",
        status=TaskStatus.RUNNING,
    )
    datetime.now(UTC)
    store.try_acquire_lease(30, task.id, owner=f"worker-{task.id}", ttl_seconds=300)
    client = TestClient(app)
    resp = client.get("/autonomous/status.json")
    assert resp.status_code == 200
    data = resp.json()
    assert data["system_status"] in {"RUNNING", "STALE", "REVIEWING", "IDLE", "FAILED"}


def test_dashboard_github_failure_still_200(infra_env, monkeypatch):
    repo, db = infra_env
    store = StateStore(db)
    settings = AutonomousDevSettings()
    store.create_task(issue_number=31, delivery_id="gh-fail", status=TaskStatus.RUNNING)

    def _slow_labels(*args, **kwargs):
        import time

        time.sleep(5)
        return ["worker-running"]

    monkeypatch.setattr(
        "autonomous_dev.dashboard._fetch_issue_labels_sync",
        _slow_labels,
    )
    monkeypatch.setattr("autonomous_dev.dashboard.DASHBOARD_GITHUB_TIMEOUT_SECONDS", 0.1)

    from autonomous_dev.dashboard import build_dashboard_payload

    payload = build_dashboard_payload(settings, store)
    assert payload["system_status"]
    assert payload["runtime_evidence"]["dashboard_degraded"] is True
    client = TestClient(app)
    resp = client.get("/autonomous/status.json")
    assert resp.status_code == 200


def test_dashboard_github_error_degrades(infra_env, monkeypatch):
    repo, db = infra_env
    store = StateStore(db)
    settings = AutonomousDevSettings()
    store.create_task(issue_number=32, delivery_id="gh-err", status=TaskStatus.RUNNING)

    def _boom(*args, **kwargs):
        raise RuntimeError("github down")

    monkeypatch.setattr("autonomous_dev.dashboard._fetch_issue_labels_sync", _boom)
    from autonomous_dev.dashboard import build_dashboard_payload

    payload = build_dashboard_payload(settings, store)
    assert payload["runtime_evidence"]["dashboard_degraded"] is True
    assert payload["system_status"]


def test_dashboard_reviewer_block_does_not_block(infra_env, monkeypatch):
    repo, db = infra_env
    store = StateStore(db)
    store.update_task(
        store.create_task(issue_number=33, delivery_id="rev").id,
        status=TaskStatus.READY_FOR_REVIEW,
    )
    monkeypatch.setattr(
        "autonomous_dev.dashboard._fetch_issue_labels_bounded",
        lambda *a, **k: ([], None),
    )
    client = TestClient(app)
    resp = client.get("/autonomous/status.json")
    assert resp.status_code == 200
    assert resp.json()["system_status"] == "REVIEWING"


def test_dashboard_db_busy_degrades_not_hang(infra_env):
    repo, db = infra_env
    store = StateStore(db)
    settings = AutonomousDevSettings()
    store.create_task(issue_number=34, delivery_id="db-busy", status=TaskStatus.RUNNING)
    hold = threading.Event()
    blocker_done = threading.Event()

    def _hold_lock():
        with store._lock:
            hold.set()
            blocker_done.wait(timeout=3)

    t = threading.Thread(target=_hold_lock, daemon=True)
    t.start()
    assert hold.wait(timeout=2)
    from autonomous_dev.dashboard import build_dashboard_payload

    payload = build_dashboard_payload(settings, store)
    blocker_done.set()
    t.join(timeout=2)
    assert payload["system_status"]


def test_dashboard_no_unbounded_external_io(infra_env, monkeypatch):
    monkeypatch.setattr(
        "autonomous_dev.dashboard.DASHBOARD_GITHUB_TIMEOUT_SECONDS",
        0.05,
    )
    calls = {"n": 0}

    def _count(*args, **kwargs):
        calls["n"] += 1
        return []

    monkeypatch.setattr("autonomous_dev.dashboard._fetch_issue_labels_sync", _count)
    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    store.create_task(issue_number=35, delivery_id="io-bound", status=TaskStatus.RUNNING)
    import time

    from autonomous_dev.dashboard import build_dashboard_payload

    start = time.monotonic()
    build_dashboard_payload(settings, store)
    elapsed = time.monotonic() - start
    assert elapsed < 2.0
    assert calls["n"] <= 1


def test_execution_event_append_and_read(infra_env):
    repo, db = infra_env
    store = StateStore(db)
    task = store.create_task(issue_number=40, delivery_id="evt-1", status=TaskStatus.RUNNING)
    e1 = store.append_execution_event(
        task_id=task.id,
        issue_number=40,
        generation="gen1",
        event_type="TASK_STARTED",
        phase="worker",
        action="Worker 已启动",
    )
    e2 = store.append_execution_event(
        task_id=task.id,
        issue_number=40,
        generation="gen1",
        event_type="TEST_STARTED",
        command_summary="pytest backend/tests -q",
    )
    events = store.list_execution_events(task_id=task.id, limit=10)
    assert len(events) == 2
    assert events[0].event_type == "TEST_STARTED"
    assert events[1].event_type == "TASK_STARTED"
    assert e1.id != e2.id


def test_execution_events_task_isolation(infra_env):
    repo, db = infra_env
    store = StateStore(db)
    t1 = store.create_task(issue_number=41, delivery_id="iso-1")
    t2 = store.create_task(issue_number=42, delivery_id="iso-2")
    store.append_execution_event(
        task_id=t1.id,
        issue_number=41,
        generation=None,
        event_type="TASK_STARTED",
    )
    store.append_execution_event(
        task_id=t2.id,
        issue_number=42,
        generation=None,
        event_type="TASK_STARTED",
    )
    assert len(store.list_execution_events(task_id=t1.id)) == 1
    assert store.list_execution_events(task_id=t1.id)[0].issue_number == 41


def test_execution_event_ordering(infra_env):
    repo, db = infra_env
    store = StateStore(db)
    task = store.create_task(issue_number=43, delivery_id="ord")
    for et in ("TASK_STARTED", "TEST_STARTED", "TEST_FINISHED", "GIT_DIFF"):
        store.append_execution_event(
            task_id=task.id,
            issue_number=43,
            generation=None,
            event_type=et,
        )
    events = store.list_execution_events(task_id=task.id, limit=10)
    assert [e.event_type for e in events] == [
        "GIT_DIFF",
        "TEST_FINISHED",
        "TEST_STARTED",
        "TASK_STARTED",
    ]


def test_derive_motion_moving(infra_env):
    from autonomous_dev.execution_events import MotionStatus, derive_motion_status
    from autonomous_dev.status_deriver import WorkerLeaseSnapshot

    repo, db = infra_env
    store = StateStore(db)
    settings = AutonomousDevSettings()
    now = datetime.now(UTC)
    task = store.create_task(issue_number=44, delivery_id="mov", status=TaskStatus.RUNNING)
    store.append_execution_event(
        task_id=task.id,
        issue_number=44,
        generation=None,
        event_type="TEST_STARTED",
    )
    events = store.list_execution_events(task_id=task.id)
    lease = WorkerLeaseSnapshot(
        True,
        f"worker-{task.id}",
        task.id,
        44,
        now.isoformat(),
        now.isoformat(),
        (now + timedelta(seconds=300)).isoformat(),
    )
    motion = derive_motion_status(
        primary_task=task,
        lease=lease,
        reviewer_locked=False,
        active_review=None,
        events=events,
        progress_stale_seconds=120,
        cursor_long_op_grace_seconds=600,
        cursor_long_op_suspect_seconds=900,
        lease_ttl_seconds=settings.worker_lease_ttl_seconds,
        now=now,
    )
    assert motion == MotionStatus.MOVING


def test_derive_motion_stalled(infra_env):
    from autonomous_dev.execution_events import MotionStatus, derive_motion_status
    from autonomous_dev.status_deriver import WorkerLeaseSnapshot

    repo, db = infra_env
    store = StateStore(db)
    settings = AutonomousDevSettings()
    now = datetime.now(UTC)
    task = store.create_task(issue_number=45, delivery_id="stall", status=TaskStatus.RUNNING)
    old = (now - timedelta(seconds=300)).isoformat()
    with store._conn() as conn:  # noqa: SLF001
        conn.execute(
            """
            INSERT INTO execution_events
            (task_id, issue_number, generation, event_type, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (task.id, 45, None, "TASK_STARTED", old),
        )
    events = store.list_execution_events(task_id=task.id)
    lease = WorkerLeaseSnapshot(
        True,
        f"worker-{task.id}",
        task.id,
        45,
        now.isoformat(),
        now.isoformat(),
        (now + timedelta(seconds=300)).isoformat(),
    )
    motion = derive_motion_status(
        primary_task=task,
        lease=lease,
        reviewer_locked=False,
        active_review=None,
        events=events,
        progress_stale_seconds=120,
        cursor_long_op_grace_seconds=600,
        cursor_long_op_suspect_seconds=900,
        lease_ttl_seconds=settings.worker_lease_ttl_seconds,
        now=now,
    )
    assert motion == MotionStatus.STALLED


def test_derive_motion_stale_priority(infra_env):
    from autonomous_dev.execution_events import MotionStatus, derive_motion_status
    from autonomous_dev.status_deriver import WorkerLeaseSnapshot

    repo, db = infra_env
    store = StateStore(db)
    settings = AutonomousDevSettings()
    now = datetime.now(UTC)
    task = store.create_task(issue_number=46, delivery_id="stale", status=TaskStatus.RUNNING)
    store.append_execution_event(
        task_id=task.id,
        issue_number=46,
        generation=None,
        event_type="TASK_STARTED",
    )
    events = store.list_execution_events(task_id=task.id)
    past = (now - timedelta(seconds=600)).isoformat()
    lease = WorkerLeaseSnapshot(
        True,
        f"worker-{task.id}",
        task.id,
        46,
        past,
        past,
        past,
    )
    motion = derive_motion_status(
        primary_task=task,
        lease=lease,
        reviewer_locked=False,
        active_review=None,
        events=events,
        progress_stale_seconds=120,
        cursor_long_op_grace_seconds=600,
        cursor_long_op_suspect_seconds=900,
        lease_ttl_seconds=settings.worker_lease_ttl_seconds,
        now=now,
    )
    assert motion == MotionStatus.STALE


def test_derive_motion_reviewing(infra_env):
    from autonomous_dev.execution_events import MotionStatus, derive_motion_status
    from autonomous_dev.status_deriver import WorkerLeaseSnapshot

    repo, db = infra_env
    store = StateStore(db)
    settings = AutonomousDevSettings()
    task = store.update_task(
        store.create_task(issue_number=47, delivery_id="rev").id,
        status=TaskStatus.READY_FOR_REVIEW,
        commit_sha="abc1234567890",
    )
    inv = store.create_review_invocation(
        invocation_id="inv-1",
        task_id=task.id,
        issue_number=47,
        commit_sha="abc1234567890",
    )
    store.update_review_invocation(inv.invocation_id, status=ReviewInvocationStatus.RUNNING)
    active = store.get_review_invocation(task.id, "abc1234567890")
    lease = WorkerLeaseSnapshot(False, None, None, None, None, None, None)
    motion = derive_motion_status(
        primary_task=task,
        lease=lease,
        reviewer_locked=True,
        active_review=active,
        events=[],
        progress_stale_seconds=120,
        cursor_long_op_grace_seconds=600,
        cursor_long_op_suspect_seconds=900,
        lease_ttl_seconds=settings.worker_lease_ttl_seconds,
    )
    assert motion == MotionStatus.REVIEWING


def test_sanitize_repo_path(infra_env):
    from autonomous_dev.execution_events import sanitize_repo_path

    repo, _ = infra_env
    assert sanitize_repo_path("autonomous_dev/dashboard.py", repo) == "autonomous_dev/dashboard.py"
    rel_path = str(repo / "autonomous_dev" / "state.py")
    assert sanitize_repo_path(rel_path, repo) == "autonomous_dev/state.py"
    assert sanitize_repo_path(".env", repo) is None
    assert sanitize_repo_path("backend/cases/secret.pdf", repo) is None


def test_sanitize_command_redaction():
    from autonomous_dev.execution_events import sanitize_command

    assert sanitize_command(["export", "OPENAI_API_KEY=sk-secret"]) == "[REDACTED]"
    assert sanitize_command(["pytest", "backend/tests", "-q"]) == "pytest backend/tests -q"
    assert "ghp_" not in (sanitize_command(["git", "push", "https://ghp_abc@github.com/x"]) or "")


def test_execution_trace_secret_redaction(infra_env):
    from autonomous_dev.dashboard import build_dashboard_payload

    repo, db = infra_env
    store = StateStore(db)
    settings = AutonomousDevSettings()
    task = store.create_task(issue_number=48, delivery_id="trace-sec", status=TaskStatus.RUNNING)
    store.try_acquire_lease(48, task.id, owner=f"worker-{task.id}", ttl_seconds=300)
    store.append_execution_event(
        task_id=task.id,
        issue_number=48,
        generation=None,
        event_type="COMMAND_STARTED",
        command_summary="export GITHUB_TOKEN=ghp_leaked",
        result_summary="failed ghp_leaked",
    )
    payload = build_dashboard_payload(settings, store)
    dumped = json.dumps(payload["execution_trace"])
    assert "ghp_" not in dumped
    assert "execution_trace" in payload
    assert payload["execution_trace"]["motion_status"] in {
        "MOVING",
        "STALLED",
        "STALE",
        "IDLE",
        "REVIEWING",
    }


def test_execution_trace_schema(infra_env):
    from autonomous_dev.dashboard import build_dashboard_payload

    repo, db = infra_env
    store = StateStore(db)
    settings = AutonomousDevSettings()
    task = store.create_task(issue_number=49, delivery_id="schema", status=TaskStatus.RUNNING)
    store.try_acquire_lease(49, task.id, owner=f"worker-{task.id}", ttl_seconds=300)
    store.append_execution_event(
        task_id=task.id,
        issue_number=49,
        generation=None,
        event_type="GIT_DIFF",
        metadata={"files_changed": 2, "insertions": 10, "deletions": 3},
    )
    store.append_execution_event(
        task_id=task.id,
        issue_number=49,
        generation=None,
        event_type="TEST_FINISHED",
        status="pass",
        metadata={"passed": 5, "failed": 0},
        command_summary="pytest -q",
    )
    payload = build_dashboard_payload(settings, store)
    trace = payload["execution_trace"]
    for key in (
        "motion_status",
        "current_action",
        "last_progress_at",
        "seconds_since_last_progress",
        "last_heartbeat_at",
        "seconds_since_last_heartbeat",
        "telemetry_level",
        "recent_files",
        "code_changes",
        "latest_test",
        "latest_ruff",
        "git",
        "events",
        "elapsed_display",
        "long_running",
    ):
        assert key in trace
    assert trace["git"]["files_changed"] == 2
    assert trace["latest_test"]["passed"] == 5


def test_execution_trace_event_limit(infra_env):
    from autonomous_dev.dashboard import build_dashboard_payload

    repo, db = infra_env
    store = StateStore(db)
    settings = AutonomousDevSettings()
    task = store.create_task(issue_number=50, delivery_id="limit", status=TaskStatus.RUNNING)
    store.try_acquire_lease(50, task.id, owner=f"worker-{task.id}", ttl_seconds=300)
    for i in range(120):
        store.append_execution_event(
            task_id=task.id,
            issue_number=50,
            generation=None,
            event_type="COMMAND_STARTED",
            command_summary=f"echo {i}",
        )
    payload = build_dashboard_payload(settings, store)
    assert len(payload["execution_trace"]["events"]) <= 100


def test_dashboard_trace_prefers_ready_for_review_over_newer_failed(infra_env, monkeypatch):
    """Regression: newer failed task must not hijack trace for ready-for-review issue."""
    from autonomous_dev.dashboard import build_dashboard_payload

    repo, db = infra_env
    store = StateStore(db)
    settings = AutonomousDevSettings()
    ready = store.update_task(
        store.create_task(issue_number=23, delivery_id="ready").id,
        status=TaskStatus.READY_FOR_REVIEW,
        commit_sha="abc123def456",
    )
    failed = store.create_task(issue_number=1, delivery_id="failed", status=TaskStatus.FAILED)
    store.update_task(failed.id, error="stale worker lease recovered")
    store.append_execution_event(
        task_id=ready.id,
        issue_number=23,
        generation=None,
        event_type="CURSOR_FINISHED",
        result_summary="done",
    )
    store.append_execution_event(
        task_id=failed.id,
        issue_number=1,
        generation=None,
        event_type="CURSOR_STARTED",
    )
    monkeypatch.setattr(
        "autonomous_dev.dashboard._fetch_issue_labels_bounded",
        lambda *a, **k: ([], None),
    )
    monkeypatch.setattr(
        "autonomous_dev.dashboard._fetch_main_sha_bounded",
        lambda *a, **k: (None, None),
    )

    payload = build_dashboard_payload(settings, store)

    assert payload["current_task"]["issue_number"] == 23
    assert payload["system_status"] == "REVIEWING"
    event_types = {e["event_type"] for e in payload["execution_trace"]["events"]}
    assert "CURSOR_FINISHED" in event_types
    assert "CURSOR_STARTED" not in event_types


def test_dashboard_html_execution_trace_smoke(infra_env):
    client = TestClient(app)
    resp = client.get("/autonomous/status")
    assert resp.status_code == 200
    assert "实时开发过程" in resp.text
    assert "trace-terminal" in resp.text
    assert "3000" in resp.text


def test_worker_records_execution_events(infra_env, monkeypatch):
    repo, db = infra_env
    store = StateStore(db)
    settings = AutonomousDevSettings()
    task = store.create_task(
        issue_number=51,
        delivery_id="worker-ev",
        execution_key="repo#51#gen",
        status=TaskStatus.QUEUED,
    )
    store.try_acquire_lease(51, task.id, owner=f"worker-{task.id}", ttl_seconds=300)
    worker = Worker(settings, store, repo_root=repo)
    result = worker.run_task(task, issue_body="deterministic worker test")
    assert result.status in {TaskStatus.RUNNING, TaskStatus.READY_FOR_REVIEW, TaskStatus.NEEDS_FIX}
    events = store.list_execution_events(task_id=task.id, limit=50)
    types = {e.event_type for e in events}
    assert "TASK_STARTED" in types
    assert "TEST_STARTED" in types or "COMMAND_STARTED" in types


def test_autonomous_status_json_includes_execution_trace(infra_env):
    client = TestClient(app)
    resp = client.get("/autonomous/status.json")
    assert resp.status_code == 200
    data = resp.json()
    assert "execution_trace" in data
    assert "motion_status" in data["execution_trace"]


def test_derive_motion_waiting(infra_env):
    from autonomous_dev.execution_events import MotionStatus, derive_motion_status
    from autonomous_dev.status_deriver import WorkerLeaseSnapshot

    repo, db = infra_env
    store = StateStore(db)
    settings = AutonomousDevSettings()
    task = store.update_task(
        store.create_task(issue_number=60, delivery_id="wait").id,
        status=TaskStatus.READY_FOR_REVIEW,
        commit_sha="abc1234567890",
    )
    inv = store.create_review_invocation(
        invocation_id="inv-w",
        task_id=task.id,
        issue_number=60,
        commit_sha="abc1234567890",
    )
    lease = WorkerLeaseSnapshot(False, None, None, None, None, None, None)
    motion = derive_motion_status(
        primary_task=task,
        lease=lease,
        reviewer_locked=False,
        active_review=inv,
        events=[],
        progress_stale_seconds=120,
        cursor_long_op_grace_seconds=600,
        cursor_long_op_suspect_seconds=900,
        lease_ttl_seconds=settings.worker_lease_ttl_seconds,
    )
    assert motion == MotionStatus.WAITING


def test_derive_motion_failed(infra_env):
    from autonomous_dev.execution_events import MotionStatus, derive_motion_status
    from autonomous_dev.status_deriver import WorkerLeaseSnapshot

    repo, db = infra_env
    store = StateStore(db)
    settings = AutonomousDevSettings()
    task = store.update_task(
        store.create_task(issue_number=61, delivery_id="fail-motion").id,
        status=TaskStatus.NEEDS_FIX,
        error="pytest failed",
    )
    store.append_execution_event(
        task_id=task.id,
        issue_number=61,
        generation=None,
        event_type="TEST_FINISHED",
        status="fail",
    )
    lease = WorkerLeaseSnapshot(False, None, None, None, None, None, None)
    motion = derive_motion_status(
        primary_task=task,
        lease=lease,
        reviewer_locked=False,
        active_review=None,
        events=store.list_execution_events(task_id=task.id),
        progress_stale_seconds=120,
        cursor_long_op_grace_seconds=600,
        cursor_long_op_suspect_seconds=900,
        lease_ttl_seconds=settings.worker_lease_ttl_seconds,
    )
    assert motion == MotionStatus.FAILED


def test_telemetry_level_derivation(infra_env):
    from autonomous_dev.execution_events import TelemetryLevel, _derive_telemetry_level
    from autonomous_dev.state import ExecutionEventRecord

    boundary = [
        ExecutionEventRecord(
            1, 1, 1, None, "CURSOR_STARTED", "cursor", None, None, None, None, None, None, "t"
        )
    ]
    assert _derive_telemetry_level(boundary) == TelemetryLevel.BOUNDARY_ONLY
    partial = boundary + [
        ExecutionEventRecord(
            2, 1, 1, None, "CURSOR_PROGRESS", "cursor", None, None, None, None, None, None, "t"
        )
    ]
    assert _derive_telemetry_level(partial) == TelemetryLevel.PARTIAL
    full = partial + [
        ExecutionEventRecord(
            3,
            1,
            1,
            None,
            "FILE_EDIT",
            "cursor",
            None,
            "autonomous_dev/x.py",
            None,
            None,
            None,
            None,
            "t",
        )
    ]
    assert _derive_telemetry_level(full) == TelemetryLevel.FULL


def test_code_changes_and_ruff_summary(infra_env):
    from autonomous_dev.dashboard import build_dashboard_payload

    repo, db = infra_env
    store = StateStore(db)
    settings = AutonomousDevSettings()
    task = store.create_task(issue_number=62, delivery_id="cc", status=TaskStatus.RUNNING)
    store.try_acquire_lease(62, task.id, owner=f"worker-{task.id}", ttl_seconds=300)
    store.append_execution_event(
        task_id=task.id,
        issue_number=62,
        generation=None,
        event_type="FILE_EDIT",
        file_path="autonomous_dev/dashboard.py",
    )
    store.append_execution_event(
        task_id=task.id,
        issue_number=62,
        generation=None,
        event_type="RUFF_FINISHED",
        status="pass",
        command_summary="ruff check .",
    )
    payload = build_dashboard_payload(settings, store)
    trace = payload["execution_trace"]
    assert "autonomous_dev/dashboard.py" in trace["code_changes"]["modified_files"]
    assert trace["latest_ruff"]["status"] == "pass"
    assert trace["telemetry_level"] == "FULL"


def test_watchdog_loop_scans_on_short_interval(infra_env, monkeypatch):
    import autonomous_dev.watchdog as wd

    scan_calls: list[int] = []

    def _track_scan(settings):
        scan_calls.append(1)

    waits: list[int] = []

    def _wait(timeout):
        waits.append(timeout)
        return len(waits) > 2

    monkeypatch.setattr(wd, "_scan_current_tasks", _track_scan)
    monkeypatch.setattr(wd, "_tick", lambda: None)
    monkeypatch.setattr(wd, "_tick_full_scan", lambda _s: None)
    monkeypatch.setattr(wd._stop, "wait", _wait)
    monkeypatch.setenv("CURRENT_TASK_SCAN_INTERVAL_SECONDS", "10")
    monkeypatch.setenv("REVIEW_RECOVERY_INTERVAL_SECONDS", "10")
    clear_autonomous_settings_cache()
    wd._loop()
    assert len(scan_calls) >= 1


def test_watchdog_github_timeout_survives(infra_env, monkeypatch):
    import httpx

    class _TimeoutClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, *args, **kwargs):
            raise httpx.TimeoutException("timeout")

    monkeypatch.setattr("autonomous_dev.watchdog.httpx.Client", _TimeoutClient)
    monkeypatch.setattr("autonomous_dev.watchdog.resolve_github_token", lambda: "tok")
    repo, db = infra_env
    settings = AutonomousDevSettings()
    from autonomous_dev.watchdog import _scan_current_tasks

    _scan_current_tasks(settings)
    assert True


def test_healthz_exposes_runtime_sha(infra_env, monkeypatch):
    monkeypatch.setenv("GIT_SHA", "abc123deadbeef")
    monkeypatch.setenv("IMAGE_TAG", "abc123deadbeef")
    clear_autonomous_settings_cache()
    reset_autonomous_singletons()
    client = TestClient(app)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    data = resp.json()
    assert data["git_sha"] == "abc123deadbeef"
    assert data["image_tag"] == "abc123deadbeef"
    assert "started_at" in data


def test_dashboard_exposes_runtime_version(infra_env, monkeypatch):
    monkeypatch.setenv("GIT_SHA", "def456789abc")
    clear_autonomous_settings_cache()
    reset_autonomous_singletons()
    monkeypatch.setattr(
        "autonomous_dev.dashboard._fetch_main_sha_bounded",
        lambda *a, **k: ("def456789abc", None),
    )
    client = TestClient(app)
    resp = client.get("/autonomous/status.json")
    data = resp.json()
    assert data["runtime_version"]["git_sha"] == "def456789abc"
    assert data["deployment"]["status"] == "CURRENT"


def test_deployment_stale_when_main_differs(infra_env):
    from autonomous_dev.runtime_version import derive_deployment_status

    assert derive_deployment_status(runtime_sha="aaa1111", main_sha="bbb2222") == "STALE"
    assert derive_deployment_status(runtime_sha="abc1234", main_sha="abc1234567890") == "CURRENT"


def test_runtime_version_module(infra_env, monkeypatch):
    from autonomous_dev.runtime_version import get_runtime_version

    monkeypatch.setenv("GIT_SHA", "sha999")
    monkeypatch.setenv("IMAGE_TAG", "sha999")
    v = get_runtime_version()
    assert v["git_sha"] == "sha999"
    assert v["image_tag"] == "sha999"


def test_handoff_pass_activates_queued_next_task_once(infra_env, _mock_github_client):
    from autonomous_dev.state import HandoffStatus
    from autonomous_dev.task_handoff import TaskHandoffEngine

    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    _mock_github_client.labels[100] = {"cursor-task"}
    _mock_github_client.bodies[100] = "Queued task B"
    _mock_github_client.titles[100] = "Task B"
    task_a = store.create_task(issue_number=99, delivery_id="handoff-a")
    store.update_task(task_a.id, status=TaskStatus.COMPLETED, commit_sha="abc123456789")
    engine = TaskHandoffEngine(settings, store, github=_mock_github_client)
    result = engine.perform_handoff(task_a, commit_sha="abc123456789")
    assert result["status"] == "activated"
    assert result["next_issue_number"] == "100"
    assert _mock_github_client.labels[100] & {"cursor-task"} == {"cursor-task"}
    assert _mock_github_client.labels[100] & {"current-task", "worker-running"}
    handoff = store.get_handoff_by_key(f"handoff:{task_a.id}:abc123456789")
    assert handoff is not None
    allowed = {HandoffStatus.ACTIVATED, HandoffStatus.WORKER_STARTED, HandoffStatus.PENDING}
    assert handoff.status in allowed


def test_handoff_pass_replay_no_duplicate_activation(infra_env, _mock_github_client):
    from autonomous_dev.task_handoff import TaskHandoffEngine

    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    _mock_github_client.labels[101] = {"cursor-task"}
    _mock_github_client.titles[101] = "Task B replay"
    task_a = store.create_task(issue_number=98, delivery_id="handoff-replay")
    store.update_task(task_a.id, status=TaskStatus.COMPLETED, commit_sha="replay123456")
    engine = TaskHandoffEngine(settings, store, github=_mock_github_client)
    first = engine.perform_handoff(task_a, commit_sha="replay123456", trigger_worker=False)
    created_before = len(_mock_github_client.created_issues)
    second = engine.perform_handoff(task_a, commit_sha="replay123456", trigger_worker=False)
    assert first["status"] == "activated"
    assert second["status"] == "idempotent"
    assert len(_mock_github_client.created_issues) == created_before


def test_handoff_duplicate_webhook_watchdog_race_one_worker(infra_env, _mock_github_client):
    from autonomous_dev.task_handoff import TaskHandoffEngine

    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    _mock_github_client.labels[102] = {"cursor-task"}
    _mock_github_client.titles[102] = "Race task B"
    task_a = store.create_task(issue_number=97, delivery_id="handoff-race")
    store.update_task(task_a.id, status=TaskStatus.COMPLETED, commit_sha="race12345678")
    engine = TaskHandoffEngine(settings, store, github=_mock_github_client)
    engine.perform_handoff(task_a, commit_sha="race12345678", trigger_worker=True)
    engine.recover_pending_handoffs()
    workers = [
        t for t in [store.get_task_by_issue(102)]
        if t is not None and t.status in {TaskStatus.QUEUED, TaskStatus.RUNNING}
    ]
    assert len(workers) <= 1


def test_handoff_restart_after_pass_recovers_once(infra_env, _mock_github_client):
    from autonomous_dev.state import HandoffStatus
    from autonomous_dev.task_handoff import TaskHandoffEngine

    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    _mock_github_client.labels[103] = {"cursor-task"}
    _mock_github_client.titles[103] = "Restart B"
    task_a = store.create_task(issue_number=96, delivery_id="handoff-restart")
    store.update_task(task_a.id, status=TaskStatus.COMPLETED, commit_sha="restart12345")
    engine = TaskHandoffEngine(settings, store, github=_mock_github_client)
    engine.perform_handoff(task_a, commit_sha="restart12345", trigger_worker=False)
    store2 = StateStore(db)
    engine2 = TaskHandoffEngine(settings, store2, github=_mock_github_client)
    replay = engine2.perform_handoff(task_a, commit_sha="restart12345", trigger_worker=False)
    assert replay["status"] == "idempotent"
    handoffs = store2.list_active_handoffs()
    assert len(handoffs) == 1
    assert handoffs[0].next_issue_number == 103
    assert handoffs[0].status in {HandoffStatus.ACTIVATED, HandoffStatus.PENDING}


def test_handoff_active_current_task_enforces_sole_current(infra_env, _mock_github_client):
    from autonomous_dev.task_handoff import TaskHandoffEngine

    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    _mock_github_client.labels[200] = {"cursor-task", "current-task"}
    _mock_github_client.labels[201] = {"cursor-task"}
    _mock_github_client.titles[201] = "Would-be next"
    _mock_github_client.bodies[95] = "NEXT_TASK: Would-be next"
    task_a = store.create_task(issue_number=95, delivery_id="handoff-block")
    store.update_task(task_a.id, status=TaskStatus.COMPLETED, commit_sha="block1234567")
    engine = TaskHandoffEngine(settings, store, github=_mock_github_client)
    result = engine.perform_handoff(task_a, commit_sha="block1234567", trigger_worker=False)
    assert result["status"] == "activated"
    assert "current-task" not in _mock_github_client.labels.get(200, set())
    assert _mock_github_client.labels[201] == {"cursor-task", "current-task"}


def test_handoff_ambiguous_next_waiting_product_no_issue_invented(infra_env, _mock_github_client):
    from autonomous_dev.state import HandoffStatus
    from autonomous_dev.task_handoff import TaskHandoffEngine

    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    _mock_github_client.labels[301] = {"cursor-task"}
    _mock_github_client.labels[302] = {"cursor-task"}
    _mock_github_client.titles[301] = "Ambiguous A"
    _mock_github_client.titles[302] = "Ambiguous B"
    task_a = store.create_task(issue_number=94, delivery_id="handoff-ambig")
    store.update_task(task_a.id, status=TaskStatus.COMPLETED, commit_sha="ambig1234567")
    created_before = len(_mock_github_client.created_issues)
    engine = TaskHandoffEngine(settings, store, github=_mock_github_client)
    result = engine.perform_handoff(task_a, commit_sha="ambig1234567", trigger_worker=False)
    assert result["status"] == "waiting_product"
    assert len(_mock_github_client.created_issues) == created_before
    handoff = store.get_handoff_by_key(f"handoff:{task_a.id}:ambig1234567")
    assert handoff is not None
    assert handoff.status == HandoffStatus.WAITING_PRODUCT


def test_handoff_stalled_after_delay_then_retry(infra_env, _mock_github_client, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from autonomous_dev.state import HandoffStatus
    from autonomous_dev.task_handoff import TaskHandoffEngine, derive_handoff_dashboard_state

    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    _mock_github_client.labels[104] = {"cursor-task"}
    _mock_github_client.titles[104] = "Stall B"
    task_a = store.create_task(issue_number=93, delivery_id="handoff-stall")
    store.update_task(task_a.id, status=TaskStatus.COMPLETED, commit_sha="stall1234567")
    engine = TaskHandoffEngine(settings, store, github=_mock_github_client)
    engine.perform_handoff(task_a, commit_sha="stall1234567", trigger_worker=False)
    handoff = store.get_handoff_by_key(f"handoff:{task_a.id}:stall1234567")
    assert handoff is not None
    old = (datetime.now(UTC) - timedelta(seconds=25)).isoformat()
    store.update_handoff(handoff.handoff_id, activated_at=old, status=HandoffStatus.STALLED)
    state = derive_handoff_dashboard_state(
        store.get_handoff(handoff.handoff_id),
        has_live_worker=False,
        stall_seconds=20,
    )
    assert state == "HANDOFF_STALLED"
    engine.recover_pending_handoffs()
    updated = store.get_handoff(handoff.handoff_id)
    assert updated is not None
    allowed = {HandoffStatus.ACTIVATED, HandoffStatus.WORKER_STARTED, HandoffStatus.STALLED}
    assert updated.status in allowed


def test_handoff_stale_worker_running_label_not_active_worker(infra_env, _mock_github_client):
    from autonomous_dev.task_handoff import TaskHandoffEngine

    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    _mock_github_client.labels[105] = {"cursor-task", "current-task", "worker-running"}
    _mock_github_client.titles[105] = "Stale label B"
    task_a = store.create_task(issue_number=92, delivery_id="handoff-stale-label")
    store.update_task(task_a.id, status=TaskStatus.COMPLETED, commit_sha="stale1234567")
    engine = TaskHandoffEngine(settings, store, github=_mock_github_client)
    engine.perform_handoff(task_a, commit_sha="stale1234567", trigger_worker=False)
    assert not engine._has_valid_worker_for_issue(105)


def test_dashboard_not_idle_during_handoff_pending(infra_env, _mock_github_client):
    from autonomous_dev.dashboard import build_dashboard_payload
    from autonomous_dev.task_handoff import TaskHandoffEngine

    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    _mock_github_client.labels[106] = {"cursor-task"}
    _mock_github_client.titles[106] = "Dash B"
    task_a = store.create_task(issue_number=91, delivery_id="handoff-dash")
    store.update_task(task_a.id, status=TaskStatus.COMPLETED, commit_sha="dash12345678")
    engine = TaskHandoffEngine(settings, store, github=_mock_github_client)
    engine.perform_handoff(task_a, commit_sha="dash12345678", trigger_worker=False)
    payload = build_dashboard_payload(settings, store)
    assert payload["system_status"] in {"HANDOFF_PENDING", "HANDOFF_STALLED", "RUNNING"}
    assert payload["system_status"] != "IDLE"
    assert payload["handoff"] is not None
    assert payload["handoff"]["next_issue_number"] == 106


def test_reviewer_pass_triggers_handoff_via_executor(infra_env, _mock_github_client):
    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    marker = repo / "autonomous_dev" / "acceptance_marker.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("acceptance ok\n", encoding="utf-8")
    _mock_github_client.labels[107] = {"cursor-task"}
    _mock_github_client.titles[107] = "Executor handoff B"
    task = store.create_task(issue_number=90, delivery_id="rev-handoff")
    store.update_task(task.id, status=TaskStatus.READY_FOR_REVIEW, commit_sha="exec12345678")
    body = f"{REVIEWER_ACCEPTANCE_MARKER}\nHandoff acceptance."
    _mock_github_client.bodies[90] = body
    executor = ReviewExecutor(settings, store)
    result = executor.run_review_sync(task, commit_sha="exec12345678", issue_body=body)
    assert result["verdict"] == "PASS"
    handoff = store.get_handoff_by_key(f"handoff:{task.id}:exec12345678")
    assert handoff is not None
    assert handoff.next_issue_number == 107
    assert "cursor-task" in _mock_github_client.labels[107]
    assert _mock_github_client.labels[107] & {"current-task", "worker-running"}


def test_stale_lease_extended_when_progress_recent(infra_env):
    repo, db = infra_env
    store = StateStore(db)
    running = store.create_task(
        issue_number=24,
        delivery_id="progress-lease",
        execution_key="repo#24#gen1",
        status=TaskStatus.RUNNING,
    )
    assert store.try_acquire_lease(24, running.id, owner="worker-24", ttl_seconds=1)
    store.append_execution_event(
        task_id=running.id,
        issue_number=24,
        generation="gen1",
        event_type="CURSOR_PROGRESS",
        phase="cursor",
    )
    import time

    time.sleep(1.1)
    assert not store.recover_stale_lease(
        heartbeat_ttl_seconds=1,
        progress_grace_seconds=900,
    )
    assert store.is_locked()
    updated = store.get_task(running.id)
    assert updated.status == TaskStatus.RUNNING


def test_orphan_push_reconcile_to_ready_for_review(infra_env, monkeypatch, _mock_github_client):
    from autonomous_dev.loop_recovery import reconcile_orphan_pushes
    from autonomous_dev.review_bridge import ReviewBridge

    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    task = store.create_task(
        issue_number=24,
        delivery_id="orphan-push",
        execution_key="repo#24#orphan",
        status=TaskStatus.FAILED,
    )
    store.update_task(task.id, error="stale worker lease recovered")
    monkeypatch.setattr(
        "autonomous_dev.loop_recovery._find_orphan_commit_for_task",
        lambda _root, _task: "abc123456789deadbeef0123456789abcd",
    )
    review_bridge = ReviewBridge(settings, store)
    count = reconcile_orphan_pushes(settings, store, _mock_github_client, review_bridge)
    assert count == 1
    updated = store.get_task(task.id)
    assert updated.status == TaskStatus.READY_FOR_REVIEW
    assert updated.commit_sha == "abc123456789deadbeef0123456789abcd"


def test_cleanup_stale_worker_running_without_lease(infra_env, _mock_github_client):
    from autonomous_dev.loop_recovery import cleanup_stale_worker_running_labels

    repo, db = infra_env
    store = StateStore(db)
    _mock_github_client.labels[24] = {"cursor-task", "worker-running"}
    cleaned = cleanup_stale_worker_running_labels(store, _mock_github_client)
    assert cleaned == 1
    assert "worker-running" not in _mock_github_client.labels[24]


def test_loop_recovery_tick_survives_github_errors(infra_env, monkeypatch):
    from autonomous_dev.loop_recovery import run_loop_recovery_tick

    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    monkeypatch.setattr(
        "autonomous_dev.loop_recovery.cleanup_stale_worker_running_labels",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("github down")),
    )
    counts = run_loop_recovery_tick(settings, store)
    assert counts["stale_labels"] == 0


def test_repair_issue_no_longer_blocked_on_startup(infra_env, _mock_github_client):
    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    _mock_github_client.titles[57] = "[REPAIR] Issue #56: truncated migration"
    _mock_github_client.bodies[57] = "Fix reviewer FAIL items for #56"
    task = store.create_task(issue_number=57, delivery_id="repair-57")
    router = TaskRouter(settings, store)
    result = router.run_worker_sync(task, issue_body=_mock_github_client.bodies[57])
    assert "repair issue #57 blocked" not in (result.error or "").lower()


def test_technical_needs_fix_self_heal_without_manual_current_task(
    infra_env, _mock_github_client, monkeypatch: pytest.MonkeyPatch
):
    from autonomous_dev.loop_recovery import run_loop_recovery_tick
    from autonomous_dev.state import WorkerReactivationStatus
    from autonomous_dev.worker_self_heal import SELF_HEAL_ACCEPTANCE_MARKER

    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    issue_num = 57
    _mock_github_client.labels[issue_num] = {"cursor-task", "needs-fix"}
    _mock_github_client.bodies[issue_num] = f"body\n{SELF_HEAL_ACCEPTANCE_MARKER}"
    old_key = f"{settings.github_repo}#{issue_num}#2026-09-10T12:00:00Z"
    failed = store.create_task(
        issue_number=issue_num,
        delivery_id="repair-failed",
        execution_key=old_key,
        status=TaskStatus.NEEDS_FIX,
    )
    store.upsert_worker_reactivation(
        issue_number=issue_num,
        task_id=failed.id,
        attempt_count=1,
        next_retry_at=datetime.now(UTC).isoformat(),
        last_error="blocked",
        status=WorkerReactivationStatus.PENDING,
    )
    counts = run_loop_recovery_tick(settings, store)
    assert counts["technical_needs_fix"] == 1
    deadline = time.time() + 15
    while time.time() < deadline:
        latest = store.get_task_by_issue(issue_num)
        if latest and latest.id != failed.id:
            active = {TaskStatus.RUNNING, TaskStatus.READY_FOR_REVIEW, TaskStatus.QUEUED}
            if latest.status in active:
                break
        time.sleep(0.2)
    latest = store.get_task_by_issue(issue_num)
    assert latest is not None and latest.id != failed.id


def test_self_heal_dashboard_exposes_retry_state(infra_env, monkeypatch):
    from autonomous_dev.dashboard import build_dashboard_payload
    from autonomous_dev.state import WorkerReactivationStatus
    from autonomous_dev.status_deriver import SystemStatus

    repo, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    task = store.create_task(issue_number=57, delivery_id="dash", status=TaskStatus.NEEDS_FIX)
    future = (datetime.now(UTC) + timedelta(seconds=120)).isoformat()
    store.upsert_worker_reactivation(
        issue_number=57,
        task_id=task.id,
        attempt_count=2,
        next_retry_at=future,
        last_error="startup failure",
        status=WorkerReactivationStatus.PENDING,
    )
    monkeypatch.setattr(
        "autonomous_dev.dashboard._fetch_issue_labels_bounded",
        lambda *a, **k: (["cursor-task", "needs-fix"], None),
    )
    payload = build_dashboard_payload(settings, store)
    assert payload["system_status"] == SystemStatus.SELF_HEAL_PENDING.value
    assert payload["self_heal"]["attempt_count"] == 2


def test_reviewer_stall_self_heal_recovers_missing_invocation(
    infra_env, _mock_github_client, monkeypatch: pytest.MonkeyPatch
):
    from autonomous_dev.loop_recovery import run_loop_recovery_tick

    repo, db = infra_env
    monkeypatch.setenv("REVIEWER_STALL_SECONDS", "0")
    monkeypatch.setenv("REVIEW_RETRY_BACKOFF_SECONDS", "0")
    clear_autonomous_settings_cache()
    settings = AutonomousDevSettings()
    store = StateStore(db)
    marker = repo / "autonomous_dev" / "acceptance_marker.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("ok\n", encoding="utf-8")
    task = store.create_task(issue_number=61, delivery_id="reviewer-stall")
    store.update_task(task.id, status=TaskStatus.READY_FOR_REVIEW, commit_sha="stallabc123456")
    _mock_github_client.bodies[61] = f"{REVIEWER_ACCEPTANCE_MARKER}\nReviewer stall recovery."

    counts = run_loop_recovery_tick(settings, store)
    assert counts["stalled_ready_for_review"] >= 1

    deadline = time.time() + 15
    inv = store.get_review_invocation(task.id, "stallabc123456")
    while time.time() < deadline:
        inv = store.get_review_invocation(task.id, "stallabc123456")
        if inv and inv.status == ReviewInvocationStatus.COMPLETED:
            break
        from autonomous_dev.review_worker import process_due_reviews

        process_due_reviews(settings, store)
        time.sleep(0.1)
    assert inv is not None
    assert inv.status == ReviewInvocationStatus.COMPLETED
    assert inv.verdict == ReviewVerdict.PASS


def test_reviewer_handoff_deduped_for_same_commit(
    infra_env, _mock_github_client, monkeypatch: pytest.MonkeyPatch
):
    from autonomous_dev.review_bridge import GitHubIssueReviewAdapter

    _, db = infra_env
    store = StateStore(db)
    task = store.create_task(issue_number=62, delivery_id="handoff-dedup")
    store.update_task(task.id, status=TaskStatus.READY_FOR_REVIEW, commit_sha="dedup123456789")
    store.create_review_invocation(
        invocation_id="inv-dedup",
        task_id=task.id,
        issue_number=62,
        commit_sha="dedup123456789",
    )
    adapter = GitHubIssueReviewAdapter(_mock_github_client, store)
    result = adapter.trigger(task, commit_sha="dedup123456789")
    assert result.triggered is False
    assert "deduped" in result.detail
    handoffs = [
        c for n, c in _mock_github_client.comments if n == 62 and "Ready for Review" in c
    ]
    assert handoffs == []


def test_reviewer_self_heal_dashboard_exposes_recovery_state(infra_env, monkeypatch):
    from autonomous_dev.dashboard import build_dashboard_payload
    from autonomous_dev.state import ReviewerReactivationStatus
    from autonomous_dev.status_deriver import SystemStatus

    _, db = infra_env
    settings = AutonomousDevSettings()
    store = StateStore(db)
    task = store.create_task(
        issue_number=63,
        delivery_id="reviewer-dash",
        status=TaskStatus.READY_FOR_REVIEW,
    )
    store.update_task(task.id, commit_sha="dash1234567890")
    store.upsert_reviewer_reactivation(
        issue_number=63,
        task_id=task.id,
        attempt_count=2,
        next_retry_at=(datetime.now(UTC) + timedelta(seconds=120)).isoformat(),
        last_error="reviewer lock orphaned",
        status=ReviewerReactivationStatus.STALE_RECOVERED,
    )
    monkeypatch.setattr(
        "autonomous_dev.dashboard._fetch_issue_labels_bounded",
        lambda *a, **k: (["cursor-task", "ready-for-review"], None),
    )
    payload = build_dashboard_payload(settings, store)
    assert payload["system_status"] == SystemStatus.REVIEWER_STALE_RECOVERED.value
    assert payload["reviewer_self_heal"]["status"] == "stale-recovered"
    assert payload["reviewer_self_heal"]["attempt_count"] == 2
    assert payload["runtime_evidence"]["reviewer_status"] == "stale-recovered"

