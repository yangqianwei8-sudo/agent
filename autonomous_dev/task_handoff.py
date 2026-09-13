"""Deterministic next-task handoff after reviewer PASS — idempotent, lease-aware."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from enum import StrEnum

from autonomous_dev.config import AutonomousDevSettings
from autonomous_dev.execution_events import ExecutionEventType, record_review_event
from autonomous_dev.github_client import (
    LABEL_CURSOR_TASK,
    GitHubClient,
    GitHubClientError,
)
from autonomous_dev.next_task_resolver import NextTaskOutcome, NextTaskResolver
from autonomous_dev.product_decision import ProductDecisionPacket
from autonomous_dev.state import (
    DeliveryStatus,
    HandoffRecord,
    HandoffStatus,
    StateStore,
    TaskRecord,
    TaskStatus,
)

logger = logging.getLogger(__name__)


class HandoffResultStatus(StrEnum):
    IDEMPOTENT = "idempotent"
    ACTIVATED = "activated"
    WAITING_PRODUCT = "waiting_product"
    NO_NEXT = "no_next"
    STALLED = "stalled"
    RETRY_SCHEDULED = "retry_scheduled"


def compute_handoff_idempotency_key(task_id: int, commit_sha: str) -> str:
    return f"handoff:{task_id}:{commit_sha[:12]}"


class TaskHandoffEngine:
    def __init__(
        self,
        settings: AutonomousDevSettings,
        store: StateStore,
        *,
        github: GitHubClient | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self._github = github or GitHubClient(settings)
        self._resolver = NextTaskResolver(self._github)

    def perform_handoff(
        self,
        task: TaskRecord,
        *,
        commit_sha: str,
        issue_body: str | None = None,
        trigger_worker: bool = True,
    ) -> dict[str, str]:
        """Seal → resolve → activate next task exactly once."""
        idempotency_key = compute_handoff_idempotency_key(task.id, commit_sha)
        existing = self.store.get_handoff_by_key(idempotency_key)
        if existing is not None:
            if existing.status == HandoffStatus.ACTIVATED and existing.next_issue_number:
                self._ensure_worker_for_handoff(existing, trigger_worker=trigger_worker)
            elif existing.status == HandoffStatus.STALLED:
                return self._retry_stalled_handoff(existing, trigger_worker=trigger_worker)
            return self._result_from_record(existing, status=HandoffResultStatus.IDEMPOTENT)

        body = issue_body if issue_body is not None else self._fetch_issue_body(task.issue_number)
        resolution = self._resolver.resolve(
            source_issue_number=task.issue_number,
            source_body=body,
            idempotency_key=idempotency_key,
        )

        if resolution.outcome in {
            NextTaskOutcome.WAITING_PRODUCT,
            NextTaskOutcome.NO_NEXT_DEFINED,
        }:
            record = self.store.create_handoff(
                idempotency_key=idempotency_key,
                source_task_id=task.id,
                source_issue_number=task.issue_number,
                commit_sha=commit_sha,
                status=HandoffStatus.WAITING_PRODUCT,
                reason=resolution.reason,
            )
            self._enter_product_decision(task, resolution.reason)
            return self._result_from_record(record, status=HandoffResultStatus.WAITING_PRODUCT)

        next_issue = resolution.issue_number
        if resolution.outcome == NextTaskOutcome.CREATE_NEW:
            assert resolution.title
            next_issue = self._github.create_issue(
                title=resolution.title,
                body=resolution.body or "",
                labels={LABEL_CURSOR_TASK},
            )

        assert next_issue is not None
        self._github.enforce_single_current_task(next_issue)
        record = self.store.create_handoff(
            idempotency_key=idempotency_key,
            source_task_id=task.id,
            source_issue_number=task.issue_number,
            commit_sha=commit_sha,
            status=HandoffStatus.PENDING,
            next_issue_number=next_issue,
            reason=resolution.reason,
        )
        record = self.store.update_handoff(
            record.handoff_id,
            status=HandoffStatus.ACTIVATED,
            activated_at=datetime.now(UTC).isoformat(),
        )
        record_review_event(
            self.store,
            task=task,
            event_type=ExecutionEventType.REVIEW_FINISHED,
            result_summary=f"handoff activated issue=#{next_issue}",
            status="handoff_activated",
            metadata={"next_issue_number": next_issue},
        )
        if trigger_worker:
            self._kick_worker_for_issue(next_issue, handoff=record)
        return self._result_from_record(record, status=HandoffResultStatus.ACTIVATED)

    def recover_pending_handoffs(self) -> list[dict[str, str]]:
        """Watchdog / restart recovery — activate or retry stalled handoffs."""
        outcomes: list[dict[str, str]] = []
        now = datetime.now(UTC)
        stall_seconds = self.settings.handoff_stall_seconds

        for record in self.store.list_active_handoffs():
            if record.status == HandoffStatus.WAITING_PRODUCT:
                continue
            if record.next_issue_number is None:
                continue

            if self._has_valid_worker_for_issue(record.next_issue_number):
                if record.status != HandoffStatus.WORKER_STARTED:
                    self.store.update_handoff(
                        record.handoff_id,
                        status=HandoffStatus.WORKER_STARTED,
                        worker_started_at=now.isoformat(),
                    )
                continue

            activated_at = _parse_ts(record.activated_at)
            age = (now - activated_at).total_seconds() if activated_at else stall_seconds + 1

            if record.status in {HandoffStatus.PENDING, HandoffStatus.ACTIVATED}:
                if age <= stall_seconds:
                    self._kick_worker_for_issue(record.next_issue_number, handoff=record)
                    outcomes.append(
                        {
                            "handoff_id": record.handoff_id,
                            "status": HandoffResultStatus.RETRY_SCHEDULED.value,
                            "issue_number": str(record.next_issue_number),
                        }
                    )
                else:
                    self.store.update_handoff(record.handoff_id, status=HandoffStatus.STALLED)
                    self._kick_worker_for_issue(record.next_issue_number, handoff=record)
                    outcomes.append(
                        {
                            "handoff_id": record.handoff_id,
                            "status": HandoffResultStatus.STALLED.value,
                            "issue_number": str(record.next_issue_number),
                        }
                    )
            elif record.status == HandoffStatus.STALLED:
                outcomes.append(self._retry_stalled_handoff(record, trigger_worker=True))

        return outcomes

    def get_dashboard_handoff(self) -> HandoffRecord | None:
        return self.store.get_latest_active_handoff()

    def _retry_stalled_handoff(
        self,
        record: HandoffRecord,
        *,
        trigger_worker: bool,
    ) -> dict[str, str]:
        if record.next_issue_number is None:
            return self._result_from_record(record, status=HandoffResultStatus.STALLED)
        self.store.update_handoff(
            record.handoff_id,
            status=HandoffStatus.ACTIVATED,
            retry_count=record.retry_count + 1,
            activated_at=datetime.now(UTC).isoformat(),
        )
        if trigger_worker:
            self._kick_worker_for_issue(record.next_issue_number, handoff=record)
        updated = self.store.get_handoff(record.handoff_id)
        assert updated is not None
        return self._result_from_record(updated, status=HandoffResultStatus.RETRY_SCHEDULED)

    def _ensure_worker_for_handoff(
        self,
        record: HandoffRecord,
        *,
        trigger_worker: bool,
    ) -> None:
        if record.next_issue_number is None or not trigger_worker:
            return
        if not self._has_valid_worker_for_issue(record.next_issue_number):
            self._kick_worker_for_issue(record.next_issue_number, handoff=record)

    def _kick_worker_for_issue(self, issue_number: int, *, handoff: HandoffRecord) -> None:
        from autonomous_dev.task_router import TaskRouter

        if self.store.is_locked():
            lease = self.store.get_lease()
            if lease.issue_number == issue_number and self._lease_is_live():
                return
            logger.info(
                "handoff worker kick deferred issue=#%s — lease held by #%s",
                issue_number,
                lease.issue_number,
            )
            return

        try:
            issues = self._github.list_open_issues_with_label("", limit=100, state="open")
            issue_still_open = any(int(iss.get("number", -1)) == issue_number for iss in issues)
            if not issue_still_open:
                logger.warning(
                    "handoff worker kick skipped issue=#%s — issue is closed/not found",
                    issue_number
                )
                return
            
            body = self._github.get_issue_body(issue_number)
            labels = sorted(self._github.get_issue_labels(issue_number))
        except GitHubClientError as exc:
            logger.warning(
                "handoff worker kick failed to fetch issue=#%s: %s — aborting kick",
                issue_number,
                exc
            )
            return

        payload = {
            "action": "labeled",
            "issue": {
                "number": issue_number,
                "state": "open",
                "body": body,
                "labels": [{"name": n} for n in labels],
                "updated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
        }
        router = TaskRouter(self.settings, self.store)
        delivery_id = f"handoff-{handoff.handoff_id[:8]}-{issue_number}-{handoff.retry_count}"
        if self.store.delivery_exists(delivery_id):
            return
        self.store.record_delivery(
            delivery_id=delivery_id,
            event_type="issues",
            action="labeled",
            payload=payload,
            status=DeliveryStatus.RECEIVED,
        )
        result = router.handle(
            event_type="issues",
            action="labeled",
            delivery_id=delivery_id,
            payload=payload,
        )
        logger.info(
            "handoff worker kick issue=#%s handoff=%s result=%s",
            issue_number,
            handoff.handoff_id,
            result.get("status"),
        )
        if result.get("status") == "worker_started":
            self.store.update_handoff(
                handoff.handoff_id,
                status=HandoffStatus.WORKER_STARTED,
                worker_started_at=datetime.now(UTC).isoformat(),
            )

    def _has_valid_worker_for_issue(self, issue_number: int) -> bool:
        if not self._lease_is_live():
            return False
        lease = self.store.get_lease()
        if lease.issue_number != issue_number:
            return False
        task = self.store.get_running_task_for_issue(issue_number)
        return task is not None and task.status in {TaskStatus.QUEUED, TaskStatus.RUNNING}

    def _lease_is_live(self) -> bool:
        lease = self.store.get_lease()
        if not lease.locked:
            return False
        now_iso = datetime.now(UTC).isoformat()
        if lease.lease_expires_at and lease.lease_expires_at < now_iso:
            return False
        return True

    def _enter_product_decision(self, task: TaskRecord, reason: str) -> None:
        packet = ProductDecisionPacket(
            question="审查通过后，下一步技术任务不明确，需要产品/路线图决策。",
            why_owner_required=reason,
            option_a="指定唯一的下一个 cursor-task（或在其 body 中添加 NEXT_TASK: 标题）",
            impact_a="系统将自动激活该任务并启动 Worker。",
            option_b="保持当前状态，等待路线图更新",
            impact_b="Dashboard 显示 WAITING_PRODUCT_DIRECTION，不会自动发明 V2-P5 等范围。",
            recommended_option="A",
            recommendation_reason="明确路线图后可恢复全自动推进。",
            blocked_task=f"Issue #{task.issue_number} 已 sealed",
        )
        try:
            self._github.sync_product_decision(task.issue_number)
            self._github.add_comment(task.issue_number, packet.to_chinese_markdown())
        except GitHubClientError:
            logger.warning("product decision comment failed issue=#%s", task.issue_number)

    def _fetch_issue_body(self, issue_number: int) -> str:
        try:
            return self._github.get_issue_body(issue_number)
        except GitHubClientError:
            return ""

    @staticmethod
    def _result_from_record(
        record: HandoffRecord,
        *,
        status: HandoffResultStatus,
    ) -> dict[str, str]:
        return {
            "status": status.value,
            "handoff_id": record.handoff_id,
            "handoff_status": record.status.value,
            "next_issue_number": str(record.next_issue_number or ""),
            "reason": record.reason or "",
        }


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except ValueError:
        return None


def derive_handoff_dashboard_state(
    handoff: HandoffRecord | None,
    *,
    has_live_worker: bool,
    stall_seconds: int,
    now: datetime | None = None,
) -> str | None:
    """Return HANDOFF_PENDING | HANDOFF_STALLED | None."""
    if handoff is None:
        return None
    if handoff.status == HandoffStatus.WAITING_PRODUCT:
        return "WAITING_PRODUCT_DIRECTION"
    if handoff.next_issue_number is None:
        return None
    if has_live_worker:
        return None
    now = now or datetime.now(UTC)
    activated = _parse_ts(handoff.activated_at)
    if handoff.status == HandoffStatus.STALLED:
        return "HANDOFF_STALLED"
    if activated is not None:
        age = (now - activated).total_seconds()
        if age > stall_seconds:
            return "HANDOFF_STALLED"
    if handoff.status in {HandoffStatus.PENDING, HandoffStatus.ACTIVATED}:
        return "HANDOFF_PENDING"
    return None

