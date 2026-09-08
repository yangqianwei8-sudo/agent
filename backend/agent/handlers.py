"""Agent command handlers — call Application/Runtime only (never ORM Domain writes)."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.agent.context import CaseContext, CaseContextService
from backend.agent.dto import (
    AgentAction,
    AgentError,
    AgentErrorCode,
    AgentIntent,
    AgentResponse,
    IntentResult,
)
from backend.agent.resolver import TargetResolver
from backend.application.case_analyst import CaseAnalystService
from backend.application.claim_direction import ClaimDirectionService
from backend.application.evidence_organizer import EvidenceOrganizerService
from backend.application.pleading_writer import PleadingWriterService
from backend.domain.errors import ConflictError, DomainError, NotFoundError, ValidationError
from backend.domain.services import DomainService
from backend.models import (
    CaseMaterial,
    CaseParty,
    ClaimDirection,
    EvidenceItem,
    ExtractedContent,
    Fact,
    NodeRun,
    SystemCommand,
)
from backend.workflow.errors import WorkflowConflictError, WorkflowError
from backend.workflow.runtime import WorkflowRuntime


def _now() -> datetime:
    return datetime.now(UTC)


def stable_command_id(*parts: Any) -> UUID:
    blob = json.dumps([str(p) for p in parts], ensure_ascii=False, sort_keys=False)
    digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    return UUID(digest[:32])


@dataclass
class HandlerResult:
    message: str
    intent: AgentIntent
    warnings: list[str] = field(default_factory=list)
    references: list[dict[str, Any]] = field(default_factory=list)
    error_code: AgentErrorCode | None = None
    command_id: UUID | None = None
    idempotent_replay: bool = False
    related_decision_id: UUID | None = None


class CommandHandler:
    def __init__(
        self,
        session: Session,
        *,
        actor_id: UUID,
        organizer: EvidenceOrganizerService | None = None,
        analyst: CaseAnalystService | None = None,
        claim_svc: ClaimDirectionService | None = None,
        writer: PleadingWriterService | None = None,
    ) -> None:
        self.session = session
        self.actor_id = actor_id
        self.runtime = WorkflowRuntime(session)
        self.domain = DomainService(session)
        self.context_svc = CaseContextService(session)
        self.organizer = organizer or EvidenceOrganizerService(session)
        self.analyst = analyst or CaseAnalystService(session)
        self.claim_svc = claim_svc or ClaimDirectionService(session)
        self.writer = writer or PleadingWriterService(session)

    def dispatch(
        self,
        *,
        ctx: CaseContext,
        intent: IntentResult,
    ) -> HandlerResult:
        mapping = {
            AgentIntent.START_CASE_WORKFLOW: self._start,
            AgentIntent.CONTINUE: self._continue,
            AgentIntent.STATUS: self._status,
            AgentIntent.PAUSE: self._pause,
            AgentIntent.RESUME: self._resume,
            AgentIntent.RETRY: self._retry,
            AgentIntent.ORGANIZE_EVIDENCE: self._organize,
            AgentIntent.ACCEPT_EVIDENCE: self._accept_evidence,
            AgentIntent.EXCLUDE_EVIDENCE: self._exclude_evidence,
            AgentIntent.CONFIRM_PARTY: self._confirm_party,
            AgentIntent.AMEND_PARTY: self._amend_party,
            AgentIntent.CONFIRM_FACT: self._confirm_fact,
            AgentIntent.REJECT_FACT: self._reject_fact,
            AgentIntent.AMEND_FACT: self._amend_fact,
            AgentIntent.CONFIRM_CLAIM_DIRECTION: self._confirm_claim,
            AgentIntent.REJECT_CLAIM_DIRECTION: self._reject_claim,
            AgentIntent.AMEND_CLAIM_DIRECTION: self._amend_claim,
            AgentIntent.GENERATE_COMPLAINT: self._generate_complaint,
            AgentIntent.REVIEW_DRAFT: self._show_draft,
            AgentIntent.APPROVE_DRAFT: self._approve_draft,
            AgentIntent.SHOW_EVIDENCE: self._show_evidence,
            AgentIntent.SHOW_FACTS: self._show_facts,
            AgentIntent.SHOW_CLAIMS: self._show_claims,
            AgentIntent.SHOW_DRAFT: self._show_draft,
            AgentIntent.UNKNOWN: self._unknown,
        }
        fn = mapping.get(intent.intent, self._unknown)
        try:
            return fn(ctx, intent)
        except AgentError as exc:
            return HandlerResult(
                message=exc.message,
                intent=intent.intent,
                error_code=exc.code,
            )
        except (ValidationError, ConflictError, NotFoundError, DomainError) as exc:
            code = AgentErrorCode.VALIDATION_ERROR
            if isinstance(exc, NotFoundError):
                code = AgentErrorCode.NOT_FOUND
            elif isinstance(exc, ConflictError):
                code = AgentErrorCode.INVALID_WORKFLOW_STATE
            return HandlerResult(
                message=getattr(exc, "message", str(exc)),
                intent=intent.intent,
                error_code=code,
            )
        except WorkflowConflictError as exc:
            return HandlerResult(
                message=str(exc),
                intent=intent.intent,
                error_code=AgentErrorCode.INVALID_WORKFLOW_STATE,
            )
        except WorkflowError as exc:
            return HandlerResult(
                message=str(exc),
                intent=intent.intent,
                error_code=AgentErrorCode.INTERNAL_ERROR,
            )

    # ----- SystemCommand helpers -----

    def _begin_cmd(
        self,
        command_id: UUID,
        *,
        case_id: UUID,
        instance_id: UUID | None,
        command_type: str,
        payload: dict[str, Any],
    ) -> tuple[SystemCommand | None, bool]:
        existing = self.session.get(SystemCommand, command_id)
        if existing is not None:
            return existing, True
        cmd = SystemCommand(
            id=command_id,
            case_id=case_id,
            instance_id=instance_id,
            command_type=command_type,
            status="ACCEPTED",
            payload_json=payload,
        )
        self.session.add(cmd)
        self.session.flush()
        return cmd, False

    def _finish_cmd(self, cmd: SystemCommand, result: dict[str, Any]) -> None:
        payload = dict(cmd.payload_json or {})
        payload["result"] = result
        cmd.payload_json = payload
        cmd.status = "DONE"
        cmd.updated_at = _now()
        self.session.flush()

    def _replay_msg(self, cmd: SystemCommand, intent: AgentIntent) -> HandlerResult:
        result = (cmd.payload_json or {}).get("result") or {}
        return HandlerResult(
            message=str(result.get("message") or "命令已执行过（幂等重放）。"),
            intent=intent,
            command_id=cmd.id,
            idempotent_replay=True,
            references=list(result.get("references") or []),
            warnings=list(result.get("warnings") or []),
        )

    # ----- intents -----

    def _unknown(self, ctx: CaseContext, intent: IntentResult) -> HandlerResult:
        reason = (intent.parameters or {}).get("reason")
        if reason == "ambiguous":
            return HandlerResult(
                message="请使用明确指令，例如「确认事实1」「接受证据2」「批准这份起诉状」。",
                intent=AgentIntent.UNKNOWN,
                error_code=AgentErrorCode.UNKNOWN_INTENT,
            )
        return HandlerResult(
            message="未识别意图。可以说：开始处理案件 / 继续 / 状态 / 暂停 / 查看证据。",
            intent=AgentIntent.UNKNOWN,
            error_code=AgentErrorCode.UNKNOWN_INTENT,
        )

    def _status(self, ctx: CaseContext, intent: IntentResult) -> HandlerResult:
        return HandlerResult(message=self._status_text(ctx), intent=AgentIntent.STATUS)

    def _start(self, ctx: CaseContext, intent: IntentResult) -> HandlerResult:
        if ctx.instance is not None:
            code = ctx.current_node.code if ctx.current_node else "?"
            # Active = non-terminal only for "already running" check
            if ctx.workflow_status not in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                return HandlerResult(
                    message=(
                        f"案件已有进行中的工作流（状态 {ctx.workflow_status}，"
                        f"当前节点 {code}），未重复创建。"
                    ),
                    intent=AgentIntent.START_CASE_WORKFLOW,
                )
        cmd_id = stable_command_id("START", ctx.case.id)
        cmd, replay = self._begin_cmd(
            cmd_id,
            case_id=ctx.case.id,
            instance_id=None,
            command_type="START_CASE_WORKFLOW",
            payload={},
        )
        if replay and cmd is not None:
            return self._replay_msg(cmd, AgentIntent.START_CASE_WORKFLOW)

        from backend.models import WorkflowNode
        from backend.workflow.seed import ensure_pleading_prep_template

        ensure_pleading_prep_template(self.session)
        inst = self.runtime.create_instance(case_id=ctx.case.id)
        result = self.runtime.start_instance(inst.id)
        node = (
            self.session.get(WorkflowNode, result.instance.current_node_id)
            if result.instance.current_node_id
            else None
        )
        msg = (
            f"已创建并启动 PLEADING_PREP 工作流。"
            f"当前节点：{node.code if node else 'N0_CREATE'}。"
        )
        assert cmd is not None
        self._finish_cmd(
            cmd,
            {
                "message": msg,
                "instance_id": str(result.instance.id),
            },
        )
        cmd.instance_id = result.instance.id
        self.session.flush()
        return HandlerResult(
            message=msg,
            intent=AgentIntent.START_CASE_WORKFLOW,
            command_id=cmd_id,
            references=[{"workflow_instance_id": str(result.instance.id)}],
        )

    def _pause(self, ctx: CaseContext, intent: IntentResult) -> HandlerResult:
        if ctx.instance is None:
            raise AgentError("尚无工作流可暂停", code=AgentErrorCode.INVALID_WORKFLOW_STATE)
        if ctx.waiting_reason == "user_pause":
            code = ctx.current_node.code if ctx.current_node else "?"
            return HandlerResult(
                message=f"案件工作已暂停。当前停在 {code}。",
                intent=AgentIntent.PAUSE,
            )
        cmd_id = stable_command_id("PAUSE", ctx.instance.id, ctx.workflow_status)
        cmd, replay = self._begin_cmd(
            cmd_id,
            case_id=ctx.case.id,
            instance_id=ctx.instance.id,
            command_type="PAUSE",
            payload={"reason": "user_pause"},
        )
        if replay and cmd is not None:
            return self._replay_msg(cmd, AgentIntent.PAUSE)
        self.runtime.wait_for_user(ctx.instance.id, reason="user_pause")
        code = ctx.current_node.code if ctx.current_node else "?"
        msg = f"案件工作已暂停。当前停在 {code}。"
        assert cmd is not None
        self._finish_cmd(cmd, {"message": msg})
        return HandlerResult(message=msg, intent=AgentIntent.PAUSE, command_id=cmd_id)

    def _resume(self, ctx: CaseContext, intent: IntentResult) -> HandlerResult:
        if ctx.instance is None:
            raise AgentError("尚无工作流可恢复", code=AgentErrorCode.INVALID_WORKFLOW_STATE)
        if ctx.waiting_reason != "user_pause":
            # Business human gate — do not bypass
            gate = ctx.waiting_reason
            if not gate and ctx.current_node:
                gate = ctx.current_node.code
            return HandlerResult(
                message=(
                    f"当前停在人工确认门（{gate}），"
                    "「恢复」不会跳过律师确认。"
                    "请查看待确认项并使用明确确认指令，或说「继续」查看状态。"
                ),
                intent=AgentIntent.RESUME,
                error_code=AgentErrorCode.HUMAN_GATE_REQUIRED,
            )
        cmd_id = stable_command_id("RESUME_PAUSE", ctx.instance.id)
        result = self.runtime.resume_instance(ctx.instance.id, command_id=cmd_id)
        code = ctx.current_node.code if ctx.current_node else "?"
        # Reload node after resume
        from backend.models import WorkflowNode

        node = (
            self.session.get(WorkflowNode, result.instance.current_node_id)
            if result.instance.current_node_id
            else None
        )
        msg = (
            f"已从用户暂停恢复。当前节点：{node.code if node else code}，"
            f"状态：{result.instance.status}。"
        )
        return HandlerResult(
            message=msg,
            intent=AgentIntent.RESUME,
            command_id=cmd_id,
            idempotent_replay=result.idempotent_replay,
        )

    def _retry(self, ctx: CaseContext, intent: IntentResult) -> HandlerResult:
        if ctx.instance is None:
            raise AgentError("尚无工作流", code=AgentErrorCode.INVALID_WORKFLOW_STATE)
        if ctx.workflow_status != "WAITING_RETRY":
            raise AgentError(
                f"当前状态不是 WAITING_RETRY（{ctx.workflow_status}）",
                code=AgentErrorCode.INVALID_WORKFLOW_STATE,
            )
        cmd_id = stable_command_id("RETRY", ctx.instance.id, ctx.instance.current_node_id)
        result = self.runtime.retry_node(ctx.instance.id, command_id=cmd_id)
        return HandlerResult(
            message=(
                f"已重试当前节点（复用原 input snapshot）。"
                f"node_run={result.node_run.id if result.node_run else None}"
            ),
            intent=AgentIntent.RETRY,
            command_id=cmd_id,
            idempotent_replay=result.idempotent_replay,
        )

    def _continue(self, ctx: CaseContext, intent: IntentResult) -> HandlerResult:
        if ctx.instance is None:
            raise AgentError(
                "尚无工作流。请先说「开始处理这个案件」。",
                code=AgentErrorCode.INVALID_WORKFLOW_STATE,
            )
        if ctx.waiting_reason == "user_pause":
            raise AgentError(
                "案件处于用户暂停。请先说「恢复」。",
                code=AgentErrorCode.INVALID_WORKFLOW_STATE,
            )
        code = ctx.current_node.code if ctx.current_node else None
        if code is None:
            raise AgentError("工作流无当前节点", code=AgentErrorCode.INVALID_WORKFLOW_STATE)

        if code == "N0_CREATE":
            return self._run_machine_complete(ctx, label="N0_CREATE")
        if code == "N1_PARSE":
            return self._run_n1(ctx)
        if code == "N2_ORGANIZE":
            return self._run_n2(ctx)
        if code == "N3_CONFIRM_EVIDENCE":
            return self._gate_continue_evidence(ctx)
        if code == "N4_ANALYZE":
            return self._run_n4(ctx)
        if code == "N5_CONFIRM_PARTIES":
            return self._gate_continue_parties(ctx)
        if code == "N6_CONFIRM_FACTS":
            return self._gate_continue_facts(ctx)
        if code == "N7_CONFIRM_CLAIMS":
            return self._gate_continue_claims(ctx)
        if code == "N8_WRITE":
            return self._run_n8(ctx)
        if code == "N9_REVIEW":
            return self._gate_continue_draft(ctx)
        raise AgentError(f"未知节点 {code}", code=AgentErrorCode.INVALID_WORKFLOW_STATE)

    def _organize(self, ctx: CaseContext, intent: IntentResult) -> HandlerResult:
        if ctx.current_node and ctx.current_node.code == "N2_ORGANIZE":
            return self._run_n2(ctx)
        raise AgentError(
            "当前不在 N2_ORGANIZE，无法整理证据。可说「继续」按工作流推进。",
            code=AgentErrorCode.INVALID_WORKFLOW_STATE,
        )

    def _generate_complaint(self, ctx: CaseContext, intent: IntentResult) -> HandlerResult:
        if ctx.current_node and ctx.current_node.code == "N8_WRITE":
            return self._run_n8(ctx)
        if ctx.current_node and ctx.current_node.code == "N9_REVIEW":
            return HandlerResult(
                message=(
                    "起诉状草稿已存在，当前在 N9 审核。"
                    "可说「查看起诉状」或「批准这份起诉状」。"
                ),
                intent=AgentIntent.GENERATE_COMPLAINT,
                error_code=AgentErrorCode.HUMAN_GATE_REQUIRED,
            )
        raise AgentError(
            self._blocked_write_reason(ctx),
            code=AgentErrorCode.INVALID_WORKFLOW_STATE,
        )

    # ----- machine nodes -----

    def _current_node_run(self, ctx: CaseContext) -> NodeRun | None:
        assert ctx.instance and ctx.current_node
        runs = self.runtime.list_node_runs(ctx.instance.id, node_id=ctx.current_node.id)
        if not runs:
            return None
        return runs[-1]

    def _ensure_running_node_run(self, ctx: CaseContext) -> NodeRun:
        assert ctx.instance and ctx.current_node
        latest = self._current_node_run(ctx)
        if latest and latest.status in {"RUNNING", "WAITING_USER"}:
            return latest
        if ctx.workflow_status == "WAITING_USER":
            cmd_id = stable_command_id(
                "RESUME_GATE",
                ctx.instance.id,
                ctx.current_node.code,
                latest.attempt if latest else 0,
            )
            resumed = self.runtime.resume_instance(ctx.instance.id, command_id=cmd_id)
            if resumed.node_run is None:
                raise AgentError(
                    "无法启动当前门节点",
                    code=AgentErrorCode.INVALID_WORKFLOW_STATE,
                )
            return resumed.node_run
        if latest and latest.status == "RUNNING":
            return latest
        # RUNNING instance without node run — start
        if ctx.workflow_status == "RUNNING":
            if latest is None or latest.status in {"SUCCEEDED", "FAILED"}:
                return self.runtime.start_node(
                    ctx.instance.id,
                    node_id=ctx.current_node.id,
                    input_payload={"case_id": str(ctx.case.id)},
                )
            return latest
        raise AgentError(
            f"无法取得 NodeRun（status={ctx.workflow_status}）",
            code=AgentErrorCode.INVALID_WORKFLOW_STATE,
        )

    def _run_machine_complete(self, ctx: CaseContext, *, label: str) -> HandlerResult:
        assert ctx.instance
        node_run = self._ensure_running_node_run(ctx)
        cmd_id = stable_command_id("CONTINUE", ctx.instance.id, label, node_run.id)
        cmd, replay = self._begin_cmd(
            cmd_id,
            case_id=ctx.case.id,
            instance_id=ctx.instance.id,
            command_type="CONTINUE",
            payload={"node": label, "node_run_id": str(node_run.id)},
        )
        if replay and cmd is not None:
            return self._replay_msg(cmd, AgentIntent.CONTINUE)
        result = self.runtime.complete_node(node_run.id, auto_advance=True)
        from backend.models import WorkflowNode

        node = (
            self.session.get(WorkflowNode, result.instance.current_node_id)
            if result.instance.current_node_id
            else None
        )
        msg = (
            f"已完成 {label}。"
            f"当前：{node.code if node else 'DONE'} / {result.instance.status}"
            + (
                f"（{result.instance.waiting_reason}）"
                if result.instance.waiting_reason
                else ""
            )
        )
        assert cmd is not None
        self._finish_cmd(cmd, {"message": msg})
        return HandlerResult(message=msg, intent=AgentIntent.CONTINUE, command_id=cmd_id)

    def _run_n1(self, ctx: CaseContext) -> HandlerResult:
        """N1: if materials already have SUCCEEDED EC, complete; else instruct."""
        materials = list(
            self.session.scalars(
                select(CaseMaterial).where(
                    CaseMaterial.case_id == ctx.case.id,
                    CaseMaterial.life_status == "ACTIVE",
                )
            )
        )
        if not materials:
            raise AgentError(
                "案件尚无已登记材料。请先登记 CaseMaterial 后再继续。",
                code=AgentErrorCode.VALIDATION_ERROR,
            )
        missing: list[str] = []
        for m in materials:
            ecs = list(
                self.session.scalars(
                    select(ExtractedContent).where(ExtractedContent.material_id == m.id)
                )
            )
            if not any(ec.status == "SUCCEEDED" for ec in ecs):
                missing.append(m.filename)
        if missing:
            raise AgentError(
                "以下材料尚无成功解析结果，本阶段不自动跑复杂提取："
                + "、".join(missing)
                + "。请先完成材料解析后再「继续」。",
                code=AgentErrorCode.VALIDATION_ERROR,
            )
        return self._run_machine_complete(ctx, label="N1_PARSE")

    def _resolve_ec_ids(self, case_id: UUID) -> list[UUID]:
        materials = list(
            self.session.scalars(
                select(CaseMaterial).where(
                    CaseMaterial.case_id == case_id,
                    CaseMaterial.life_status == "ACTIVE",
                )
            )
        )
        if not materials:
            raise AgentError("没有可用材料", code=AgentErrorCode.NOT_FOUND)
        ids: list[UUID] = []
        for m in materials:
            ecs = [
                ec
                for ec in self.session.scalars(
                    select(ExtractedContent).where(ExtractedContent.material_id == m.id)
                )
                if ec.status == "SUCCEEDED"
            ]
            if len(ecs) == 0:
                raise AgentError(
                    f"材料 {m.filename} 无 SUCCEEDED ExtractedContent",
                    code=AgentErrorCode.VALIDATION_ERROR,
                )
            if len(ecs) > 1:
                raise AgentError(
                    f"材料 {m.filename} 有多个 ExtractedContent，请明确指定",
                    code=AgentErrorCode.AMBIGUOUS_TARGET,
                )
            ids.append(ecs[0].id)
        return ids

    def _run_n2(self, ctx: CaseContext) -> HandlerResult:
        assert ctx.instance
        node_run = self._ensure_running_node_run(ctx)
        cmd_id = stable_command_id("CONTINUE", ctx.instance.id, "N2", node_run.id)
        cmd, replay = self._begin_cmd(
            cmd_id,
            case_id=ctx.case.id,
            instance_id=ctx.instance.id,
            command_type="CONTINUE",
            payload={"node": "N2_ORGANIZE", "node_run_id": str(node_run.id)},
        )
        if replay and cmd is not None:
            return self._replay_msg(cmd, AgentIntent.CONTINUE)
        ec_ids = self._resolve_ec_ids(ctx.case.id)
        result = self.organizer.run_n2_organize(
            instance_id=ctx.instance.id,
            node_run_id=node_run.id,
            extracted_content_ids=ec_ids,
            actor_id=self.actor_id,
            auto_complete=True,
        )
        pending = len(result.created) + len(
            [x for x in result.amended if x.acceptance == "PENDING"]
        )
        msg = (
            f"证据整理完成，新增/更新 {len(result.evidence_item_ids)} 条证据。"
            f"已进入 N3 确认门，待确认约 {pending} 条。"
            "可以说「接受证据1」「排除证据2」或「查看证据」。"
        )
        assert cmd is not None
        self._finish_cmd(cmd, {"message": msg})
        return HandlerResult(
            message=msg,
            intent=AgentIntent.CONTINUE,
            command_id=cmd_id,
            references=[
                {"evidence_item_id": str(i)} for i in result.evidence_item_ids
            ],
        )

    def _run_n4(self, ctx: CaseContext) -> HandlerResult:
        assert ctx.instance
        node_run = self._ensure_running_node_run(ctx)
        cmd_id = stable_command_id("CONTINUE", ctx.instance.id, "N4", node_run.id)
        cmd, replay = self._begin_cmd(
            cmd_id,
            case_id=ctx.case.id,
            instance_id=ctx.instance.id,
            command_type="CONTINUE",
            payload={"node": "N4_ANALYZE", "node_run_id": str(node_run.id)},
        )
        if replay and cmd is not None:
            return self._replay_msg(cmd, AgentIntent.CONTINUE)
        refs = self._accepted_evidence_refs(ctx.case.id)
        result = self.analyst.run_n4_analyze(
            instance_id=ctx.instance.id,
            node_run_id=node_run.id,
            accepted_evidence_refs=refs,
            actor_id=self.actor_id,
            auto_complete=True,
        )
        msg = (
            f"案件分析完成，提出 {len(result.created_facts)} 条事实候选。"
            "已进入当事人/事实确认门。请确认当事人与事实。"
        )
        assert cmd is not None
        self._finish_cmd(cmd, {"message": msg})
        return HandlerResult(message=msg, intent=AgentIntent.CONTINUE, command_id=cmd_id)

    def _run_n8(self, ctx: CaseContext) -> HandlerResult:
        assert ctx.instance
        node_run = self._ensure_running_node_run(ctx)
        cmd_id = stable_command_id("CONTINUE", ctx.instance.id, "N8", node_run.id)
        cmd, replay = self._begin_cmd(
            cmd_id,
            case_id=ctx.case.id,
            instance_id=ctx.instance.id,
            command_type="CONTINUE",
            payload={"node": "N8_WRITE", "node_run_id": str(node_run.id)},
        )
        if replay and cmd is not None:
            return self._replay_msg(cmd, AgentIntent.CONTINUE)
        claim = self.claim_svc.get_confirmed_claim_direction_for_writer(ctx.case.id)
        facts = self._confirmed_fact_refs(ctx.case.id)
        evidence = self._accepted_evidence_refs(ctx.case.id)
        parties = self._confirmed_party_keys(ctx.case.id)
        result = self.writer.run_n8_write(
            instance_id=ctx.instance.id,
            node_run_id=node_run.id,
            claim_direction_ref={
                "claim_direction_key": str(claim.claim_direction_key),
                "claim_direction_version": claim.version,
            },
            confirmed_fact_refs=facts,
            accepted_evidence_refs=evidence,
            confirmed_party_keys=parties,
            actor_id=self.actor_id,
            auto_complete=True,
        )
        warnings_raw = list(result.warnings or [])
        warnings: list[str] = []
        for w in warnings_raw:
            if isinstance(w, dict):
                code = w.get("code") or "WARNING"
                msg_w = w.get("message") or w.get("detail") or str(w)
                warnings.append(f"{code}: {msg_w}")
            else:
                warnings.append(str(w))
        msg = (
            f"起诉状草稿已生成（version={result.draft.version if result.draft else '?'}）。"
            "已进入 N9 审核，不会自动批准。可说「查看起诉状」或「批准这份起诉状」。"
        )
        assert cmd is not None
        self._finish_cmd(cmd, {"message": msg, "warnings": warnings})
        return HandlerResult(
            message=msg,
            intent=AgentIntent.GENERATE_COMPLAINT,
            command_id=cmd_id,
            warnings=warnings,
            references=[
                {
                    "draft_id": str(result.draft.id) if result.draft else None,
                    "version": result.draft.version if result.draft else None,
                }
            ],
        )

    # ----- human gates on CONTINUE -----

    def _gate_continue_evidence(self, ctx: CaseContext) -> HandlerResult:
        pending = ctx.pending_evidence
        if pending:
            lines = [
                f"{i + 1}. 编号{e.number} {e.title} (v{e.version})"
                for i, e in enumerate(pending)
            ]
            return HandlerResult(
                message=(
                    f"当前有 {len(pending)} 条待确认 EvidenceItem，不能因「继续」自动接受。\n"
                    + "\n".join(lines)
                    + "\n你可以说：「接受证据1」「排除证据2」。"
                ),
                intent=AgentIntent.CONTINUE,
                error_code=AgentErrorCode.HUMAN_GATE_REQUIRED,
                references=[
                    {
                        "evidence_item_id": str(e.id),
                        "version": e.version,
                        "number": e.number,
                    }
                    for e in pending
                ],
            )
        # Ready — resume + complete N3, advance toward N4
        assert ctx.instance
        try:
            self.organizer.assert_n3_ready_to_complete(ctx.instance.id)
        except ValidationError as exc:
            return HandlerResult(
                message=exc.message,
                intent=AgentIntent.CONTINUE,
                error_code=AgentErrorCode.HUMAN_GATE_REQUIRED,
            )
        node_run = self._ensure_running_node_run(ctx)
        cmd_id = stable_command_id("COMPLETE_N3", ctx.instance.id, node_run.id)
        cmd, replay = self._begin_cmd(
            cmd_id,
            case_id=ctx.case.id,
            instance_id=ctx.instance.id,
            command_type="COMPLETE_N3",
            payload={},
        )
        if replay and cmd is not None:
            return self._replay_msg(cmd, AgentIntent.CONTINUE)
        done = self.organizer.complete_n3_confirm_evidence(
            instance_id=ctx.instance.id,
            node_run_id=node_run.id,
            auto_advance=True,
        )
        msg = f"证据确认门已完成。当前状态 {done.instance.status}。"
        assert cmd is not None
        self._finish_cmd(cmd, {"message": msg})
        return HandlerResult(message=msg, intent=AgentIntent.CONTINUE, command_id=cmd_id)

    def _gate_continue_parties(self, ctx: CaseContext) -> HandlerResult:
        pending = ctx.pending_parties
        if pending or not self.analyst.is_party_gate_complete(ctx.case.id):
            parties = list(
                self.session.scalars(
                    select(CaseParty)
                    .where(
                        CaseParty.case_id == ctx.case.id,
                        CaseParty.is_current.is_(True),
                    )
                    .order_by(CaseParty.created_at.asc(), CaseParty.party_key.asc())
                )
            )
            if not parties:
                return HandlerResult(
                    message="当前没有当事人记录，无法通过 N5。请先由分析流程创建当事人候选。",
                    intent=AgentIntent.CONTINUE,
                    error_code=AgentErrorCode.HUMAN_GATE_REQUIRED,
                )
            lines = [
                f"{i + 1}. {p.role} {p.name} ({p.layer})"
                for i, p in enumerate(parties)
            ]
            return HandlerResult(
                message=(
                    "当事人确认门：不能因「继续」自动确认。\n"
                    + "\n".join(lines)
                    + "\n请说「确认当事人1」。"
                ),
                intent=AgentIntent.CONTINUE,
                error_code=AgentErrorCode.HUMAN_GATE_REQUIRED,
            )
        assert ctx.instance
        node_run = self._ensure_running_node_run(ctx)
        cmd_id = stable_command_id("COMPLETE_N5", ctx.instance.id, node_run.id)
        cmd, replay = self._begin_cmd(
            cmd_id,
            case_id=ctx.case.id,
            instance_id=ctx.instance.id,
            command_type="COMPLETE_N5",
            payload={},
        )
        if replay and cmd is not None:
            return self._replay_msg(cmd, AgentIntent.CONTINUE)
        done = self.analyst.complete_n5_confirm_parties(
            instance_id=ctx.instance.id,
            node_run_id=node_run.id,
            auto_advance=True,
        )
        msg = f"当事人确认完成，进入 {done.instance.waiting_reason or done.instance.status}。"
        assert cmd is not None
        self._finish_cmd(cmd, {"message": msg})
        return HandlerResult(message=msg, intent=AgentIntent.CONTINUE, command_id=cmd_id)

    def _gate_continue_facts(self, ctx: CaseContext) -> HandlerResult:
        pending = ctx.pending_facts
        if pending:
            lines = [
                f"{i + 1}. {f.statement[:60]} (v{f.version})"
                for i, f in enumerate(pending)
            ]
            return HandlerResult(
                message=(
                    f"当前有 {len(pending)} 个 Fact Candidate 尚未确认/拒绝，"
                    "不能因「继续」自动确认。\n"
                    + "\n".join(lines)
                    + "\n请说「确认事实1」或「拒绝事实2」。"
                ),
                intent=AgentIntent.CONTINUE,
                error_code=AgentErrorCode.HUMAN_GATE_REQUIRED,
            )
        assert ctx.instance
        try:
            self.analyst.assert_n6_ready_to_complete(ctx.instance.id)
        except ValidationError as exc:
            return HandlerResult(
                message=exc.message,
                intent=AgentIntent.CONTINUE,
                error_code=AgentErrorCode.HUMAN_GATE_REQUIRED,
            )
        node_run = self._ensure_running_node_run(ctx)
        cmd_id = stable_command_id("COMPLETE_N6", ctx.instance.id, node_run.id)
        cmd, replay = self._begin_cmd(
            cmd_id,
            case_id=ctx.case.id,
            instance_id=ctx.instance.id,
            command_type="COMPLETE_N6",
            payload={},
        )
        if replay and cmd is not None:
            return self._replay_msg(cmd, AgentIntent.CONTINUE)
        done = self.analyst.complete_n6_confirm_facts(
            instance_id=ctx.instance.id,
            node_run_id=node_run.id,
            auto_advance=True,
        )
        msg = f"事实确认门完成。当前 {done.instance.status}/{done.instance.waiting_reason}。"
        assert cmd is not None
        self._finish_cmd(cmd, {"message": msg})
        return HandlerResult(message=msg, intent=AgentIntent.CONTINUE, command_id=cmd_id)

    def _gate_continue_claims(self, ctx: CaseContext) -> HandlerResult:
        assert ctx.instance
        claim_ctx = (ctx.context_json or {}).get("claim_direction") or {}
        keys = claim_ctx.get("claim_direction_keys") or []
        # Need propose if no proposals yet
        if not keys and not ctx.pending_claims:
            node_run = self._ensure_running_node_run(ctx)
            # If node already has skill, don't re-propose blindly
            cmd_id = stable_command_id("N7_PROPOSE", ctx.instance.id, node_run.id)
            cmd, replay = self._begin_cmd(
                cmd_id,
                case_id=ctx.case.id,
                instance_id=ctx.instance.id,
                command_type="N7_PROPOSE",
                payload={},
            )
            if replay and cmd is not None:
                return self._replay_msg(cmd, AgentIntent.CONTINUE)
            facts = self._confirmed_fact_refs(ctx.case.id)
            parties = self._confirmed_party_keys(ctx.case.id)
            result = self.claim_svc.run_n7_propose(
                instance_id=ctx.instance.id,
                node_run_id=node_run.id,
                confirmed_fact_refs=facts,
                confirmed_party_keys=parties,
                actor_id=self.actor_id,
                auto_wait=True,
            )
            msg = (
                f"已生成 {len(result.created)} 条诉讼请求建议，等待律师确认。"
                "可以说「查看诉讼请求」「确认诉讼请求1」。"
            )
            assert cmd is not None
            self._finish_cmd(cmd, {"message": msg})
            return HandlerResult(message=msg, intent=AgentIntent.CONTINUE, command_id=cmd_id)

        if ctx.pending_claims:
            lines = [
                f"{i + 1}. {self._claim_summary(c)}"
                for i, c in enumerate(ctx.pending_claims)
            ]
            return HandlerResult(
                message=(
                    f"当前有 {len(ctx.pending_claims)} 条诉讼请求待确认，"
                    "不能因「继续」自动确认。\n"
                    + "\n".join(lines)
                ),
                intent=AgentIntent.CONTINUE,
                error_code=AgentErrorCode.HUMAN_GATE_REQUIRED,
            )
        try:
            self.claim_svc.assert_n7_ready_to_complete(ctx.instance.id)
        except ValidationError as exc:
            return HandlerResult(
                message=exc.message,
                intent=AgentIntent.CONTINUE,
                error_code=AgentErrorCode.HUMAN_GATE_REQUIRED,
            )
        node_run = self._ensure_running_node_run(ctx)
        if node_run.status == "RUNNING":
            node_run.status = "WAITING_USER"
            self.session.flush()
        cmd_id = stable_command_id("COMPLETE_N7", ctx.instance.id, node_run.id)
        cmd, replay = self._begin_cmd(
            cmd_id,
            case_id=ctx.case.id,
            instance_id=ctx.instance.id,
            command_type="COMPLETE_N7",
            payload={},
        )
        if replay and cmd is not None:
            return self._replay_msg(cmd, AgentIntent.CONTINUE)
        done = self.claim_svc.complete_n7_confirm_claims(
            instance_id=ctx.instance.id,
            node_run_id=node_run.id,
            auto_advance=True,
        )
        msg = f"诉讼请求确认完成。当前 {done.instance.status}。"
        assert cmd is not None
        self._finish_cmd(cmd, {"message": msg})
        return HandlerResult(message=msg, intent=AgentIntent.CONTINUE, command_id=cmd_id)

    def _gate_continue_draft(self, ctx: CaseContext) -> HandlerResult:
        draft = ctx.latest_draft
        if draft is None:
            return HandlerResult(
                message="N9：尚无草稿可审。",
                intent=AgentIntent.CONTINUE,
                error_code=AgentErrorCode.NOT_FOUND,
            )
        if draft.status == "APPROVED_BY_LAWYER":
            # Complete N9 → SUCCEEDED
            assert ctx.instance
            node_run = self._ensure_running_node_run(ctx)
            cmd_id = stable_command_id("COMPLETE_N9", ctx.instance.id, node_run.id)
            cmd, replay = self._begin_cmd(
                cmd_id,
                case_id=ctx.case.id,
                instance_id=ctx.instance.id,
                command_type="COMPLETE_N9",
                payload={},
            )
            if replay and cmd is not None:
                return self._replay_msg(cmd, AgentIntent.CONTINUE)
            done = self.runtime.complete_node(node_run.id, auto_advance=True)
            msg = f"N9 审核完成，工作流状态：{done.instance.status}。"
            assert cmd is not None
            self._finish_cmd(cmd, {"message": msg})
            return HandlerResult(message=msg, intent=AgentIntent.CONTINUE, command_id=cmd_id)
        return HandlerResult(
            message=(
                f"当前停在 N9 草稿审核。草稿 v{draft.version} 状态={draft.status}。"
                "「继续」不会自动批准。请说「查看起诉状」或「批准这份起诉状」。"
            ),
            intent=AgentIntent.CONTINUE,
            error_code=AgentErrorCode.HUMAN_GATE_REQUIRED,
            references=[
                {
                    "draft_id": str(draft.id),
                    "version": draft.version,
                    "status": draft.status,
                }
            ],
        )

    # ----- evidence / fact / party / claim / draft actions -----

    def _accept_evidence(self, ctx: CaseContext, intent: IntentResult) -> HandlerResult:
        resolver = TargetResolver(self.session, case_id=ctx.case.id)
        items: list[EvidenceItem] = []
        if intent.parameters.get("evidence_item_id"):
            items = [
                resolver.resolve_evidence_by_id(
                    UUID(str(intent.parameters["evidence_item_id"]))
                )
            ]
        elif intent.targets:
            items = resolver.resolve_evidence_by_numbers(intent.targets)
        else:
            raise AgentError(
                "请指定证据编号，例如「接受证据2」",
                code=AgentErrorCode.AMBIGUOUS_TARGET,
            )
        results: list[str] = []
        refs: list[dict[str, Any]] = []
        last_cmd: UUID | None = None
        replay_any = False
        for item in items:
            if item.case_id != ctx.case.id:
                raise AgentError("跨案件证据不可操作", code=AgentErrorCode.VALIDATION_ERROR)
            cmd_id = stable_command_id(
                "ACCEPT_EVIDENCE", ctx.case.id, item.id, item.version
            )
            cmd, replay = self._begin_cmd(
                cmd_id,
                case_id=ctx.case.id,
                instance_id=ctx.instance.id if ctx.instance else None,
                command_type="ACCEPT_EVIDENCE",
                payload={
                    "evidence_item_id": str(item.id),
                    "version": item.version,
                },
            )
            last_cmd = cmd_id
            if replay and cmd is not None:
                replay_any = True
                results.append(f"证据{item.number}已处理（幂等）")
                continue
            if item.acceptance == "ACCEPTED":
                assert cmd is not None
                self._finish_cmd(
                    cmd, {"message": f"证据{item.number}已是 ACCEPTED", "noop": True}
                )
                results.append(f"证据{item.number}已是接受状态")
                continue
            if item.acceptance != "PENDING":
                raise AgentError(
                    f"证据{item.number}状态为 {item.acceptance}，无法接受",
                    code=AgentErrorCode.VALIDATION_ERROR,
                )
            updated = self.domain.accept_evidence(item.id, actor_id=self.actor_id)
            assert cmd is not None
            self._finish_cmd(
                cmd,
                {
                    "message": f"已接受证据{item.number}",
                    "decision_linked": True,
                },
            )
            results.append(f"已接受证据{item.number} (v{updated.version})")
            refs.append(
                {
                    "evidence_item_id": str(updated.id),
                    "version": updated.version,
                }
            )
        return HandlerResult(
            message="；".join(results),
            intent=AgentIntent.ACCEPT_EVIDENCE,
            command_id=last_cmd,
            idempotent_replay=replay_any,
            references=refs,
        )

    def _exclude_evidence(self, ctx: CaseContext, intent: IntentResult) -> HandlerResult:
        if not intent.targets:
            raise AgentError("请指定证据编号", code=AgentErrorCode.AMBIGUOUS_TARGET)
        resolver = TargetResolver(self.session, case_id=ctx.case.id)
        items = resolver.resolve_evidence_by_numbers(intent.targets)
        results: list[str] = []
        for item in items:
            cmd_id = stable_command_id(
                "EXCLUDE_EVIDENCE", ctx.case.id, item.id, item.version
            )
            cmd, replay = self._begin_cmd(
                cmd_id,
                case_id=ctx.case.id,
                instance_id=ctx.instance.id if ctx.instance else None,
                command_type="EXCLUDE_EVIDENCE",
                payload={"evidence_item_id": str(item.id), "version": item.version},
            )
            if replay and cmd is not None:
                results.append(f"证据{item.number}已处理（幂等）")
                continue
            if item.acceptance == "EXCLUDED":
                assert cmd is not None
                self._finish_cmd(cmd, {"message": "already excluded", "noop": True})
                results.append(f"证据{item.number}已是排除状态")
                continue
            self.domain.exclude_evidence(item.id, actor_id=self.actor_id)
            assert cmd is not None
            self._finish_cmd(cmd, {"message": f"已排除证据{item.number}"})
            results.append(f"已排除证据{item.number}")
        return HandlerResult(
            message="；".join(results), intent=AgentIntent.EXCLUDE_EVIDENCE
        )

    def _confirm_fact(self, ctx: CaseContext, intent: IntentResult) -> HandlerResult:
        if not intent.targets:
            raise AgentError("请指定事实编号", code=AgentErrorCode.AMBIGUOUS_TARGET)
        resolver = TargetResolver(self.session, case_id=ctx.case.id)
        fact = resolver.resolve_fact_by_display_index(int(intent.targets[0]))
        if fact.case_id != ctx.case.id:
            raise AgentError("跨案件事实不可操作", code=AgentErrorCode.VALIDATION_ERROR)
        cmd_id = stable_command_id("CONFIRM_FACT", ctx.case.id, fact.fact_key, fact.version)
        cmd, replay = self._begin_cmd(
            cmd_id,
            case_id=ctx.case.id,
            instance_id=ctx.instance.id if ctx.instance else None,
            command_type="CONFIRM_FACT",
            payload={"fact_key": str(fact.fact_key), "version": fact.version},
        )
        if replay and cmd is not None:
            return self._replay_msg(cmd, AgentIntent.CONFIRM_FACT)
        if fact.status != "CANDIDATE":
            raise AgentError(
                f"仅 CANDIDATE 可确认，当前为 {fact.status}",
                code=AgentErrorCode.VALIDATION_ERROR,
            )
        updated = self.domain.confirm_fact(fact.fact_key, actor_id=self.actor_id)
        msg = f"已确认事实：{updated.statement[:80]}"
        assert cmd is not None
        self._finish_cmd(cmd, {"message": msg})
        return HandlerResult(
            message=msg,
            intent=AgentIntent.CONFIRM_FACT,
            command_id=cmd_id,
            references=[
                {"fact_key": str(updated.fact_key), "version": updated.version}
            ],
        )

    def _reject_fact(self, ctx: CaseContext, intent: IntentResult) -> HandlerResult:
        if not intent.targets:
            raise AgentError("请指定事实编号", code=AgentErrorCode.AMBIGUOUS_TARGET)
        resolver = TargetResolver(self.session, case_id=ctx.case.id)
        fact = resolver.resolve_fact_by_display_index(int(intent.targets[0]))
        cmd_id = stable_command_id("REJECT_FACT", ctx.case.id, fact.fact_key, fact.version)
        cmd, replay = self._begin_cmd(
            cmd_id,
            case_id=ctx.case.id,
            instance_id=ctx.instance.id if ctx.instance else None,
            command_type="REJECT_FACT",
            payload={"fact_key": str(fact.fact_key), "version": fact.version},
        )
        if replay and cmd is not None:
            return self._replay_msg(cmd, AgentIntent.REJECT_FACT)
        if fact.status != "CANDIDATE":
            raise AgentError(
                f"仅 CANDIDATE 可拒绝（V1），当前为 {fact.status}",
                code=AgentErrorCode.VALIDATION_ERROR,
            )
        self.domain.reject_fact(fact.fact_key, actor_id=self.actor_id)
        msg = "已拒绝该事实候选。"
        assert cmd is not None
        self._finish_cmd(cmd, {"message": msg})
        return HandlerResult(message=msg, intent=AgentIntent.REJECT_FACT, command_id=cmd_id)

    def _amend_fact(self, ctx: CaseContext, intent: IntentResult) -> HandlerResult:
        if not intent.targets:
            raise AgentError("请指定事实编号", code=AgentErrorCode.AMBIGUOUS_TARGET)
        new_statement = (intent.parameters or {}).get("new_statement")
        if not new_statement:
            raise AgentError("请提供新的事实陈述", code=AgentErrorCode.VALIDATION_ERROR)
        resolver = TargetResolver(self.session, case_id=ctx.case.id)
        fact = resolver.resolve_fact_by_display_index(int(intent.targets[0]))
        updated = self.domain.amend_fact(
            fact.fact_key, new_statement=new_statement, actor_id=self.actor_id
        )
        return HandlerResult(
            message=f"已修改事实为：{updated.statement[:80]}",
            intent=AgentIntent.AMEND_FACT,
            references=[
                {"fact_key": str(updated.fact_key), "version": updated.version}
            ],
        )

    def _confirm_party(self, ctx: CaseContext, intent: IntentResult) -> HandlerResult:
        if not intent.targets:
            raise AgentError("请指定当事人编号", code=AgentErrorCode.AMBIGUOUS_TARGET)
        resolver = TargetResolver(self.session, case_id=ctx.case.id)
        party = resolver.resolve_party_by_display_index(int(intent.targets[0]))
        cmd_id = stable_command_id(
            "CONFIRM_PARTY", ctx.case.id, party.party_key, party.version
        )
        cmd, replay = self._begin_cmd(
            cmd_id,
            case_id=ctx.case.id,
            instance_id=ctx.instance.id if ctx.instance else None,
            command_type="CONFIRM_PARTY",
            payload={"party_key": str(party.party_key)},
        )
        if replay and cmd is not None:
            return self._replay_msg(cmd, AgentIntent.CONFIRM_PARTY)
        if party.layer != "CANDIDATE":
            raise AgentError(
                f"仅 CANDIDATE 可确认，当前 {party.layer}",
                code=AgentErrorCode.VALIDATION_ERROR,
            )
        updated = self.domain.confirm_party(party.party_key, actor_id=self.actor_id)
        msg = f"已确认当事人：{updated.role} {updated.name}"
        assert cmd is not None
        self._finish_cmd(cmd, {"message": msg})
        return HandlerResult(message=msg, intent=AgentIntent.CONFIRM_PARTY, command_id=cmd_id)

    def _amend_party(self, ctx: CaseContext, intent: IntentResult) -> HandlerResult:
        raise AgentError(
            "请使用明确格式修改当事人（V1 请通过 Application/Domain 已有 amend_party）。",
            code=AgentErrorCode.UNKNOWN_INTENT,
        )

    def _confirm_claim(self, ctx: CaseContext, intent: IntentResult) -> HandlerResult:
        resolver = TargetResolver(self.session, case_id=ctx.case.id)
        index = int(intent.targets[0]) if intent.targets else None
        claim = resolver.resolve_claim_candidate(index)
        cmd_id = stable_command_id(
            "CONFIRM_CLAIM", ctx.case.id, claim.claim_direction_key, claim.version
        )
        cmd, replay = self._begin_cmd(
            cmd_id,
            case_id=ctx.case.id,
            instance_id=ctx.instance.id if ctx.instance else None,
            command_type="CONFIRM_CLAIM_DIRECTION",
            payload={"claim_direction_key": str(claim.claim_direction_key)},
        )
        if replay and cmd is not None:
            return self._replay_msg(cmd, AgentIntent.CONFIRM_CLAIM_DIRECTION)
        updated = self.domain.confirm_claim_direction(
            claim.claim_direction_key, actor_id=self.actor_id
        )
        msg = f"已确认诉讼请求：{self._claim_summary(updated)}"
        assert cmd is not None
        self._finish_cmd(cmd, {"message": msg})
        return HandlerResult(
            message=msg,
            intent=AgentIntent.CONFIRM_CLAIM_DIRECTION,
            command_id=cmd_id,
        )

    def _reject_claim(self, ctx: CaseContext, intent: IntentResult) -> HandlerResult:
        resolver = TargetResolver(self.session, case_id=ctx.case.id)
        index = int(intent.targets[0]) if intent.targets else None
        claim = resolver.resolve_claim_candidate(index)
        self.domain.reject_claim_direction(
            claim.claim_direction_key, actor_id=self.actor_id
        )
        return HandlerResult(
            message="已拒绝该诉讼请求建议。",
            intent=AgentIntent.REJECT_CLAIM_DIRECTION,
        )

    def _amend_claim(self, ctx: CaseContext, intent: IntentResult) -> HandlerResult:
        amount = (intent.parameters or {}).get("amount")
        if amount is None:
            raise AgentError("请提供金额", code=AgentErrorCode.VALIDATION_ERROR)
        resolver = TargetResolver(self.session, case_id=ctx.case.id)
        index = int(intent.targets[0]) if intent.targets else None
        # Prefer unique candidate or unique confirmed
        claim = resolver.resolve_claim_candidate(index)
        if claim.status != "CONFIRMED":
            raise AgentError(
                "金额修改仅支持已确认（CONFIRMED）的诉讼请求；请先确认后再改金额。",
                code=AgentErrorCode.VALIDATION_ERROR,
            )
        payload = copy.deepcopy(claim.payload or {})
        claims = payload.get("claims") or []
        if not claims:
            raise AgentError("诉讼请求 payload 无 claims", code=AgentErrorCode.VALIDATION_ERROR)
        if len(claims) > 1 and index is None:
            raise AgentError(
                "payload 含多条 claim，请指定唯一目标",
                code=AgentErrorCode.AMBIGUOUS_TARGET,
            )
        claims[0]["amount"] = float(amount)
        updated = self.domain.amend_claim_direction(
            claim.claim_direction_key, payload=payload, actor_id=self.actor_id
        )
        return HandlerResult(
            message=f"已将金额修改为 {amount}。新版本 v{updated.version}。",
            intent=AgentIntent.AMEND_CLAIM_DIRECTION,
            references=[
                {
                    "claim_direction_key": str(updated.claim_direction_key),
                    "version": updated.version,
                }
            ],
            warnings=["CLAIM_FACT_VERSION_PROVENANCE_GAP"],
        )

    def _approve_draft(self, ctx: CaseContext, intent: IntentResult) -> HandlerResult:
        draft = ctx.latest_draft
        if draft is None or draft.doc_type != "CIVIL_COMPLAINT":
            raise AgentError("没有可审核的起诉状草稿", code=AgentErrorCode.NOT_FOUND)
        if draft.status == "STALE":
            raise AgentError("草稿已过时，无法批准", code=AgentErrorCode.VALIDATION_ERROR)
        cmd_id = stable_command_id(
            "APPROVE_DRAFT", ctx.case.id, draft.id, draft.version
        )
        cmd, replay = self._begin_cmd(
            cmd_id,
            case_id=ctx.case.id,
            instance_id=ctx.instance.id if ctx.instance else None,
            command_type="APPROVE_DRAFT",
            payload={"draft_id": str(draft.id), "version": draft.version},
        )
        if replay and cmd is not None:
            return self._replay_msg(cmd, AgentIntent.APPROVE_DRAFT)
        if draft.status == "APPROVED_BY_LAWYER":
            assert cmd is not None
            self._finish_cmd(cmd, {"message": "已批准（幂等）", "noop": True})
            return HandlerResult(
                message="该起诉状已批准，未产生第二次独立批准。",
                intent=AgentIntent.APPROVE_DRAFT,
                command_id=cmd_id,
                idempotent_replay=True,
            )
        if draft.status not in {"DRAFT", "IN_REVIEW"}:
            raise AgentError(
                f"草稿状态 {draft.status} 不可批准",
                code=AgentErrorCode.VALIDATION_ERROR,
            )
        updated = self.domain.approve_document_draft(draft.id, actor_id=self.actor_id)
        msg = (
            f"律师已批准起诉状草稿 v{updated.version}（APPROVED_BY_LAWYER）。"
            "可说「继续」完成 N9 并结束工作流。"
        )
        assert cmd is not None
        self._finish_cmd(cmd, {"message": msg})
        return HandlerResult(
            message=msg,
            intent=AgentIntent.APPROVE_DRAFT,
            command_id=cmd_id,
            references=[
                {
                    "draft_id": str(updated.id),
                    "version": updated.version,
                    "status": updated.status,
                }
            ],
        )

    def _show_evidence(self, ctx: CaseContext, intent: IntentResult) -> HandlerResult:
        items = list(
            self.session.scalars(
                select(EvidenceItem).where(
                    EvidenceItem.case_id == ctx.case.id,
                    EvidenceItem.is_current.is_(True),
                )
            )
        )
        if not items:
            return HandlerResult(message="当前没有证据。", intent=AgentIntent.SHOW_EVIDENCE)
        lines = [
            f"- 编号{e.number} {e.title} [{e.acceptance}] v{e.version}" for e in items
        ]
        return HandlerResult(
            message="证据列表：\n" + "\n".join(lines),
            intent=AgentIntent.SHOW_EVIDENCE,
            references=[
                {
                    "evidence_item_id": str(e.id),
                    "version": e.version,
                    "number": e.number,
                }
                for e in items
            ],
        )

    def _show_facts(self, ctx: CaseContext, intent: IntentResult) -> HandlerResult:
        facts = list(
            self.session.scalars(
                select(Fact)
                .where(Fact.case_id == ctx.case.id, Fact.is_current.is_(True))
                .order_by(Fact.created_at.asc())
            )
        )
        if not facts:
            return HandlerResult(message="当前没有事实。", intent=AgentIntent.SHOW_FACTS)
        lines = [
            f"{i + 1}. [{f.status}] {f.statement[:80]} v{f.version}"
            for i, f in enumerate(facts)
        ]
        return HandlerResult(
            message="事实列表：\n" + "\n".join(lines), intent=AgentIntent.SHOW_FACTS
        )

    def _show_claims(self, ctx: CaseContext, intent: IntentResult) -> HandlerResult:
        claims = list(
            self.session.scalars(
                select(ClaimDirection).where(
                    ClaimDirection.case_id == ctx.case.id,
                    ClaimDirection.is_current.is_(True),
                )
            )
        )
        if not claims:
            return HandlerResult(
                message="当前没有诉讼请求。", intent=AgentIntent.SHOW_CLAIMS
            )
        lines = [f"{i + 1}. [{c.status}] {self._claim_summary(c)}" for i, c in enumerate(claims)]
        return HandlerResult(
            message="诉讼请求：\n"
            + "\n".join(lines)
            + "\n（提示：ClaimDirection→Fact version provenance gap 仍在）",
            intent=AgentIntent.SHOW_CLAIMS,
            warnings=["CLAIM_FACT_VERSION_PROVENANCE_GAP"],
        )

    def _show_draft(self, ctx: CaseContext, intent: IntentResult) -> HandlerResult:
        draft = ctx.latest_draft
        if draft is None:
            raise AgentError("没有起诉状草稿", code=AgentErrorCode.NOT_FOUND)
        body = draft.body_structured_json or {}
        preview = json.dumps(body, ensure_ascii=False)[:500]
        return HandlerResult(
            message=(
                f"起诉状草稿 id={draft.id} v{draft.version} status={draft.status}\n"
                f"摘要：{preview}"
            ),
            intent=AgentIntent.SHOW_DRAFT,
            references=[
                {
                    "draft_id": str(draft.id),
                    "version": draft.version,
                    "status": draft.status,
                }
            ],
        )

    # ----- helpers -----

    def _status_text(self, ctx: CaseContext) -> str:
        if ctx.instance is None:
            return "尚无工作流。可以说「开始处理这个案件」。"
        code = ctx.current_node.code if ctx.current_node else "?"
        label = ctx.current_node.name if ctx.current_node else ""
        pending = 0
        blocking = None
        if code == "N3_CONFIRM_EVIDENCE":
            pending = len(ctx.pending_evidence)
            blocking = f"{pending} 条 Evidence 待律师确认"
        elif code == "N5_CONFIRM_PARTIES":
            pending = len(ctx.pending_parties)
            party_ok = self.analyst.is_party_gate_complete(ctx.case.id)
            blocking = "当事人待确认" if pending or not party_ok else None
        elif code == "N6_CONFIRM_FACTS":
            pending = len(ctx.pending_facts)
            blocking = f"{pending} 个 Fact Candidate 尚未由律师确认或拒绝"
        elif code == "N7_CONFIRM_CLAIMS":
            pending = len(ctx.pending_claims)
            blocking = f"{pending} 条诉讼请求待确认" if pending else None
        elif code == "N9_REVIEW":
            d = ctx.latest_draft
            blocking = (
                f"草稿 v{d.version} status={d.status} 待审核"
                if d
                else "无草稿"
            )
        parts = [
            f"workflow_status={ctx.workflow_status}",
            f"current_node={code} ({label})",
        ]
        if ctx.waiting_reason:
            parts.append(f"waiting_reason={ctx.waiting_reason}")
        if pending:
            parts.append(f"pending_count={pending}")
        if blocking:
            parts.append(f"blocking={blocking}")
        if ctx.latest_draft and code == "N9_REVIEW":
            parts.append(
                f"draft_id={ctx.latest_draft.id} v{ctx.latest_draft.version} "
                f"status={ctx.latest_draft.status}"
            )
        return "；".join(parts)

    def _accepted_evidence_refs(self, case_id: UUID) -> list[dict[str, Any]]:
        items = list(
            self.session.scalars(
                select(EvidenceItem).where(
                    EvidenceItem.case_id == case_id,
                    EvidenceItem.is_current.is_(True),
                    EvidenceItem.acceptance == "ACCEPTED",
                )
            )
        )
        if not items:
            raise AgentError("没有 ACCEPTED 证据", code=AgentErrorCode.VALIDATION_ERROR)
        return [
            {"evidence_item_id": str(e.id), "evidence_item_version": e.version}
            for e in items
        ]

    def _confirmed_fact_refs(self, case_id: UUID) -> list[dict[str, Any]]:
        facts = list(
            self.session.scalars(
                select(Fact).where(
                    Fact.case_id == case_id,
                    Fact.is_current.is_(True),
                    Fact.status == "CONFIRMED",
                    Fact.stale.is_(False),
                )
            )
        )
        if not facts:
            raise AgentError("没有 CONFIRMED 事实", code=AgentErrorCode.VALIDATION_ERROR)
        return [{"fact_key": str(f.fact_key), "fact_version": f.version} for f in facts]

    def _confirmed_party_keys(self, case_id: UUID) -> list[UUID]:
        parties = list(
            self.session.scalars(
                select(CaseParty).where(
                    CaseParty.case_id == case_id,
                    CaseParty.is_current.is_(True),
                    CaseParty.layer == "CONFIRMED",
                )
            )
        )
        if not parties:
            raise AgentError("没有 CONFIRMED 当事人", code=AgentErrorCode.VALIDATION_ERROR)
        return [p.party_key for p in parties]

    def _claim_summary(self, claim: ClaimDirection) -> str:
        payload = claim.payload or {}
        claims = payload.get("claims") or []
        if claims:
            c0 = claims[0]
            amt = c0.get("amount")
            desc = c0.get("description") or c0.get("claim_type")
            return f"{desc}" + (f" amount={amt}" if amt is not None else "")
        return payload.get("overall_strategy") or str(claim.claim_direction_key)

    def _blocked_write_reason(self, ctx: CaseContext) -> str:
        code = ctx.current_node.code if ctx.current_node else None
        if code == "N6_CONFIRM_FACTS" and ctx.pending_facts:
            return (
                f"现在不能生成起诉状，因为还有 {len(ctx.pending_facts)} 个事实候选"
                "尚未由律师确认。"
            )
        if code == "N3_CONFIRM_EVIDENCE" and ctx.pending_evidence:
            return (
                f"现在不能生成起诉状，因为还有 {len(ctx.pending_evidence)} 条证据待确认。"
            )
        return f"当前节点 {code} 不能生成起诉状。"


def build_agent_response(
    *,
    ctx: CaseContext,
    handler: HandlerResult,
) -> AgentResponse:
    # refresh light fields from ctx (caller should reload ctx after mutations)
    pending = None
    blocking = None
    code = ctx.current_node.code if ctx.current_node else None
    if code == "N3_CONFIRM_EVIDENCE":
        pending = len(ctx.pending_evidence)
        if pending:
            blocking = f"{pending} 条 Evidence 待确认"
    elif code == "N6_CONFIRM_FACTS":
        pending = len(ctx.pending_facts)
        if pending:
            blocking = f"{pending} 个 Fact Candidate 待处理"
    elif code == "N7_CONFIRM_CLAIMS":
        pending = len(ctx.pending_claims)
    elif code == "N9_REVIEW" and ctx.latest_draft:
        blocking = f"draft status={ctx.latest_draft.status}"

    return AgentResponse(
        conversation_id=ctx.conversation_id,
        case_id=ctx.case.id,
        message=handler.message,
        intent=handler.intent,
        workflow_status=ctx.workflow_status,
        current_node=code,
        current_node_label=ctx.current_node.name if ctx.current_node else None,
        pending_count=pending,
        blocking_reason=blocking,
        actions=[AgentAction(**a) for a in ctx.available_actions],
        references=handler.references,
        warnings=handler.warnings,
        error_code=handler.error_code,
        command_id=handler.command_id,
        idempotent_replay=handler.idempotent_replay,
    )
