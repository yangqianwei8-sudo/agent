"""Execute reviewer verdict — exactly-once, idempotent GitHub state transitions."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from autonomous_dev.config import AutonomousDevSettings
from autonomous_dev.execution_events import ExecutionEventType, record_review_event
from autonomous_dev.github_client import (
    LABEL_CURRENT_TASK,
    LABEL_CURSOR_TASK,
    GitHubClient,
    GitHubClientError,
)
from autonomous_dev.product_decision import ProductDecisionPacket
from autonomous_dev.reviewer_service import ReviewerCredentialError, ReviewerService
from autonomous_dev.state import (
    ReviewInvocationRecord,
    ReviewInvocationStatus,
    ReviewVerdict,
    StateStore,
    TaskRecord,
    TaskStatus,
)
from autonomous_dev.task_handoff import TaskHandoffEngine

logger = logging.getLogger(__name__)


class ReviewExecutor:
    def __init__(
        self,
        settings: AutonomousDevSettings,
        store: StateStore,
        *,
        github: GitHubClient | None = None,
        reviewer: ReviewerService | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self._github = github or GitHubClient(settings)
        self._reviewer = reviewer or ReviewerService(settings)

    def schedule_review(self, task: TaskRecord, *, commit_sha: str) -> dict[str, str]:
        """Event-driven entry — exactly one review per (task_id, commit_sha)."""
        existing = self.store.get_review_invocation(task.id, commit_sha)
        if existing and existing.status == ReviewInvocationStatus.COMPLETED:
            return {
                "status": "idempotent",
                "verdict": existing.verdict.value if existing.verdict else "",
                "invocation_id": existing.invocation_id,
            }
        if existing and existing.status in {
            ReviewInvocationStatus.PENDING,
            ReviewInvocationStatus.RUNNING,
        }:
            self._kick_review_worker()
            return {
                "status": "already_scheduled",
                "invocation_id": existing.invocation_id,
            }
        if existing and existing.status == ReviewInvocationStatus.FAILED:
            if self.store.is_review_retryable(
                existing,
                max_attempts=self.settings.review_max_attempts,
            ):
                self.store.reset_review_for_retry(existing.invocation_id)
                self._kick_review_worker()
                return {
                    "status": "retry_scheduled",
                    "invocation_id": existing.invocation_id,
                    "attempt_count": str(existing.attempt_count),
                }
            return {
                "status": "failed_permanent",
                "invocation_id": existing.invocation_id,
                "error": existing.error or "max attempts exceeded",
            }

        invocation_id = str(uuid.uuid4())
        record = self.store.create_review_invocation(
            invocation_id=invocation_id,
            task_id=task.id,
            issue_number=task.issue_number,
            commit_sha=commit_sha,
        )
        if record.invocation_id != invocation_id:
            if record.status == ReviewInvocationStatus.COMPLETED:
                return {
                    "status": "idempotent",
                    "verdict": record.verdict.value if record.verdict else "",
                    "invocation_id": record.invocation_id,
                }
            if record.status == ReviewInvocationStatus.FAILED:
                if self.store.is_review_retryable(
                    record,
                    max_attempts=self.settings.review_max_attempts,
                ):
                    self.store.reset_review_for_retry(record.invocation_id)
                    self._kick_review_worker()
                    return {
                        "status": "retry_scheduled",
                        "invocation_id": record.invocation_id,
                    }
            self._kick_review_worker()
            return {"status": "already_scheduled", "invocation_id": record.invocation_id}

        self._kick_review_worker()
        return {"status": "scheduled", "invocation_id": invocation_id}

    def run_review_sync(
        self,
        task: TaskRecord,
        *,
        commit_sha: str,
        issue_body: str = "",
    ) -> dict[str, str]:
        """Synchronous review for tests."""
        invocation_id = str(uuid.uuid4())
        self.store.create_review_invocation(
            invocation_id=invocation_id,
            task_id=task.id,
            issue_number=task.issue_number,
            commit_sha=commit_sha,
        )
        owner = f"reviewer-sync-{invocation_id[:8]}"
        if not self.store.try_acquire_reviewer_lock(task.id, owner=owner, ttl_seconds=300):
            return {"status": "blocked"}
        try:
            invocation = self.store.get_review_invocation(task.id, commit_sha)
            assert invocation is not None
            return self.execute_review_invocation(
                task,
                invocation,
                issue_body=issue_body or None,
            )
        finally:
            self.store.release_reviewer_lock(owner)

    def execute_review_invocation(
        self,
        task: TaskRecord,
        invocation: ReviewInvocationRecord,
        *,
        issue_body: str | None = None,
    ) -> dict[str, str]:
        return self._execute_review(
            task,
            invocation.commit_sha,
            invocation.invocation_id,
            issue_body=issue_body,
            attempt_count=invocation.attempt_count,
        )

    def _kick_review_worker(self) -> None:
        from autonomous_dev.review_worker import schedule_review_processing

        schedule_review_processing(self.settings, self.store)

    def _execute_review(
        self,
        task: TaskRecord,
        commit_sha: str,
        invocation_id: str,
        *,
        issue_body: str | None = None,
        attempt_count: int = 0,
    ) -> dict[str, str]:
        now = datetime.now(UTC).isoformat()
        self.store.update_review_invocation(
            invocation_id,
            status=ReviewInvocationStatus.RUNNING,
            started_at=now,
            attempt_count=attempt_count + 1,
            clear_next_retry=True,
        )
        record_review_event(
            self.store,
            task=task,
            event_type=ExecutionEventType.REVIEW_STARTED,
            status="running",
            metadata={"invocation_id": invocation_id, "commit_sha": commit_sha[:40]},
        )
        try:
            if issue_body is None:
                issue_body = self._fetch_issue_body(task.issue_number)
            ctx = self._reviewer.gather_context(
                issue_number=task.issue_number,
                issue_body=issue_body,
                commit_sha=commit_sha,
            )
            result = self._reviewer.review(ctx, invocation_id=invocation_id)
            # Persist verdict before GitHub mutations — API review succeeded.
            self.store.update_review_invocation(
                invocation_id,
                status=ReviewInvocationStatus.COMPLETED,
                verdict=ReviewVerdict(result.verdict),
                clear_started_at=True,
            )
            record_review_event(
                self.store,
                task=task,
                event_type=ExecutionEventType.REVIEW_FINISHED,
                result_summary=f"verdict={result.verdict}",
                status="ok",
                metadata={"invocation_id": invocation_id, "verdict": result.verdict},
            )
            self._post_verdict_comment(
                task.issue_number,
                result.verdict,
                result.reason,
                invocation_id,
            )
            try:
                self._apply_verdict(task, commit_sha, result.verdict, result)
            except Exception:  # noqa: BLE001 — GitHub apply must not block queue
                logger.exception(
                    "verdict GitHub apply failed task=%s invocation=%s (verdict persisted)",
                    task.id,
                    invocation_id,
                )
            return {
                "status": "completed",
                "verdict": result.verdict,
                "invocation_id": invocation_id,
            }
        except ReviewerCredentialError as exc:
            self.store.update_review_invocation(
                invocation_id,
                status=ReviewInvocationStatus.FAILED,
                error=str(exc)[:2000],
                attempt_count=self.settings.review_max_attempts,
                clear_started_at=True,
            )
            record_review_event(
                self.store,
                task=task,
                event_type=ExecutionEventType.REVIEW_FINISHED,
                result_summary=str(exc)[:300],
                status="fail",
            )
            self._post_credential_blocker(task.issue_number, str(exc))
            return {"status": "credential_blocker", "error": str(exc)}
        except Exception as exc:  # noqa: BLE001 — review boundary
            logger.exception("review failed task=%s invocation=%s", task.id, invocation_id)
            record_review_event(
                self.store,
                task=task,
                event_type=ExecutionEventType.REVIEW_FINISHED,
                result_summary=str(exc)[:300],
                status="fail",
            )
            return self._mark_transient_failure(invocation_id, attempt_count, exc)

    def _mark_transient_failure(
        self,
        invocation_id: str,
        attempt_count: int,
        exc: Exception,
    ) -> dict[str, str]:
        next_attempt = attempt_count + 1
        error = str(exc)[:2000]
        if next_attempt >= self.settings.review_max_attempts:
            self.store.update_review_invocation(
                invocation_id,
                status=ReviewInvocationStatus.FAILED,
                error=error,
                attempt_count=next_attempt,
                clear_started_at=True,
            )
            return {"status": "failed", "error": error[:500], "retryable": "false"}

        backoff = self.settings.review_retry_backoff_seconds * next_attempt
        next_retry = (datetime.now(UTC) + timedelta(seconds=backoff)).isoformat()
        self.store.update_review_invocation(
            invocation_id,
            status=ReviewInvocationStatus.FAILED,
            error=error,
            attempt_count=next_attempt,
            next_retry_at=next_retry,
            clear_started_at=True,
        )
        return {
            "status": "failed",
            "error": error[:500],
            "retryable": "true",
            "next_retry_at": next_retry,
        }

    def _apply_verdict(self, task: TaskRecord, commit_sha: str, verdict: str, result) -> None:
        if verdict == "PASS":
            self._apply_pass(task, commit_sha, result.reason)
        elif verdict == "PRODUCT_DECISION":
            self._apply_product_decision(task, result)
        elif verdict == "SKIP":
            logger.info("reviewer SKIP task=%s — no state change", task.id)
        else:
            self._apply_fail(task, result)

    def _apply_pass(self, task: TaskRecord, commit_sha: str, reason: str) -> None:
        self._github.sync_completed(task.issue_number)
        self.store.update_task(task.id, status=TaskStatus.COMPLETED, commit_sha=commit_sha)
        try:
            self._github.close_issue(task.issue_number, reason=f"Reviewer PASS: {reason[:200]}")
        except GitHubClientError:
            logger.warning("close issue failed issue=#%s", task.issue_number)
        self._perform_handoff(task, commit_sha)

    def _apply_fail(self, task: TaskRecord, result) -> None:
        self.store.update_task(
            task.id,
            status=TaskStatus.NEEDS_FIX,
            error=result.reason[:2000],
        )
        self._github.sync_needs_fix(task.issue_number)
        if self._is_acceptance_chain_task(task):
            logger.info(
                "acceptance chain FAIL — skip repair spawn issue=#%s",
                task.issue_number,
            )
            return
        repair_num = self._find_or_create_repair_issue(task, result)
        self._github.remove_label(task.issue_number, LABEL_CURRENT_TASK)
        self._github.set_issue_labels(
            repair_num,
            {LABEL_CURSOR_TASK, LABEL_CURRENT_TASK},
        )

    def _is_acceptance_chain_task(self, task: TaskRecord) -> bool:
        try:
            body = self._github.get_issue_body(task.issue_number)
        except GitHubClientError:
            return False
        if "[P0-LIVE-ACCEPTANCE]" in body:
            return True
        for issue in self._github.list_open_issues_with_label(LABEL_CURSOR_TASK, limit=100):
            if int(issue["number"]) == task.issue_number:
                return str(issue.get("title") or "").startswith("[AUTO-")
        return False

    def _apply_product_decision(self, task: TaskRecord, result) -> None:
        pd = result.product_decision or {}
        packet = ProductDecisionPacket(
            question=pd.get("question", "Product scope ambiguity detected by reviewer"),
            why_owner_required=pd.get(
                "why_owner_required",
                "Reviewer cannot autonomously decide product/legal/business scope.",
            ),
            option_a=pd.get("option_a", "Option A — proceed with proposed change"),
            impact_a=pd.get("impact_a", "May affect product boundaries."),
            option_b=pd.get("option_b", "Option B — keep current scope"),
            impact_b=pd.get("impact_b", "Task remains blocked."),
            recommended_option=pd.get("recommended_option", "B"),
            recommendation_reason=pd.get(
                "recommendation_reason",
                "Avoid unilateral product scope changes.",
            ),
            blocked_task=f"Issue #{task.issue_number}",
        )
        self._github.sync_product_decision(task.issue_number)
        self._github.add_comment(task.issue_number, packet.to_chinese_markdown())
        self.store.update_task(
            task.id,
            status=TaskStatus.PRODUCT_DECISION,
            error=packet.to_chinese_markdown()[:2000],
        )

    def _find_or_create_repair_issue(self, task: TaskRecord, result) -> int:
        title_prefix = f"[REPAIR] Issue #{task.issue_number}"
        existing = self._github.find_open_issue_by_title_prefix(title_prefix)
        summary = result.fail_repair_summary or result.reason
        body = (
            f"## Repair task (reviewer FAIL)\n\n"
            f"Original issue: #{task.issue_number}\n\n"
            f"**Required fix:** {summary}\n\n"
            f"Labels: `cursor-task` + `current-task`"
        )
        if existing:
            self._github.update_issue_body(existing, body)
            return existing
        return self._github.create_issue(
            title=f"{title_prefix}: {summary[:60]}",
            body=body,
            labels={LABEL_CURSOR_TASK, LABEL_CURRENT_TASK},
        )

    def _perform_handoff(self, task: TaskRecord, commit_sha: str) -> None:
        try:
            body = self._fetch_issue_body(task.issue_number)
            engine = TaskHandoffEngine(self.settings, self.store, github=self._github)
            engine.perform_handoff(task, commit_sha=commit_sha, issue_body=body)
        except Exception:  # noqa: BLE001 — handoff must not block review completion
            logger.exception("task handoff failed task=%s", task.id)

    def _fetch_issue_body(self, issue_number: int) -> str:
        try:
            return self._github.get_issue_body(issue_number)
        except GitHubClientError:
            return ""

    def _post_verdict_comment(
        self,
        issue_number: int,
        verdict: str,
        reason: str,
        invocation_id: str,
    ) -> None:
        body = (
            f"## Reviewer Verdict: **{verdict}**\n\n"
            f"- invocation_id: `{invocation_id}`\n"
            f"- reason: {reason[:1500]}\n"
        )
        try:
            self._github.add_comment(issue_number, body)
        except GitHubClientError as exc:
            logger.error("verdict comment failed issue=#%s: %s", issue_number, exc)

    def _post_credential_blocker(self, issue_number: int, message: str) -> None:
        body = (
            "## Reviewer Credential Blocker\n\n"
            f"{message}\n\n"
            "Configure `OPENAI_API_KEY` (reviewer-only; not LLM_API_KEY) — "
            "will not substitute Cursor or worker LLM as independent reviewer."
        )
        try:
            self._github.add_comment(issue_number, body)
        except GitHubClientError:
            pass
