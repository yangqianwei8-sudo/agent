"""Case Work Product V1 — deterministic stage / todo / next-action projections."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import (
    AgentMessage,
    CaseMaterial,
    DocumentDraft,
    EvidenceItemSpan,
    HumanDecision,
    SourceSpan,
)
from backend.schemas.case_work_product import (
    CaseSummaryView,
    CaseWorkProductView,
    NextActionView,
    TimelineEventView,
    TodoSummaryView,
    WorkActionButton,
    WorkStageView,
    WorkTodoItem,
)

STAGE_BY_NODE: dict[str | None, tuple[str, str, str]] = {
    None: ("material_prep", "材料整理", "尚未开始"),
    "N0_CREATE": ("material_prep", "材料整理", "进行中"),
    "N1_PARSE": ("material_prep", "材料整理", "进行中"),
    "N2_ORGANIZE": ("evidence_confirm", "证据确认", "进行中"),
    "N3_CONFIRM_EVIDENCE": ("evidence_confirm", "证据确认", "等待律师处理"),
    "N4_ANALYZE": ("case_analysis", "案件分析", "进行中"),
    "N5_CONFIRM_PARTIES": ("party_confirm", "当事人确认", "等待律师处理"),
    "N6_CONFIRM_FACTS": ("fact_confirm", "事实确认", "等待律师处理"),
    "N7_CONFIRM_CLAIMS": ("claim_confirm", "诉请确认", "等待律师处理"),
    "N8_WRITE": ("draft_write", "起诉状起草", "进行中"),
    "N9_REVIEW": ("draft_review", "起诉状审核", "等待律师处理"),
}

EVIDENCE_STATUS_ZH = {
    "PENDING": "待确认",
    "ACCEPTED": "已采纳",
    "EXCLUDED": "已排除",
}

FACT_STATUS_ZH = {
    "CANDIDATE": "待确认",
    "CONFIRMED": "已确认",
    "REJECTED": "已拒绝",
}


class CaseWorkStageProjector:
    def project(
        self,
        *,
        workflow_status: str | None,
        current_node: str | None,
        usable_material_count: int,
    ) -> WorkStageView:
        if workflow_status == "SUCCEEDED":
            return WorkStageView(
                stage_key="completed",
                stage_label="案件流程已完成",
                stage_status="completed",
                stage_status_label="已完成",
                completion_hint="主要流程节点已走完",
                workflow_node=current_node,
            )
        if not current_node and usable_material_count == 0:
            key, label, status_label = STAGE_BY_NODE[None]
            return WorkStageView(
                stage_key=key,
                stage_label=label,
                stage_status="not_started",
                stage_status_label=status_label,
                completion_hint="请先上传案件材料",
                workflow_node=None,
            )
        key, label, status_label = STAGE_BY_NODE.get(
            current_node, ("material_prep", "材料整理", "进行中")
        )
        status = "waiting_lawyer" if "等待律师" in status_label else "in_progress"
        if current_node in {"N1_PARSE", "N2_ORGANIZE", "N4_ANALYZE", "N8_WRITE"}:
            status = "in_progress"
        return WorkStageView(
            stage_key=key,
            stage_label=label,
            stage_status=status,
            stage_status_label=status_label,
            workflow_node=current_node,
        )


class CaseTodoProjector:
    def project(self, ctx: dict[str, Any]) -> TodoSummaryView:
        items: list[WorkTodoItem] = []
        prio = 10

        for m in ctx.get("pending_materials") or []:
            reason = m.get("pending_reason") or m.get("status_label") or "暂未解析"
            items.append(
                WorkTodoItem(
                    id=f"material-pending-{m['id']}",
                    todo_type="MATERIAL",
                    title="待处理文件",
                    summary=m.get("filename") or "未命名文件",
                    reason=reason,
                    actions=[
                        WorkActionButton(
                            label="查看材料",
                            action_type="NAVIGATE",
                            target="materials",
                            variant="ghost",
                        )
                    ],
                    entity_ref={"section": "materials", "material_id": m["id"]},
                    priority=prio,
                )
            )
            prio += 1

        for e in ctx.get("evidence") or []:
            if e.get("acceptance") != "PENDING":
                continue
            num = e.get("number")
            items.append(
                WorkTodoItem(
                    id=f"evidence-{e['id']}",
                    todo_type="EVIDENCE",
                    title="待确认证据",
                    summary=f"{e.get('title') or '证据' + str(num)}",
                    reason=e.get("summary") or "需律师确认是否采纳",
                    actions=[
                        WorkActionButton(
                            label="采纳", action_type="ACCEPT_EVIDENCE", target=str(num)
                        ),
                        WorkActionButton(
                            label="排除",
                            action_type="EXCLUDE_EVIDENCE",
                            target=str(num),
                            variant="danger",
                        ),
                        WorkActionButton(
                            label="查看原文",
                            action_type="VIEW_EVIDENCE",
                            target=str(e["id"]),
                            variant="ghost",
                        ),
                    ],
                    entity_ref={
                        "section": "evidence",
                        "evidence_id": e["id"],
                        "number": num,
                    },
                    priority=prio,
                )
            )
            prio += 1

        for f in ctx.get("facts") or []:
            if f.get("status") != "CANDIDATE":
                continue
            idx = f.get("display_index")
            items.append(
                WorkTodoItem(
                    id=f"fact-{f['fact_key']}",
                    todo_type="FACT",
                    title="待确认事实",
                    summary=(f.get("statement") or "")[:200],
                    reason="AI 分析建议，需律师确认后方能作为案件事实",
                    actions=[
                        WorkActionButton(
                            label="确认", action_type="CONFIRM_FACT", target=str(idx)
                        ),
                        WorkActionButton(
                            label="拒绝",
                            action_type="REJECT_FACT",
                            target=str(idx),
                            variant="danger",
                        ),
                    ],
                    entity_ref={
                        "section": "facts",
                        "fact_key": f["fact_key"],
                        "display_index": idx,
                    },
                    priority=prio,
                )
            )
            prio += 1

        for p in ctx.get("parties") or []:
            if p.get("layer") != "CANDIDATE":
                continue
            idx = p.get("display_index")
            items.append(
                WorkTodoItem(
                    id=f"party-{p['party_key']}",
                    todo_type="PARTY",
                    title="待确认当事人",
                    summary=f"{p.get('role_label') or p.get('role')}：{p.get('name')}",
                    reason="当事人信息需律师确认",
                    actions=[
                        WorkActionButton(
                            label="确认", action_type="CONFIRM_PARTY", target=str(idx)
                        ),
                        WorkActionButton(
                            label="拒绝",
                            action_type="REJECT_PARTY",
                            target=str(idx),
                            variant="danger",
                        ),
                    ],
                    entity_ref={
                        "section": "parties",
                        "party_key": p["party_key"],
                        "display_index": idx,
                    },
                    priority=prio,
                )
            )
            prio += 1

        claim = ctx.get("claim_direction")
        if claim and claim.get("status") == "CANDIDATE":
            desc = claim.get("description") or "诉讼请求"
            amount = claim.get("amount")
            summary = desc
            if amount is not None:
                summary = f"{desc} {amount:g} 元"
            items.append(
                WorkTodoItem(
                    id=f"claim-{claim.get('claim_direction_key')}",
                    todo_type="CLAIM",
                    title="待确认诉请",
                    summary=summary,
                    reason="诉请方向需律师确认后方可用于起诉状",
                    actions=[
                        WorkActionButton(label="确认", action_type="CONFIRM_CLAIM"),
                        WorkActionButton(
                            label="拒绝", action_type="REJECT_CLAIM", variant="danger"
                        ),
                    ],
                    entity_ref={"section": "claim"},
                    priority=prio,
                )
            )
            prio += 1

        readiness = ctx.get("pleading_readiness") or {}
        if readiness.get("status") != "READY":
            for issue in readiness.get("blocking_issues") or []:
                code = issue.get("code") or "BLOCKER"
                items.append(
                    WorkTodoItem(
                        id=f"readiness-{code}",
                        todo_type="READINESS",
                        title="起诉准备缺口",
                        summary=issue.get("message") or code,
                        reason=issue.get("suggested_action"),
                        actions=[
                            WorkActionButton(
                                label="查看准备度",
                                action_type="NAVIGATE",
                                target="readiness",
                                variant="ghost",
                            ),
                            WorkActionButton(
                                label="与 Agent 讨论",
                                action_type="AGENT_MESSAGE",
                                target="现在能不能生成起诉状？",
                                variant="ghost",
                            ),
                        ],
                        entity_ref={"section": "readiness", "code": code},
                        priority=prio,
                    )
                )
                prio += 1

        draft = ctx.get("draft")
        if draft and draft.get("status") in {"DRAFT", "IN_REVIEW"}:
            items.append(
                WorkTodoItem(
                    id=f"draft-{draft.get('id')}",
                    todo_type="DRAFT",
                    title="起诉状待审核",
                    summary=f"起诉状 v{draft.get('version')} 等待律师批准",
                    reason="审核通过后方可视为正式稿件",
                    actions=[
                        WorkActionButton(label="查看全文", action_type="VIEW_DRAFT"),
                        WorkActionButton(label="批准", action_type="APPROVE_DRAFT"),
                    ],
                    entity_ref={"section": "draft", "draft_id": draft.get("id")},
                    priority=prio,
                )
            )

        items.sort(key=lambda x: x.priority)
        return TodoSummaryView(total_count=len(items), items=items)


class CaseNextActionProjector:
    def project(
        self,
        *,
        todos: TodoSummaryView,
        stage: WorkStageView,
        readiness_status: str | None,
        draft: dict[str, Any] | None,
        usable_material_count: int,
        workflow_status: str | None,
    ) -> NextActionView:
        if usable_material_count == 0 and stage.stage_key == "material_prep":
            return NextActionView(
                label="上传案件材料",
                description="案件尚无可用材料，请先上传合同、往来函件等文件。",
                action_type="NAVIGATE",
                section_anchor="materials",
            )

        if todos.items:
            top = todos.items[0]
            primary = next((a for a in top.actions if a.variant == "primary"), None)
            if primary and primary.action_type not in {"NAVIGATE", "VIEW_EVIDENCE", "VIEW_DRAFT"}:
                return NextActionView(
                    label=top.title,
                    description=top.summary[:120],
                    action_type=primary.action_type,
                    action_target=primary.target,
                    section_anchor=top.entity_ref.get("section"),
                )
            if top.todo_type == "EVIDENCE":
                pending = [
                    t for t in todos.items if t.todo_type == "EVIDENCE"
                ]
                return NextActionView(
                    label=f"确认 {len(pending)} 条证据",
                    description="请逐条采纳或排除 AI 整理的证据。",
                    action_type="NAVIGATE",
                    section_anchor="evidence",
                )
            if top.todo_type == "FACT":
                pending = [t for t in todos.items if t.todo_type == "FACT"]
                return NextActionView(
                    label=f"确认 {len(pending)} 条案件事实",
                    description="请确认 AI 提出的候选事实。",
                    action_type="NAVIGATE",
                    section_anchor="facts",
                )
            return NextActionView(
                label=top.title,
                description=top.summary[:120],
                action_type="NAVIGATE",
                section_anchor=top.entity_ref.get("section"),
            )

        if readiness_status == "READY" and (
            draft is None or draft.get("status") not in {"DRAFT", "IN_REVIEW", "APPROVED_BY_LAWYER"}
        ):
            return NextActionView(
                label="生成起诉状",
                description="起诉准备度已具备，可以生成起诉状草稿。",
                action_type="GENERATE_DRAFT",
                section_anchor="draft",
            )

        if draft and draft.get("status") in {"DRAFT", "IN_REVIEW"}:
            return NextActionView(
                label=f"审核起诉状 v{draft.get('version')}",
                description="请查阅全文并决定是否批准。",
                action_type="VIEW_DRAFT",
                section_anchor="draft",
            )

        if workflow_status == "SUCCEEDED":
            return NextActionView(
                label="当前无紧急待办",
                description="主要流程已完成，可继续补充材料或优化诉状。",
            )

        if stage.stage_status == "waiting_lawyer":
            return NextActionView(
                label="继续推进案件",
                description="当前阶段等待律师处理，请查看下方分区或待办。",
                action_type="CONTINUE",
            )

        return NextActionView(
            label="继续处理案件",
            description="点击继续推进工作流到下一阶段。",
            action_type="CONTINUE",
        )


class CaseWorkTimelineProjector:
    def __init__(self, session: Session) -> None:
        self.session = session

    def project(self, case_id: UUID, *, limit: int = 30) -> list[TimelineEventView]:
        events: list[TimelineEventView] = []

        materials = list(
            self.session.scalars(
                select(CaseMaterial)
                .where(CaseMaterial.case_id == case_id)
                .order_by(CaseMaterial.created_at.desc())
                .limit(10)
            )
        )
        for m in reversed(materials):
            ts = m.created_at.isoformat() if m.created_at else ""
            events.append(
                TimelineEventView(
                    occurred_at=ts,
                    event_type="MATERIAL",
                    summary=f"上传《{m.filename}》",
                )
            )

        decisions = list(
            self.session.scalars(
                select(HumanDecision)
                .where(HumanDecision.case_id == case_id)
                .order_by(HumanDecision.created_at.asc())
                .limit(40)
            )
        )
        for d in decisions:
            ts = d.created_at.isoformat() if d.created_at else ""
            summary = self._decision_summary(d)
            if summary:
                events.append(
                    TimelineEventView(
                        occurred_at=ts,
                        event_type="DECISION",
                        summary=summary,
                    )
                )

        drafts = list(
            self.session.scalars(
                select(DocumentDraft)
                .where(DocumentDraft.case_id == case_id)
                .order_by(DocumentDraft.created_at.asc())
            )
        )
        for dr in drafts:
            ts = dr.created_at.isoformat() if dr.created_at else ""
            if dr.status == "APPROVED_BY_LAWYER":
                events.append(
                    TimelineEventView(
                        occurred_at=ts,
                        event_type="DRAFT",
                        summary=f"批准起诉状 v{dr.version}",
                    )
                )
            else:
                events.append(
                    TimelineEventView(
                        occurred_at=ts,
                        event_type="DRAFT",
                        summary=f"生成起诉状 v{dr.version}",
                    )
                )

        events.sort(key=lambda e: e.occurred_at or "")
        return events[-limit:]

    @staticmethod
    def _decision_summary(d: HumanDecision) -> str | None:
        dt = d.decision_type or ""
        payload = d.input_payload_json or {}
        if dt == "ACCEPT_EVIDENCE":
            return f"律师采纳证据{payload.get('number', '')}".strip()
        if dt == "EXCLUDE_EVIDENCE":
            return f"律师排除证据{payload.get('number', '')}".strip()
        if dt == "CONFIRM_FACT":
            return "律师确认案件事实"
        if dt == "REJECT_FACT":
            return "律师拒绝候选事实"
        if dt == "CONFIRM_PARTY":
            return f"律师确认当事人{payload.get('name', '')}".strip()
        if dt == "CONFIRM_CLAIM_DIRECTION":
            return "律师确认诉讼请求"
        if dt == "APPROVE_DRAFT":
            return "律师批准起诉状"
        return None


class CaseWorkProductBuilder:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.stage_projector = CaseWorkStageProjector()
        self.todo_projector = CaseTodoProjector()
        self.next_projector = CaseNextActionProjector()
        self.timeline_projector = CaseWorkTimelineProjector(session)

    def build(self, ctx: dict[str, Any]) -> CaseWorkProductView:
        case = ctx["case"]
        parties = ctx.get("parties") or []
        plaintiff = next((p["name"] for p in parties if p["role"] == "PLAINTIFF"), None)
        defendant = next((p["name"] for p in parties if p["role"] == "DEFENDANT"), None)
        wf = ctx.get("workflow") or {}
        usable = ctx.get("usable_materials") or ctx.get("materials") or []
        readiness = ctx.get("pleading_readiness") or {}
        draft = ctx.get("draft")

        stage = self.stage_projector.project(
            workflow_status=wf.get("status"),
            current_node=wf.get("current_node"),
            usable_material_count=len(usable),
        )
        todos = self.todo_projector.project(ctx)
        next_action = self.next_projector.project(
            todos=todos,
            stage=stage,
            readiness_status=readiness.get("status"),
            draft=draft,
            usable_material_count=len(usable),
            workflow_status=wf.get("status"),
        )
        timeline = self.timeline_projector.project(UUID(case["id"]))

        last_cid = _latest_conversation_id(self.session, UUID(case["id"]))

        return CaseWorkProductView(
            case_summary=CaseSummaryView(
                title=case.get("title") or "",
                case_no_internal=case.get("case_no_internal"),
                plaintiff=plaintiff,
                defendant=defendant,
                goal_summary=case.get("goal_summary"),
                updated_at=case.get("updated_at"),
            ),
            stage=stage,
            next_action=next_action,
            todo_summary=todos,
            timeline=timeline,
            resume={
                "conversation_id": last_cid,
                "stage_key": stage.stage_key,
                "todo_count": todos.total_count,
                "readiness_status": readiness.get("status"),
                "draft_version": draft.get("version") if draft else None,
            },
        )


def enrich_evidence_rows(session: Session, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for e in rows:
        item = dict(e)
        item["acceptance_label"] = EVIDENCE_STATUS_ZH.get(
            item.get("acceptance") or "", item.get("acceptance") or "—"
        )
        spans = list(
            session.scalars(
                select(EvidenceItemSpan).where(
                    EvidenceItemSpan.evidence_item_id == UUID(item["id"]),
                    EvidenceItemSpan.evidence_item_version == item["version"],
                )
            )
        )
        sources: list[dict[str, Any]] = []
        for link in spans:
            span = session.get(SourceSpan, link.source_span_id)
            if span is None:
                continue
            material = session.get(CaseMaterial, span.material_id)
            sources.append(
                {
                    "source_span_id": str(span.id),
                    "material_id": str(span.material_id),
                    "material_filename": material.filename if material else None,
                    "quote": span.quote,
                    "character_start": span.character_start,
                    "character_end": span.character_end,
                }
            )
        item["source_spans"] = sources
        item["source_material"] = sources[0]["material_filename"] if sources else None
        item["source_excerpt"] = sources[0]["quote"] if sources else item.get("summary")
        out.append(item)
    return out


def _latest_conversation_id(session: Session, case_id: UUID) -> str | None:
    rows = list(
        session.scalars(
            select(AgentMessage)
            .where(AgentMessage.case_id == case_id)
            .order_by(AgentMessage.created_at.desc())
            .limit(30)
        )
    )
    for row in rows:
        meta = row.parsed_intent_json or {}
        cid = meta.get("conversation_id")
        if cid:
            return str(cid)
    return None


def enrich_fact_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for f in rows:
        item = dict(f)
        item["status_label"] = FACT_STATUS_ZH.get(
            item.get("status") or "", item.get("status") or "—"
        )
        item["is_ai_candidate"] = item.get("status") == "CANDIDATE"
        out.append(item)
    return out
