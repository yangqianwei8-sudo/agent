"""Workflow Runtime — persistent, resumable, retryable orchestration (Phase 3).

Does NOT mutate domain truth tables (Fact/Party/Evidence/Draft).
Does NOT invoke Skills / Tools / LLM.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.domain.enums import (
    CommandStatus,
    NodeRunStatus,
    WorkflowInstanceStatus,
)
from backend.models import (
    NodeRun,
    SystemCommand,
    WorkflowInstance,
    WorkflowNode,
    WorkflowSnapshot,
    WorkflowTemplate,
)
from backend.workflow.dto import RuntimeResult
from backend.workflow.errors import WorkflowConflictError, WorkflowNotFoundError


def _now() -> datetime:
    return datetime.now(UTC)


def _stable_hash(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


TERMINAL_INSTANCE = {
    WorkflowInstanceStatus.SUCCEEDED.value,
    WorkflowInstanceStatus.FAILED.value,
    WorkflowInstanceStatus.CANCELLED.value,
}


class WorkflowRuntime:
    """Sole mutator of WorkflowInstance / NodeRun / WorkflowSnapshot / SystemCommand."""

    def __init__(self, session: Session) -> None:
        self.session = session

    # ------------------------------------------------------------------ create / start

    def create_instance(
        self,
        *,
        case_id: UUID,
        template_code: str = "PLEADING_PREP",
        template_version: int | None = None,
        context: dict[str, Any] | None = None,
    ) -> WorkflowInstance:
        template = self._get_template(template_code, template_version)
        instance = WorkflowInstance(
            case_id=case_id,
            template_id=template.id,
            template_version=template.version,
            status=WorkflowInstanceStatus.PENDING.value,
            current_node_id=None,
            waiting_reason=None,
            context_json=context or {},
        )
        self.session.add(instance)
        self.session.flush()
        return instance

    def start_instance(
        self,
        instance_id: UUID,
        *,
        input_payload: dict[str, Any] | None = None,
    ) -> RuntimeResult:
        instance = self._require_instance(instance_id)
        if instance.status != WorkflowInstanceStatus.PENDING.value:
            raise WorkflowConflictError(
                f"start_instance requires PENDING, got {instance.status}"
            )
        first = self._first_node(instance.template_id)
        instance.status = WorkflowInstanceStatus.RUNNING.value
        instance.current_node_id = first.id
        instance.waiting_reason = None
        instance.updated_at = _now()
        self.session.flush()
        node_run = self.start_node(
            instance_id,
            node_id=first.id,
            input_payload=input_payload or {"case_id": str(instance.case_id)},
        )
        return RuntimeResult(instance=instance, node_run=node_run)

    # ------------------------------------------------------------------ node lifecycle

    def start_node(
        self,
        instance_id: UUID,
        *,
        node_id: UUID | None = None,
        input_payload: dict[str, Any] | None = None,
        reuse_snapshot_ref: str | None = None,
    ) -> NodeRun:
        instance = self._require_instance(instance_id)
        if instance.status != WorkflowInstanceStatus.RUNNING.value:
            raise WorkflowConflictError(
                f"start_node requires RUNNING instance, got {instance.status}"
            )

        target_node_id = node_id or instance.current_node_id
        if target_node_id is None:
            raise WorkflowConflictError("no current node to start")
        node = self._require_node(target_node_id)
        if node.template_id != instance.template_id:
            raise WorkflowConflictError("node does not belong to instance template")

        attempt = self._next_attempt(instance.id, target_node_id)
        if reuse_snapshot_ref:
            snapshot_ref = reuse_snapshot_ref
            snap = self._get_snapshot(reuse_snapshot_ref)
            if snap.instance_id != instance.id:
                raise WorkflowConflictError("snapshot does not belong to instance")
        else:
            payload = dict(input_payload or {})
            payload.setdefault("case_id", str(instance.case_id))
            payload.setdefault("node_id", str(target_node_id))
            payload.setdefault("node_code", node.code)
            snapshot_ref = self._create_snapshot(
                instance_id=instance.id,
                payload=payload,
                confirmation_set_hash=payload.get("confirmation_set_hash"),
            )

        node_run = NodeRun(
            instance_id=instance.id,
            node_id=target_node_id,
            attempt=attempt,
            status=NodeRunStatus.RUNNING.value,
            input_snapshot_ref=snapshot_ref,
            started_at=_now(),
        )
        instance.current_node_id = target_node_id
        instance.status = WorkflowInstanceStatus.RUNNING.value
        instance.waiting_reason = None
        instance.updated_at = _now()
        self.session.add(node_run)
        self.session.flush()
        return node_run

    def complete_node(
        self,
        node_run_id: UUID,
        *,
        output_ref: str | None = None,
        auto_advance: bool = True,
    ) -> RuntimeResult:
        node_run = self._require_node_run(node_run_id)
        instance = self._require_instance(node_run.instance_id)

        # Idempotent: already SUCCEEDED → no-op
        if node_run.status == NodeRunStatus.SUCCEEDED.value:
            return RuntimeResult(
                instance=instance,
                node_run=node_run,
                idempotent_replay=True,
                meta={"reason": "already_succeeded"},
            )

        if node_run.status not in {
            NodeRunStatus.RUNNING.value,
            NodeRunStatus.WAITING_USER.value,
        }:
            raise WorkflowConflictError(
                f"complete_node requires RUNNING/WAITING_USER NodeRun, got {node_run.status}"
            )

        node_run.status = NodeRunStatus.SUCCEEDED.value
        node_run.output_ref = output_ref
        node_run.ended_at = _now()
        instance.updated_at = _now()
        self.session.flush()

        if not auto_advance:
            return RuntimeResult(instance=instance, node_run=node_run)

        return self._advance_after_success(instance, node_run)

    def fail_node(
        self,
        node_run_id: UUID,
        *,
        error_code: str,
        error_detail: str | None = None,
        retryable: bool = True,
    ) -> RuntimeResult:
        node_run = self._require_node_run(node_run_id)
        instance = self._require_instance(node_run.instance_id)

        if node_run.status not in {
            NodeRunStatus.RUNNING.value,
            NodeRunStatus.WAITING_USER.value,
        }:
            raise WorkflowConflictError(
                f"fail_node requires RUNNING/WAITING_USER NodeRun, got {node_run.status}"
            )

        # Truncate detail — never log full evidence text
        detail = (error_detail or "")[:500]
        node_run.status = NodeRunStatus.FAILED.value
        node_run.error_code = error_code[:100]
        node_run.error_detail = detail
        node_run.retryable = retryable
        node_run.ended_at = _now()

        if retryable:
            instance.status = WorkflowInstanceStatus.WAITING_RETRY.value
            instance.waiting_reason = f"retryable:{error_code}"
        else:
            instance.status = WorkflowInstanceStatus.FAILED.value
            instance.waiting_reason = f"fatal:{error_code}"
        instance.updated_at = _now()
        self.session.flush()
        return RuntimeResult(instance=instance, node_run=node_run)

    # ------------------------------------------------------------------ wait / resume

    def wait_for_user(
        self,
        instance_id: UUID,
        *,
        reason: str,
        context: dict[str, Any] | None = None,
    ) -> RuntimeResult:
        instance = self._require_instance(instance_id)
        if instance.status not in {
            WorkflowInstanceStatus.RUNNING.value,
            WorkflowInstanceStatus.WAITING_USER.value,
        }:
            raise WorkflowConflictError(
                f"wait_for_user requires RUNNING/WAITING_USER, got {instance.status}"
            )
        instance.status = WorkflowInstanceStatus.WAITING_USER.value
        instance.waiting_reason = reason
        ctx = dict(instance.context_json or {})
        if context:
            ctx["waiting_context"] = context
        instance.context_json = ctx
        instance.updated_at = _now()
        self.session.flush()
        return RuntimeResult(instance=instance)

    def resume_instance(
        self,
        instance_id: UUID,
        *,
        command_id: UUID,
        input_payload: dict[str, Any] | None = None,
        actor_case_id: UUID | None = None,
    ) -> RuntimeResult:
        instance = self._require_instance(instance_id)
        case_id = actor_case_id or instance.case_id

        existing = self._get_command(command_id)
        if existing is not None:
            return self._replay_command(existing, instance)

        if instance.status != WorkflowInstanceStatus.WAITING_USER.value:
            cmd = self._accept_command(
                command_id=command_id,
                case_id=case_id,
                instance_id=instance_id,
                command_type="RESUME",
                payload={"rejected": True, "reason": f"status={instance.status}"},
            )
            cmd.status = CommandStatus.REJECTED.value
            self.session.flush()
            raise WorkflowConflictError(
                f"resume_instance requires WAITING_USER, got {instance.status}"
            )

        cmd = self._accept_command(
            command_id=command_id,
            case_id=case_id,
            instance_id=instance_id,
            command_type="RESUME",
            payload={"waiting_reason": instance.waiting_reason},
        )

        instance.status = WorkflowInstanceStatus.RUNNING.value
        prev_reason = instance.waiting_reason
        instance.waiting_reason = None
        instance.updated_at = _now()
        self.session.flush()

        # If paused mid-node with RUNNING NodeRun already finished or none:
        # continue current node if no SUCCEEDED run for current node yet; else advance.
        node_run: NodeRun | None = None
        if instance.current_node_id is not None:
            latest = self._latest_node_run(instance.id, instance.current_node_id)
            if latest is None or latest.status == NodeRunStatus.FAILED.value:
                node_run = self.start_node(
                    instance.id,
                    node_id=instance.current_node_id,
                    input_payload=input_payload,
                )
            elif latest.status == NodeRunStatus.RUNNING.value:
                node_run = latest
            elif latest.status == NodeRunStatus.SUCCEEDED.value:
                # Gate completed externally; advance
                advanced = self._advance_after_success(instance, latest)
                node_run = advanced.node_run
                instance = advanced.instance
            elif latest.status == NodeRunStatus.WAITING_USER.value:
                # Resume a node that was waiting inside the node
                latest.status = NodeRunStatus.RUNNING.value
                node_run = latest

        self._complete_command(
            cmd,
            result={
                "instance_id": str(instance.id),
                "instance_status": instance.status,
                "node_run_id": str(node_run.id) if node_run else None,
                "resumed_from": prev_reason,
            },
        )
        return RuntimeResult(
            instance=instance, node_run=node_run, command_id=command_id
        )

    # ------------------------------------------------------------------ retry / rerun

    def retry_node(
        self,
        instance_id: UUID,
        *,
        command_id: UUID,
        node_id: UUID | None = None,
        actor_case_id: UUID | None = None,
    ) -> RuntimeResult:
        """Same business input → new attempt + same immutable snapshot."""
        instance = self._require_instance(instance_id)
        case_id = actor_case_id or instance.case_id

        existing = self._get_command(command_id)
        if existing is not None:
            return self._replay_command(existing, instance)

        if instance.status != WorkflowInstanceStatus.WAITING_RETRY.value:
            cmd = self._accept_command(
                command_id=command_id,
                case_id=case_id,
                instance_id=instance_id,
                command_type="RETRY_NODE",
                payload={"rejected": True},
            )
            cmd.status = CommandStatus.REJECTED.value
            self.session.flush()
            raise WorkflowConflictError(
                f"retry_node requires WAITING_RETRY, got {instance.status}"
            )

        target_node_id = node_id or instance.current_node_id
        if target_node_id is None:
            raise WorkflowConflictError("no node to retry")

        failed = self._latest_failed_retryable(instance.id, target_node_id)
        if failed is None or not failed.input_snapshot_ref:
            raise WorkflowConflictError("no failed retryable NodeRun with snapshot")

        cmd = self._accept_command(
            command_id=command_id,
            case_id=case_id,
            instance_id=instance_id,
            command_type="RETRY_NODE",
            payload={
                "node_id": str(target_node_id),
                "reuse_snapshot_ref": failed.input_snapshot_ref,
                "failed_attempt": failed.attempt,
            },
        )

        instance.status = WorkflowInstanceStatus.RUNNING.value
        instance.waiting_reason = None
        instance.updated_at = _now()
        self.session.flush()

        node_run = self.start_node(
            instance.id,
            node_id=target_node_id,
            reuse_snapshot_ref=failed.input_snapshot_ref,
        )
        self._complete_command(
            cmd,
            result={
                "instance_id": str(instance.id),
                "node_run_id": str(node_run.id),
                "attempt": node_run.attempt,
                "input_snapshot_ref": node_run.input_snapshot_ref,
            },
        )
        return RuntimeResult(instance=instance, node_run=node_run, command_id=command_id)

    def rerun_from_node(
        self,
        instance_id: UUID,
        node_id: UUID,
        *,
        command_id: UUID,
        input_payload: dict[str, Any] | None = None,
        actor_case_id: UUID | None = None,
    ) -> RuntimeResult:
        """New execution with a NEW snapshot from current inputs. History preserved.

        Domain stale invalidation is Application's responsibility (Runtime must not
        UPDATE Fact/Party/Draft tables).
        """
        instance = self._require_instance(instance_id)
        case_id = actor_case_id or instance.case_id

        existing = self._get_command(command_id)
        if existing is not None:
            return self._replay_command(existing, instance)

        if instance.status in TERMINAL_INSTANCE:
            cmd = self._accept_command(
                command_id=command_id,
                case_id=case_id,
                instance_id=instance_id,
                command_type="RERUN_FROM_NODE",
                payload={"rejected": True},
            )
            cmd.status = CommandStatus.REJECTED.value
            self.session.flush()
            raise WorkflowConflictError(
                f"rerun_from_node not allowed on terminal status {instance.status}"
            )

        node = self._require_node(node_id)
        if node.template_id != instance.template_id:
            raise WorkflowConflictError("node does not belong to instance template")

        cmd = self._accept_command(
            command_id=command_id,
            case_id=case_id,
            instance_id=instance_id,
            command_type="RERUN_FROM_NODE",
            payload={"node_id": str(node_id), "node_code": node.code},
        )

        # Cancel any RUNNING node run on this instance without deleting history
        running = self.session.scalars(
            select(NodeRun).where(
                NodeRun.instance_id == instance.id,
                NodeRun.status == NodeRunStatus.RUNNING.value,
            )
        ).all()
        for rn in running:
            rn.status = NodeRunStatus.FAILED.value
            rn.error_code = "SUPERSEDED_BY_RERUN"
            rn.error_detail = "superseded by rerun_from_node"
            rn.retryable = False
            rn.ended_at = _now()

        ctx = dict(instance.context_json or {})
        ctx["last_rerun"] = {
            "node_id": str(node_id),
            "at": _now().isoformat(),
            "note": "domain_invalidate_required_by_application",
        }
        instance.context_json = ctx
        instance.current_node_id = node_id
        instance.status = WorkflowInstanceStatus.RUNNING.value
        instance.waiting_reason = None
        instance.updated_at = _now()
        self.session.flush()

        payload = dict(input_payload or {})
        payload.setdefault("case_id", str(instance.case_id))
        payload["rerun"] = True
        node_run = self.start_node(
            instance.id,
            node_id=node_id,
            input_payload=payload,
        )
        self._complete_command(
            cmd,
            result={
                "instance_id": str(instance.id),
                "node_run_id": str(node_run.id),
                "attempt": node_run.attempt,
                "input_snapshot_ref": node_run.input_snapshot_ref,
            },
        )
        return RuntimeResult(instance=instance, node_run=node_run, command_id=command_id)

    # ------------------------------------------------------------------ cancel

    def cancel_instance(
        self,
        instance_id: UUID,
        *,
        command_id: UUID | None = None,
        actor_case_id: UUID | None = None,
    ) -> RuntimeResult:
        instance = self._require_instance(instance_id)
        case_id = actor_case_id or instance.case_id

        if command_id is not None:
            existing = self._get_command(command_id)
            if existing is not None:
                return self._replay_command(existing, instance)

        # Idempotent cancel
        if instance.status == WorkflowInstanceStatus.CANCELLED.value:
            return RuntimeResult(
                instance=instance,
                command_id=command_id,
                idempotent_replay=True,
                meta={"reason": "already_cancelled"},
            )

        if instance.status in {
            WorkflowInstanceStatus.SUCCEEDED.value,
            WorkflowInstanceStatus.FAILED.value,
        }:
            raise WorkflowConflictError(
                f"cannot cancel terminal status {instance.status}"
            )

        cmd = None
        if command_id is not None:
            cmd = self._accept_command(
                command_id=command_id,
                case_id=case_id,
                instance_id=instance_id,
                command_type="CANCEL",
                payload={},
            )

        running = self.session.scalars(
            select(NodeRun).where(
                NodeRun.instance_id == instance.id,
                NodeRun.status.in_(
                    [NodeRunStatus.RUNNING.value, NodeRunStatus.WAITING_USER.value]
                ),
            )
        ).all()
        for rn in running:
            rn.status = NodeRunStatus.FAILED.value
            rn.error_code = "CANCELLED"
            rn.error_detail = "instance cancelled"
            rn.retryable = False
            rn.ended_at = _now()

        instance.status = WorkflowInstanceStatus.CANCELLED.value
        instance.waiting_reason = "cancelled"
        instance.updated_at = _now()
        self.session.flush()

        if cmd is not None:
            self._complete_command(
                cmd,
                result={
                    "instance_id": str(instance.id),
                    "instance_status": instance.status,
                },
            )
        return RuntimeResult(instance=instance, command_id=command_id)

    # ------------------------------------------------------------------ helpers

    def get_instance(self, instance_id: UUID) -> WorkflowInstance:
        return self._require_instance(instance_id)

    def get_node_run(self, node_run_id: UUID) -> NodeRun:
        return self._require_node_run(node_run_id)

    def get_snapshot_payload(self, snapshot_ref: str) -> dict[str, Any]:
        return dict(self._get_snapshot(snapshot_ref).payload_json)

    def list_node_runs(self, instance_id: UUID, node_id: UUID | None = None) -> list[NodeRun]:
        stmt = select(NodeRun).where(NodeRun.instance_id == instance_id)
        if node_id is not None:
            stmt = stmt.where(NodeRun.node_id == node_id)
        stmt = stmt.order_by(NodeRun.attempt.asc())
        return list(self.session.scalars(stmt))

    def _advance_after_success(
        self, instance: WorkflowInstance, completed: NodeRun
    ) -> RuntimeResult:
        node = self._require_node(completed.node_id)
        nxt = self._next_node(instance.template_id, node.order_index)
        if nxt is None:
            instance.status = WorkflowInstanceStatus.SUCCEEDED.value
            instance.waiting_reason = None
            instance.updated_at = _now()
            self.session.flush()
            return RuntimeResult(instance=instance, node_run=completed)

        instance.current_node_id = nxt.id
        instance.updated_at = _now()
        self.session.flush()

        if nxt.is_human_gate:
            # Persist wait — do not auto-start gate execution
            return self.wait_for_user(
                instance.id,
                reason=nxt.gate_type or nxt.code,
                context={"next_node_id": str(nxt.id), "next_node_code": nxt.code},
            )

        # Auto-start next non-gate node with a fresh snapshot from prior output context
        payload = {
            "case_id": str(instance.case_id),
            "from_node_run_id": str(completed.id),
            "from_output_ref": completed.output_ref,
        }
        node_run = self.start_node(instance.id, node_id=nxt.id, input_payload=payload)
        return RuntimeResult(instance=instance, node_run=node_run)

    def _create_snapshot(
        self,
        *,
        instance_id: UUID,
        payload: dict[str, Any],
        confirmation_set_hash: str | None,
    ) -> str:
        conf = confirmation_set_hash or _stable_hash(payload)
        snap = WorkflowSnapshot(
            instance_id=instance_id,
            payload_json=payload,
            confirmation_set_hash=conf,
        )
        self.session.add(snap)
        self.session.flush()
        return str(snap.id)

    def _get_snapshot(self, snapshot_ref: str) -> WorkflowSnapshot:
        try:
            snap_id = UUID(snapshot_ref)
        except ValueError as exc:
            raise WorkflowNotFoundError(f"invalid snapshot ref: {snapshot_ref}") from exc
        snap = self.session.get(WorkflowSnapshot, snap_id)
        if snap is None:
            raise WorkflowNotFoundError(f"snapshot not found: {snapshot_ref}")
        return snap

    def _next_attempt(self, instance_id: UUID, node_id: UUID) -> int:
        current_max = self.session.scalar(
            select(func.max(NodeRun.attempt)).where(
                NodeRun.instance_id == instance_id, NodeRun.node_id == node_id
            )
        )
        return int(current_max or 0) + 1

    def _latest_node_run(self, instance_id: UUID, node_id: UUID) -> NodeRun | None:
        return self.session.scalars(
            select(NodeRun)
            .where(NodeRun.instance_id == instance_id, NodeRun.node_id == node_id)
            .order_by(NodeRun.attempt.desc())
        ).first()

    def _latest_failed_retryable(self, instance_id: UUID, node_id: UUID) -> NodeRun | None:
        return self.session.scalars(
            select(NodeRun)
            .where(
                NodeRun.instance_id == instance_id,
                NodeRun.node_id == node_id,
                NodeRun.status == NodeRunStatus.FAILED.value,
                NodeRun.retryable.is_(True),
            )
            .order_by(NodeRun.attempt.desc())
        ).first()

    def _first_node(self, template_id: UUID) -> WorkflowNode:
        node = self.session.scalars(
            select(WorkflowNode)
            .where(WorkflowNode.template_id == template_id)
            .order_by(WorkflowNode.order_index.asc())
        ).first()
        if node is None:
            raise WorkflowNotFoundError("template has no nodes")
        return node

    def _next_node(self, template_id: UUID, after_order: int) -> WorkflowNode | None:
        return self.session.scalars(
            select(WorkflowNode)
            .where(
                WorkflowNode.template_id == template_id,
                WorkflowNode.order_index > after_order,
            )
            .order_by(WorkflowNode.order_index.asc())
        ).first()

    def _get_template(self, code: str, version: int | None) -> WorkflowTemplate:
        stmt = select(WorkflowTemplate).where(WorkflowTemplate.code == code)
        if version is not None:
            stmt = stmt.where(WorkflowTemplate.version == version)
        else:
            stmt = stmt.order_by(WorkflowTemplate.version.desc())
        template = self.session.scalars(stmt).first()
        if template is None:
            raise WorkflowNotFoundError(f"template not found: {code}")
        return template

    def _require_instance(self, instance_id: UUID) -> WorkflowInstance:
        instance = self.session.get(WorkflowInstance, instance_id)
        if instance is None:
            raise WorkflowNotFoundError("workflow instance not found")
        return instance

    def _require_node(self, node_id: UUID) -> WorkflowNode:
        node = self.session.get(WorkflowNode, node_id)
        if node is None:
            raise WorkflowNotFoundError("workflow node not found")
        return node

    def _require_node_run(self, node_run_id: UUID) -> NodeRun:
        node_run = self.session.get(NodeRun, node_run_id)
        if node_run is None:
            raise WorkflowNotFoundError("node run not found")
        return node_run

    def _get_command(self, command_id: UUID) -> SystemCommand | None:
        return self.session.get(SystemCommand, command_id)

    def _accept_command(
        self,
        *,
        command_id: UUID,
        case_id: UUID,
        instance_id: UUID,
        command_type: str,
        payload: dict[str, Any],
    ) -> SystemCommand:
        cmd = SystemCommand(
            id=command_id,
            case_id=case_id,
            instance_id=instance_id,
            command_type=command_type,
            status=CommandStatus.ACCEPTED.value,
            payload_json=payload,
        )
        self.session.add(cmd)
        self.session.flush()
        return cmd

    def _complete_command(self, cmd: SystemCommand, *, result: dict[str, Any]) -> None:
        payload = dict(cmd.payload_json or {})
        payload["result"] = result
        cmd.payload_json = payload
        cmd.status = CommandStatus.DONE.value
        cmd.updated_at = _now()
        self.session.flush()

    def _replay_command(
        self, cmd: SystemCommand, instance: WorkflowInstance
    ) -> RuntimeResult:
        # Re-load latest instance state from DB identity
        instance = self._require_instance(instance.id)
        result = (cmd.payload_json or {}).get("result") or {}
        node_run = None
        node_run_id = result.get("node_run_id")
        if node_run_id:
            node_run = self.session.get(NodeRun, UUID(str(node_run_id)))
        return RuntimeResult(
            instance=instance,
            node_run=node_run,
            command_id=cmd.id,
            idempotent_replay=True,
            meta={"command_status": cmd.status, "result": result},
        )
