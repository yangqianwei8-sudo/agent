"""Cursor/Code Agent worker — executes one GitHub Issue as SSOT."""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from autonomous_dev.config import AutonomousDevSettings
from autonomous_dev.execution_events import ExecutionEventRecorder
from autonomous_dev.github_auth import git_env
from autonomous_dev.github_client import GitHubClient, GitHubClientError
from autonomous_dev.product_decision import ProductDecisionPacket
from autonomous_dev.review_bridge import ReviewBridge
from autonomous_dev.review_handoff import transition_ready_for_review
from autonomous_dev.state import StateStore, TaskRecord, TaskStatus

logger = logging.getLogger(__name__)

PRODUCT_DECISION_MARKER = "[PRODUCT-DECISION]"
CURSOR_RUNTIME_ACCEPTANCE_MARKER = "[CURSOR-RUNTIME-ACCEPTANCE]"
P0_LIVE_ACCEPTANCE_MARKER = "[P0-LIVE-ACCEPTANCE]"
CURSOR_RUNTIME_OK_MARKER = "CURSOR_AGENT_RUNTIME_OK"


@dataclass
class WorkerResult:
    task_id: int
    status: TaskStatus
    commit_sha: str | None = None
    error: str | None = None
    product_decision: ProductDecisionPacket | None = None


class Worker:
    def __init__(
        self,
        settings: AutonomousDevSettings,
        store: StateStore,
        *,
        repo_root: Path | None = None,
        review_bridge: ReviewBridge | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.repo_root = repo_root or settings.repo_root
        self._github = GitHubClient(settings)
        self._review_bridge = review_bridge or ReviewBridge(settings, store)

    def run_task(
        self,
        task: TaskRecord,
        *,
        issue_body: str = "",
        lease_owner: str | None = None,
    ) -> WorkerResult:
        owner = lease_owner or f"worker-{task.id}"
        events = ExecutionEventRecorder(self.store, task, self.repo_root)
        stop_heartbeat = threading.Event()
        heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            args=(owner, stop_heartbeat),
            name=f"lease-heartbeat-{task.id}",
            daemon=True,
        )
        heartbeat_thread.start()
        try:
            events.task_started()
            self.store.update_task(task.id, status=TaskStatus.RUNNING)
            self._github.sync_worker_running(task.issue_number)

            if PRODUCT_DECISION_MARKER in issue_body:
                packet = self._build_product_decision_packet(task, issue_body)
                self._github.sync_product_decision(task.issue_number)
                self._github.add_comment(task.issue_number, packet.to_markdown())
                self.store.update_task(
                    task.id,
                    status=TaskStatus.PRODUCT_DECISION,
                    error=packet.to_markdown()[:2000],
                )
                return WorkerResult(
                    task_id=task.id,
                    status=TaskStatus.PRODUCT_DECISION,
                    product_decision=packet,
                )

            if self.settings.autonomous_worker_mode == "cursor_sdk":
                self.settings.validate_cursor_sdk_config()
                events.analysis_started()
                if CURSOR_RUNTIME_ACCEPTANCE_MARKER in issue_body:
                    commit_sha = self._run_cursor_agent_controlled(task, issue_body, events=events)
                elif P0_LIVE_ACCEPTANCE_MARKER in issue_body:
                    commit_sha = self._run_p0_live_acceptance(task, issue_body, events=events)
                else:
                    commit_sha = self._run_cursor_agent(task, issue_body, events=events)
            else:
                self._git_fetch(events)
                self._ensure_clean_or_resolve()
                self._apply_harmless_change(task.issue_number, events=events)
                self._run_tests(events)
                commit_sha = self._commit_and_push(task.issue_number, events=events)
                self._verify_push(commit_sha)

            if self._should_handoff_after_push(commit_sha):
                self._git_fetch(events)
                self._verify_push(commit_sha)
                fresh = self.store.get_task(task.id)
                if fresh is not None and fresh.status == TaskStatus.FAILED:
                    self.store.update_task(
                        task.id,
                        status=TaskStatus.RUNNING,
                        error=None,
                        commit_sha=commit_sha,
                    )
                updated = transition_ready_for_review(
                    self.store,
                    self._github,
                    self._review_bridge,
                    task,
                    commit_sha,
                )
                events.task_finished(status=TaskStatus.READY_FOR_REVIEW)
                return WorkerResult(
                    task_id=updated.id,
                    status=TaskStatus.READY_FOR_REVIEW,
                    commit_sha=commit_sha,
                )

            updated = self.store.update_task(
                task.id,
                status=TaskStatus.RUNNING,
                commit_sha=commit_sha,
            )
            events.task_finished(status=TaskStatus.RUNNING)
            return WorkerResult(
                task_id=updated.id,
                status=TaskStatus.RUNNING,
                commit_sha=commit_sha,
            )
        except (GitHubClientError, Exception) as exc:  # noqa: BLE001 — worker boundary
            logger.exception("worker failed task=%s", task.id)
            try:
                self._github.sync_needs_fix(task.issue_number)
            except GitHubClientError as label_exc:
                logger.error("needs-fix label sync failed task=%s: %s", task.id, label_exc)
            self.store.update_task(
                task.id,
                status=TaskStatus.NEEDS_FIX,
                error=str(exc)[:2000],
            )
            events.task_failed(error=str(exc)[:500])
            return WorkerResult(
                task_id=task.id,
                status=TaskStatus.NEEDS_FIX,
                error=str(exc),
            )
        finally:
            stop_heartbeat.set()
            heartbeat_thread.join(timeout=2)
            self.store.release_lease(owner)

    def _should_handoff_after_push(self, commit_sha: str) -> bool:
        if self.settings.autonomous_worker_mode == "deterministic":
            return False
        if not commit_sha or commit_sha == CURSOR_RUNTIME_OK_MARKER:
            return False
        return len(commit_sha) >= 12

    def _heartbeat_loop(self, owner: str, stop: threading.Event) -> None:
        interval = self.settings.worker_heartbeat_interval_seconds
        while not stop.wait(interval):
            if not self.store.heartbeat_lease(
                owner, ttl_seconds=self.settings.worker_lease_ttl_seconds
            ):
                logger.warning("lease heartbeat failed owner=%s", owner)
                break

    def _build_product_decision_packet(
        self, task: TaskRecord, issue_body: str
    ) -> ProductDecisionPacket:
        return ProductDecisionPacket(
            question="Should the lawyer workflow boundary change for this task?",
            why_owner_required="Issue body contains [PRODUCT-DECISION] marker.",
            option_a="Proceed with proposed workflow change",
            impact_a="May affect lawyer confirmation boundaries.",
            option_b="Keep current workflow unchanged",
            impact_b="Task remains blocked until re-scoped.",
            recommended_option="Option B until owner confirms.",
            blocked_task=f"Issue #{task.issue_number}",
        )

    def _run(self, cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        logger.info("worker cmd: %s", " ".join(cmd))
        return subprocess.run(
            cmd,
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=check,
            env=git_env(),
        )

    def _cursor_agent_options(self):
        from cursor_sdk import AgentOptions, LocalAgentOptions

        return AgentOptions(
            api_key=self.settings.cursor_api_key,
            model=self.settings.cursor_model,
            local=LocalAgentOptions(cwd=str(self.repo_root)),
        )

    def _invoke_cursor_agent(self, prompt: str, *, events: ExecutionEventRecorder | None = None):
        from cursor_sdk import Agent, CursorAgentError

        from autonomous_dev.cursor_telemetry import record_cursor_stream_event

        agent = None
        result = None
        if events:
            events.cursor_started()
        try:
            agent = Agent.create(self._cursor_agent_options())
            run = agent.send(prompt)
            for stream_event in run.events():
                if events:
                    record_cursor_stream_event(events, stream_event)
            result = run.wait()
        except CursorAgentError as exc:
            if events:
                events.record(
                    "CURSOR_FINISHED",
                    phase="cursor",
                    result_summary=str(exc)[:300],
                    status="fail",
                )
            raise RuntimeError(str(exc)) from exc
        finally:
            if agent is not None:
                agent.close()
        if result is None:
            raise RuntimeError("cursor agent returned no result")
        if result.status == "error":
            if events:
                events.record(
                    "CURSOR_FINISHED",
                    phase="cursor",
                    result_summary=str(result.result)[:300],
                    status="fail",
                )
            raise RuntimeError(f"cursor agent failed: {result.result}")
        if events:
            events.cursor_finished(result_summary=(result.result or "")[:300])
        return result

    def _run_cursor_agent_controlled(
        self, task: TaskRecord, issue_body: str, *, events: ExecutionEventRecorder
    ) -> str:
        prompt = (
            "connectivity/controlled acceptance only; do not modify files.\n"
            f"Issue #{task.issue_number} context:\n{issue_body}\n\n"
            f"Reply with exactly: {CURSOR_RUNTIME_OK_MARKER}"
        )
        result = self._invoke_cursor_agent(prompt, events=events)
        output = (result.result or "").strip()
        if CURSOR_RUNTIME_OK_MARKER not in output:
            raise RuntimeError(
                f"controlled acceptance failed: expected {CURSOR_RUNTIME_OK_MARKER}, got: {output[:200]}"
            )
        return CURSOR_RUNTIME_OK_MARKER

    def _run_p0_live_acceptance(
        self, task: TaskRecord, issue_body: str, *, events: ExecutionEventRecorder
    ) -> str:
        """P0 live path: cursor_sdk invoked + harmless change + tests + commit + push."""
        self._git_fetch(events)
        self._ensure_clean_or_resolve()
        self._apply_harmless_change(task.issue_number, events=events)
        prompt = (
            f"P0 live acceptance for Issue #{task.issue_number}. "
            "Harmless marker file updated. Do not modify other files. "
            f"Reply with exactly: {CURSOR_RUNTIME_OK_MARKER}\n\n{issue_body}"
        )
        self._invoke_cursor_agent(prompt, events=events)
        self._run_tests(events)
        commit_sha = self._commit_and_push(task.issue_number, events=events)
        self._verify_push(commit_sha)
        return commit_sha

    def _run_cursor_agent(
        self, task: TaskRecord, issue_body: str, *, events: ExecutionEventRecorder
    ) -> str:
        prompt = (
            f"Execute GitHub Issue #{task.issue_number} as sole SSOT.\n\n"
            f"{issue_body}\n\n"
            "Rules: run tests, git add -A, commit, push origin main, "
            "verify HEAD==origin/main, stop. No next product phase."
        )
        self._invoke_cursor_agent(prompt, events=events)
        head = self._run(["git", "rev-parse", "HEAD"], check=True)
        return head.stdout.strip()

    def _git_fetch(self, events: ExecutionEventRecorder | None = None) -> None:
        if self.settings.autonomous_worker_mode == "deterministic":
            return
        cmd = ["git", "fetch", "origin", "main"]
        if events:
            events.command_started(cmd, phase="git")
        result = self._run(cmd)
        if events:
            events.command_finished(cmd, output=result.stdout[:200], ok=True, phase="git")

    def _ensure_clean_or_resolve(self) -> None:
        status = self._run(["git", "status", "--porcelain"], check=True)
        if status.stdout.strip():
            if self.settings.autonomous_worker_mode == "deterministic":
                self._run(["git", "checkout", "--", "."], check=False)
                self._run(["git", "clean", "-fd", "data/"], check=False)
                return
            raise RuntimeError(f"working tree not clean: {status.stdout.strip()[:500]}")

    def _apply_harmless_change(self, issue_number: int, *, events: ExecutionEventRecorder | None = None) -> None:
        marker = self.repo_root / "autonomous_dev" / "acceptance_marker.txt"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            f"worker-run issue={issue_number} at={datetime.now(UTC).isoformat()}\n",
            encoding="utf-8",
        )
        if events:
            events.file_edit(marker)

    def _run_ruff(self, events: ExecutionEventRecorder | None = None) -> None:
        cmd = [sys.executable, "-m", "ruff", "check", "."]
        if events:
            events.ruff_started(cmd)
        result = self._run(cmd, check=False)
        ok = result.returncode == 0
        if events:
            events.ruff_finished(output=result.stdout + result.stderr, passed=ok)
        if not ok:
            raise RuntimeError(f"ruff failed: {(result.stdout + result.stderr)[:500]}")

    def _run_tests(self, events: ExecutionEventRecorder | None = None) -> None:
        cmd = [sys.executable, "-m", "pytest", "backend/tests/test_health.py", "-q"]
        if events:
            events.test_started(cmd)
        result = self._run(cmd, check=True)
        if events:
            events.test_finished(output=result.stdout + result.stderr, passed=True)
        self._run_ruff(events)

    def _ensure_git_identity(self) -> None:
        if not self._run(["git", "config", "user.email"], check=False).stdout.strip():
            self._run(
                ["git", "config", "user.email", "autonomous-dev@sealos.local"],
                check=False,
            )
        if not self._run(["git", "config", "user.name"], check=False).stdout.strip():
            self._run(["git", "config", "user.name", "autonomous-dev-bot"], check=False)

    def _commit_and_push(
        self, issue_number: int, *, events: ExecutionEventRecorder | None = None
    ) -> str:
        self._ensure_git_identity()
        self._run(["git", "add", "-A"])
        msg = f"chore: autonomous worker update for issue #{issue_number}"
        diff_stat = self._run(["git", "diff", "--stat", "--cached"], check=False)
        if events and diff_stat.stdout.strip():
            events.git_diff(stat_output=diff_stat.stdout)
        diff = self._run(["git", "diff", "--cached", "--quiet"], check=False)
        if diff.returncode == 0:
            head = self._run(["git", "rev-parse", "HEAD"], check=True)
            return head.stdout.strip()

        if events:
            events.commit_started(msg)
        self._run(["git", "commit", "-m", msg])
        head = self._run(["git", "rev-parse", "HEAD"], check=True)
        commit_sha = head.stdout.strip()
        if events:
            events.commit_created(sha=commit_sha, message=msg)

        if self.settings.autonomous_worker_mode == "deterministic":
            return commit_sha

        from autonomous_dev.github_auth import resolve_github_auth

        auth = resolve_github_auth()
        if auth.mode == "none":
            raise RuntimeError(
                "GitHub auth not configured — set GITHUB_TOKEN (Sealos Secret) "
                "or GITHUB_APP_* / GITHUB_SSH_KEY_PATH (see docs/AUTONOMOUS_DEV_RUNBOOK.md)"
            )

        if events:
            events.push_started()
        self._run(["git", "push", "origin", "main"])
        if events:
            events.push_finished(sha=commit_sha)
        return commit_sha

    def _verify_push(self, local_head: str) -> None:
        if self.settings.autonomous_worker_mode == "deterministic":
            return
        # Local origin/main ref is stale immediately after push — refresh before compare.
        self._run(["git", "fetch", "origin", "main"], check=False)
        remote = self._run(["git", "rev-parse", "origin/main"], check=True)
        if remote.stdout.strip() != local_head:
            raise RuntimeError(
                f"push verification failed local={local_head} origin={remote.stdout.strip()}"
            )
