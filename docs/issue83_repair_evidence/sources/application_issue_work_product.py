"""Issue Work Product — canonical issue-centered read projection.

Read-only projection over Issue-centered V2 domain (Issue #60 / #73 / #80).
Does not mutate ClaimDirection, ProofTaskFactLink, positions, or issues;
invariant enforcement remains in backend/domain/issue_centered.py and services.py.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.application.issue_matrix import IssueMatrixService
from backend.domain.enums import LawyerJudgmentState, ProofState
from backend.domain.errors import NotFoundError
from backend.models import AuditLog, Case, HumanDecision, Issue, LegalTheory
from backend.repositories.base import Repository
from backend.schemas.issue_work_product import (
    CaseIssueWorkProduct,
    ConflictView,
    EvolutionEventView,
    IssueWorkProduct,
    LawyerAssessmentView,
    LegalAnalysisView,
    LitigationPlanView,
    PositionView,
    ProofGapView,
    ProofTaskFactView,
    ProofTaskView,
    StructuralWarningView,
)

_PROOF_STATE_ZH = {
    ProofState.RED.value: "关键证明缺口",
    ProofState.YELLOW.value: "尚需补强",
    ProofState.GREEN.value: "当前基本闭环",
}

_JUDGMENT_STATE_ZH = {
    LawyerJudgmentState.NOT_ANALYZED.value: "尚未分析",
    LawyerJudgmentState.RESEARCHING.value: "研究中",
    LawyerJudgmentState.LAWYER_ASSESSMENT_FORMED.value: "已形成律师判断",
}

_POSITION_TYPE_ZH = {
    "ASSERTION": "我方主张",
    "ANTICIPATED_DEFENSE": "对方可能抗辩",
    "FORMAL_DEFENSE": "对方正式主张",
}

_ISSUE_STATUS_ZH = {
    "CANDIDATE": "待确认候选",
    "CONFIRMED": "已确认",
    "REJECTED": "已拒绝",
    "SUPERSEDED": "已替代",
}


class IssueWorkProductService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = Repository(session)
        self.matrix_svc = IssueMatrixService(session)

    def build_issue(
        self,
        issue_key: UUID,
        issue_version: int | None = None,
    ) -> IssueWorkProduct:
        if issue_version is not None:
            issue = self.repo.get_issue_version(issue_key, issue_version)
        else:
            issue = self.repo.get_current_issue(issue_key)
        if issue is None:
            raise NotFoundError("issue not found")
        return self._build_for_issue(issue)

    def build_case(self, case_id: UUID) -> CaseIssueWorkProduct:
        case = self.session.get(Case, case_id)
        if case is None:
            raise NotFoundError("case not found")
        issues = list(
            self.session.scalars(
                select(Issue)
                .where(Issue.case_id == case_id, Issue.is_current.is_(True))
                .order_by(Issue.order_index.asc(), Issue.created_at.asc())
            )
        )
        confirmed: list[IssueWorkProduct] = []
        candidates: list[IssueWorkProduct] = []
        state_counts = {
            ProofState.RED.value: 0,
            ProofState.YELLOW.value: 0,
            ProofState.GREEN.value: 0,
        }
        assessment_count = 0
        for issue in issues:
            if issue.status == "REJECTED":
                continue
            wp = self._build_for_issue(issue)
            if issue.status == "CONFIRMED":
                confirmed.append(wp)
                state_counts[wp.proof_state] = state_counts.get(wp.proof_state, 0) + 1
                if wp.lawyer_assessment:
                    assessment_count += 1
            elif issue.status == "CANDIDATE":
                candidates.append(wp)
        blocking = next(
            (i for i in confirmed if i.proof_state == ProofState.RED.value),
            None,
        )
        recommended = blocking or next(
            (i for i in confirmed if i.proof_state == ProofState.YELLOW.value),
            confirmed[0] if confirmed else (candidates[0] if candidates else None),
        )
        return CaseIssueWorkProduct(
            case_id=str(case_id),
            confirmed_issues=confirmed,
            candidate_issues=candidates,
            proof_state_counts=state_counts,
            assessment_count=assessment_count,
            top_blocking_issue=blocking,
            recommended_next_issue=recommended,
        )

    def build_litigation_plan(self, case_id: UUID) -> LitigationPlanView:
        from backend.application.claim_view import ClaimViewService
        from backend.application.pleading_readiness import PleadingReadinessService

        case_wp = self.build_case(case_id)
        claims = ClaimViewService(self.session).build(case_id)
        readiness = PleadingReadinessService(self.session).evaluate(case_id)
        assessments = [
            {
                "issue_key": i.issue_key,
                "issue_statement": i.statement,
                "assessment": i.lawyer_assessment.model_dump(mode="json")
                if i.lawyer_assessment
                else None,
            }
            for i in case_wp.confirmed_issues
            if i.lawyer_assessment
        ]
        return LitigationPlanView(
            case_id=str(case_id),
            confirmed_issues=[i.model_dump(mode="json") for i in case_wp.confirmed_issues],
            claims=[c.model_dump(mode="json") for c in claims.items],
            readiness_status=readiness.status,
            readiness_display=readiness.display_status or readiness.status,
            assessments=assessments,
        )

    def _build_for_issue(self, issue: Issue) -> IssueWorkProduct:
        positions_raw = self.repo.list_issue_positions(issue.issue_key, issue.version)
        positions: dict[str, list[PositionView]] = {
            "our_current": [],
            "anticipated_defenses": [],
            "formal_opponent": [],
        }
        for pos in positions_raw:
            if pos.status in {"REJECTED", "SUPERSEDED"}:
                continue
            view = PositionView(
                position_key=str(pos.position_key),
                version=pos.version,
                side=pos.side,
                position_type=pos.position_type,
                source_type=pos.source_type,
                status=pos.status,
                statement=pos.statement,
                display_label=_POSITION_TYPE_ZH.get(pos.position_type, pos.position_type),
                opponent_material_ref=pos.opponent_material_ref,
            )
            if pos.side == "OUR" and pos.position_type == "ASSERTION":
                positions["our_current"].append(view)
            elif pos.position_type == "ANTICIPATED_DEFENSE":
                positions["anticipated_defenses"].append(view)
            elif pos.position_type == "FORMAL_DEFENSE":
                positions["formal_opponent"].append(view)

        proof_tasks: list[ProofTaskView] = []
        matrix_item = next(
            (
                i
                for i in self.matrix_svc.build(issue.case_id).items
                if i.issue_key == str(issue.issue_key) and i.issue_version == issue.version
            ),
            None,
        )
        structural_warnings: list[StructuralWarningView] = []
        if matrix_item:
            for g in matrix_item.structural_warnings:
                structural_warnings.append(
                    StructuralWarningView(
                        type=g.type,
                        description=g.description,
                        related_fact_key=g.related_fact_key,
                        related_fact_version=g.related_fact_version,
                    )
                )

        gaps_raw = self.repo.list_proof_gaps(issue.issue_key, issue.version)
        gap_views = [self._gap_view(g) for g in gaps_raw]
        open_gap_count = sum(1 for g in gaps_raw if g.status == "OPEN")

        for task in self.repo.list_proof_tasks(issue.issue_key, issue.version):
            task_gaps = [
                g.model_dump(mode="json")
                for g in gap_views
                if g.proof_task_key == str(task.proof_task_key)
            ]
            support, adverse, context = self._proof_task_facts(task)
            task_structural = structural_warnings if task.status == "ADOPTED" else []
            proof_tasks.append(
                ProofTaskView(
                    proof_task_key=str(task.proof_task_key),
                    version=task.version,
                    description=task.description,
                    status=task.status,
                    source_type=task.source_type,
                    display_status=self._proof_task_display(task.status),
                    support_facts=support,
                    adverse_facts=adverse,
                    context_facts=context,
                    structural_warnings=task_structural,
                    proof_gaps=task_gaps,
                )
            )

        conflicts: list[ConflictView] = []
        for conflict in self.repo.list_issue_conflicts(issue.issue_key, issue.version):
            facts = []
            for fl in self.repo.list_conflict_fact_links(conflict.id):
                fact = self.repo.get_fact_version(fl.fact_key, fl.fact_version)
                if fact:
                    facts.append(
                        {
                            "fact_key": str(fl.fact_key),
                            "fact_version": fl.fact_version,
                            "statement": fact.statement,
                            "role": fl.role,
                        }
                    )
            conflicts.append(
                ConflictView(
                    conflict_id=str(conflict.id),
                    conflict_key=str(conflict.conflict_key),
                    description=conflict.description,
                    status=conflict.status,
                    source_type=conflict.source_type,
                    display_status=self._conflict_display(conflict.status),
                    resolution_note=conflict.resolution_note,
                    facts=facts,
                )
            )

        legal = self._legal_analysis(issue)
        assessments = self.repo.list_lawyer_assessments(issue.issue_key, issue.version)
        current_assessment = next(
            (a for a in assessments if a.is_current and a.status == "ACTIVE"),
            None,
        )
        lawyer_assessment = None
        if current_assessment:
            lawyer_assessment = LawyerAssessmentView(
                assessment_key=str(current_assessment.assessment_key),
                version=current_assessment.version,
                content=current_assessment.content,
                status=current_assessment.status,
                display_status="当前有效",
                is_current=True,
            )

        proof_state = self._compute_proof_state(
            issue, open_gap_count, structural_warnings, proof_tasks
        )
        judgment_state = (
            LawyerJudgmentState.LAWYER_ASSESSMENT_FORMED.value
            if lawyer_assessment
            else LawyerJudgmentState.NOT_ANALYZED.value
        )

        return IssueWorkProduct(
            issue_key=str(issue.issue_key),
            issue_version=issue.version,
            statement=issue.statement,
            status=issue.status,
            source_type=issue.source_type,
            display_status=_ISSUE_STATUS_ZH.get(issue.status, issue.status),
            positions=positions,
            proof_tasks=proof_tasks,
            conflicts=conflicts,
            legal_analysis=legal,
            lawyer_assessment=lawyer_assessment,
            proof_state=proof_state,
            proof_state_label=_PROOF_STATE_ZH.get(proof_state, proof_state),
            lawyer_judgment_state=judgment_state,
            lawyer_judgment_state_label=_JUDGMENT_STATE_ZH.get(judgment_state, judgment_state),
            next_action=self._next_action(proof_state, open_gap_count, issue.status),
            evolution=self._evolution(issue),
            proof_gaps=gap_views,
            proof_gap_count=len(gap_views),
            open_proof_gap_count=open_gap_count,
            conflict_count=len([c for c in conflicts if c.status in {"CANDIDATE", "OPEN"}]),
            proof_task_count=len(proof_tasks),
        )

    def _proof_task_facts(self, task) -> tuple[list, list, list]:
        support, adverse, context = [], [], []
        for link in self.repo.list_proof_task_fact_links(
            task.proof_task_key, task.version
        ):
            fact = self.repo.get_fact_version(link.fact_key, link.fact_version)
            if fact is None:
                continue
            evidence = self._fact_evidence(fact.id)
            view = ProofTaskFactView(
                fact_key=str(link.fact_key),
                fact_version=link.fact_version,
                statement=fact.statement,
                status=fact.status,
                role=link.role,
                evidence=evidence,
            )
            if link.role == "SUPPORT":
                support.append(view)
            elif link.role == "ADVERSE":
                adverse.append(view)
            else:
                context.append(view)
        return support, adverse, context

    def _fact_evidence(self, fact_id: UUID) -> list[dict]:
        out = []
        for link in self.repo.list_fact_links(fact_id):
            if link.status != "ACTIVE":
                continue
            ev = self.repo.get_evidence_version(link.evidence_item_id, link.evidence_item_version)
            if ev and ev.acceptance == "ACCEPTED":
                out.append(
                    {
                        "evidence_item_id": str(ev.id),
                        "evidence_item_version": ev.version,
                        "title": ev.title,
                        "acceptance": ev.acceptance,
                    }
                )
        return out

    def _gap_view(self, gap) -> ProofGapView:
        status_zh = {
            "OPEN": "待处理",
            "RESOLVED": "已解决",
            "WAIVED": "已放弃",
            "SUPERSEDED": "已替代",
        }
        return ProofGapView(
            gap_id=str(gap.id),
            gap_key=str(gap.gap_key),
            gap_type=gap.gap_type,
            status=gap.status,
            source_type=gap.source_type,
            description=gap.description,
            what_exists=gap.what_exists,
            what_is_missing=gap.what_is_missing,
            why_it_matters=gap.why_it_matters,
            suggested_material_types=gap.suggested_material_types or [],
            display_status=status_zh.get(gap.status, gap.status),
            proof_task_key=str(gap.proof_task_key) if gap.proof_task_key else None,
        )

    def _legal_analysis(self, issue: Issue) -> LegalAnalysisView:
        theories = []
        for link in self.repo.list_issue_legal_theory_links(issue.issue_key, issue.version):
            lt = self.session.get(LegalTheory, link.legal_theory_id)
            if lt:
                theories.append(
                    {
                        "id": str(lt.id),
                        "theory_summary": lt.theory_summary,
                        "role": link.role,
                        "layer": lt.layer,
                    }
                )
        favorable: list[str] = []
        adverse: list[str] = []
        unknown: list[str] = []
        for link in self.repo.list_issue_fact_links(issue.issue_key, issue.version):
            fact = self.repo.get_fact_version(link.fact_key, link.fact_version)
            if fact is None or fact.status != "CONFIRMED":
                continue
            if link.role == "SUPPORT":
                favorable.append(fact.statement)
            elif link.role == "ADVERSE":
                adverse.append(fact.statement)
            else:
                unknown.append(fact.statement)
        return LegalAnalysisView(
            legal_theories=theories,
            favorable_factors=favorable,
            adverse_factors=adverse,
            unknown_factors=unknown,
        )

    def _compute_proof_state(
        self,
        issue: Issue,
        open_gap_count: int,
        structural_warnings: list,
        proof_tasks: list,
    ) -> str:
        if issue.status != "CONFIRMED":
            return ProofState.YELLOW.value
        if open_gap_count > 0:
            return ProofState.RED.value
        if structural_warnings:
            return ProofState.YELLOW.value
        adopted = [t for t in proof_tasks if t.status == "ADOPTED"]
        if not adopted:
            return ProofState.YELLOW.value
        for task in adopted:
            if not task.support_facts:
                return ProofState.YELLOW.value
        return ProofState.GREEN.value

    def _next_action(self, proof_state: str, open_gaps: int, status: str) -> str | None:
        if status == "CANDIDATE":
            return "请确认是否将本焦点纳入办案范围"
        if open_gaps > 0:
            return "请处理待补强的证明缺口"
        if proof_state == ProofState.YELLOW.value:
            return "请继续补强事实与证据关联"
        if proof_state == ProofState.GREEN.value:
            return "可进入法律分析与诉请衔接"
        return "请审查关键证明缺口"

    def _evolution(self, issue: Issue) -> list[EvolutionEventView]:
        events: list[EvolutionEventView] = []
        if issue.created_at:
            events.append(
                EvolutionEventView(
                    event_type="ISSUE_CREATED",
                    timestamp=issue.created_at.isoformat(),
                    summary=f"争点创建（v{issue.version}）",
                )
            )
        decisions = list(
            self.session.scalars(
                select(HumanDecision)
                .where(
                    HumanDecision.case_id == issue.case_id,
                    HumanDecision.target_id == issue.issue_key,
                )
                .order_by(HumanDecision.created_at.asc())
            )
        )
        for d in decisions:
            events.append(
                EvolutionEventView(
                    event_type=d.decision_type,
                    timestamp=d.created_at.isoformat() if d.created_at else None,
                    summary=d.decision_type,
                    actor_hint=str(d.actor_id)[:8] if d.actor_id else None,
                )
            )
        audits = list(
            self.session.scalars(
                select(AuditLog)
                .where(
                    AuditLog.case_id == issue.case_id,
                    AuditLog.entity_type.in_(["issue_positions", "proof_tasks", "proof_gaps"]),
                )
                .order_by(AuditLog.created_at.asc())
                .limit(20)
            )
        )
        for a in audits:
            events.append(
                EvolutionEventView(
                    event_type=a.action,
                    timestamp=a.created_at.isoformat() if a.created_at else None,
                    summary=a.action,
                )
            )
        return events

    @staticmethod
    def _proof_task_display(status: str) -> str:
        return {
            "CANDIDATE": "待采纳",
            "ADOPTED": "已采纳",
            "REJECTED": "已拒绝",
            "WAIVED": "已放弃",
            "SUPERSEDED": "已替代",
        }.get(status, status)

    @staticmethod
    def _conflict_display(status: str) -> str:
        return {
            "CANDIDATE": "待确认",
            "OPEN": "待处理",
            "RESOLVED": "已解决",
            "DISMISSED": "已驳回",
        }.get(status, status)
