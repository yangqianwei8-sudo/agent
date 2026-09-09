"""Fake-RUNNING / machine-node orchestration regression (P0)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.agent.case_agent import CaseAgent
from backend.agent.dto import AgentErrorCode, AgentIntent
from backend.agent.intent_router import DeterministicIntentRouter
from backend.application.case_analyst import CaseAnalystService
from backend.llm.errors import LLMError, LLMTimeoutError
from backend.models import EvidenceItem, NodeRun, SkillExecution, WorkflowInstance
from backend.skills.case_analyst import AnalystEngineResult, AnalystInput, EvidenceView
from backend.tests.integration.test_case_agent_phase9 import _seed_case_with_materials
from backend.workflow.seed import node_by_code


class _BoomAnalyst:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.last_meta: dict[str, Any] = {}

    def analyze(
        self, inp: AnalystInput, views: list[EvidenceView]
    ) -> AnalystEngineResult:
        _ = inp, views
        raise self._exc


def _agent(session: Session, actor_id: uuid.UUID, **kwargs: Any) -> CaseAgent:
    return CaseAgent(
        session,
        actor_id=actor_id,
        intent_engine=DeterministicIntentRouter(),
        **kwargs,
    )


def _say(agent: CaseAgent, case_id: uuid.UUID, text: str, cid: uuid.UUID | None = None):
    return agent.handle_message(case_id, text, conversation_id=cid)


def _accept_all_evidence(agent: CaseAgent, case_id: uuid.UUID, cid: uuid.UUID, session: Session):
    items = list(
        session.scalars(
            select(EvidenceItem).where(
                EvidenceItem.case_id == case_id, EvidenceItem.is_current.is_(True)
            )
        )
    )
    r = None
    for it in items:
        if it.acceptance == "PENDING":
            r = _say(agent, case_id, f"接受证据{it.number}", cid)
    return r


def _no_zombie_running(session: Session, case_id: uuid.UUID) -> None:
    inst = session.scalars(
        select(WorkflowInstance).where(WorkflowInstance.case_id == case_id)
    ).first()
    assert inst is not None
    zombies = list(
        session.scalars(
            select(NodeRun).where(
                NodeRun.instance_id == inst.id, NodeRun.status == "RUNNING"
            )
        )
    )
    assert zombies == [], f"zombie RUNNING node_runs: {[str(z.id) for z in zombies]}"


def test_a_n3_continue_runs_n4_and_lands_n5(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, _, _ = _seed_case_with_materials(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    agent = _agent(db_session, actor_id)
    r = _say(agent, case.id, "开始处理这个案件")
    cid = r.conversation_id
    assert r.current_node == "N3_CONFIRM_EVIDENCE"
    assert r.workflow_status == "WAITING_USER"
    _accept_all_evidence(agent, case.id, cid, db_session)

    r = _say(agent, case.id, "继续", cid)
    assert r.current_node == "N5_CONFIRM_PARTIES"
    assert r.workflow_status == "WAITING_USER"
    assert "分析" in r.message or "当事人" in r.message

    skills = list(
        db_session.scalars(
            select(SkillExecution).where(SkillExecution.skill_code == "CaseAnalystSkill")
        )
    )
    # skill_code may be CaseAnalyst without Skill suffix — check both
    if not skills:
        skills = list(
            db_session.scalars(
                select(SkillExecution).where(SkillExecution.skill_code.like("%Analyst%"))
            )
        )
    assert skills, "N4 CaseAnalyst SkillExecution must exist"
    assert any(s.status == "SUCCEEDED" for s in skills)
    _no_zombie_running(db_session, case.id)


def test_b_n4_llm_failure_not_left_running(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, _, _ = _seed_case_with_materials(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    boom = _BoomAnalyst(LLMError("forced analyst failure"))
    agent = _agent(
        db_session,
        actor_id,
        analyst=CaseAnalystService(db_session, engine=boom),  # type: ignore[arg-type]
    )
    r = _say(agent, case.id, "开始处理这个案件")
    cid = r.conversation_id
    _accept_all_evidence(agent, case.id, cid, db_session)
    r = _say(agent, case.id, "继续", cid)
    assert r.error_code == AgentErrorCode.LLM_REQUEST_FAILED
    assert "失败" in r.message
    assert r.workflow_status == "WAITING_RETRY"
    inst = db_session.scalars(
        select(WorkflowInstance).where(WorkflowInstance.case_id == case.id)
    ).one()
    n4 = node_by_code(db_session, inst.template_id, "N4_ANALYZE")
    runs = list(
        db_session.scalars(
            select(NodeRun).where(
                NodeRun.instance_id == inst.id, NodeRun.node_id == n4.id
            )
        )
    )
    assert runs
    assert all(run.status != "RUNNING" for run in runs)
    assert any(run.status == "FAILED" for run in runs)
    skills = list(
        db_session.scalars(
            select(SkillExecution).where(SkillExecution.skill_code.like("%Analyst%"))
        )
    )
    assert skills
    assert any(s.status == "FAILED" for s in skills)


def test_c_n4_timeout_not_left_running(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, _, _ = _seed_case_with_materials(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    boom = _BoomAnalyst(LLMTimeoutError("LLM request timed out"))
    agent = _agent(
        db_session,
        actor_id,
        analyst=CaseAnalystService(db_session, engine=boom),  # type: ignore[arg-type]
    )
    r = _say(agent, case.id, "开始处理这个案件")
    cid = r.conversation_id
    _accept_all_evidence(agent, case.id, cid, db_session)
    r = _say(agent, case.id, "继续", cid)
    assert r.workflow_status == "WAITING_RETRY"
    assert "超时" in r.message or "失败" in r.message
    _no_zombie_running(db_session, case.id)


def test_d_start_auto_runs_to_n3(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, _, _ = _seed_case_with_materials(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    agent = _agent(db_session, actor_id)
    r = _say(agent, case.id, "开始处理这个案件")
    assert r.current_node == "N3_CONFIRM_EVIDENCE"
    assert r.workflow_status == "WAITING_USER"
    _no_zombie_running(db_session, case.id)


def test_f_human_gates_still_block(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, _, _ = _seed_case_with_materials(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    agent = _agent(db_session, actor_id)
    r = _say(agent, case.id, "开始处理这个案件")
    cid = r.conversation_id
    r = _say(agent, case.id, "继续", cid)
    assert r.error_code == AgentErrorCode.HUMAN_GATE_REQUIRED
    assert r.current_node == "N3_CONFIRM_EVIDENCE"


def test_g_hao_still_unknown_at_gate(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, _, _ = _seed_case_with_materials(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    agent = _agent(db_session, actor_id)
    r = _say(agent, case.id, "开始处理这个案件")
    cid = r.conversation_id
    r = _say(agent, case.id, "好", cid)
    assert r.intent == AgentIntent.UNKNOWN


def test_h_restart_at_human_gate(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, _, _ = _seed_case_with_materials(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    agent = _agent(db_session, actor_id)
    r = _say(agent, case.id, "开始处理这个案件")
    cid = r.conversation_id
    assert r.current_node == "N3_CONFIRM_EVIDENCE"
    agent2 = _agent(db_session, actor_id)
    r2 = _say(agent2, case.id, "状态", cid)
    assert r2.current_node == "N3_CONFIRM_EVIDENCE"
    assert r2.workflow_status == "WAITING_USER"


def test_i_no_zombie_after_n3_n4_chain(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    test_a_n3_continue_runs_n4_and_lands_n5(db_session, owner_id, actor_id)


def test_e_n7_confirm_runs_n8_to_n9(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    """After claim confirm, Writer must execute in same CONTINUE → N9."""
    domain, case, _, parties = _seed_case_with_materials(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    agent = _agent(db_session, actor_id)
    r = _say(agent, case.id, "开始处理这个案件")
    cid = r.conversation_id
    _accept_all_evidence(agent, case.id, cid, db_session)
    r = _say(agent, case.id, "继续", cid)  # N3→N4→N5
    assert r.current_node == "N5_CONFIRM_PARTIES"
    for i, _ in enumerate(parties, start=1):
        _say(agent, case.id, f"确认当事人{i}", cid)
    r = _say(agent, case.id, "继续", cid)  # N5→N6 (and maybe N7 propose)
    # Confirm facts
    from backend.models import Fact

    for _ in range(20):
        facts = list(
            db_session.scalars(
                select(Fact).where(
                    Fact.case_id == case.id,
                    Fact.is_current.is_(True),
                    Fact.status == "CANDIDATE",
                )
            )
        )
        if not facts:
            break
        # display order by created_at
        facts.sort(key=lambda f: (f.created_at, f.fact_key))
        idx = 1
        # workspace-like index among all current facts
        all_facts = list(
            db_session.scalars(
                select(Fact)
                .where(Fact.case_id == case.id, Fact.is_current.is_(True))
                .order_by(Fact.created_at.asc(), Fact.fact_key.asc())
            )
        )
        for i, f in enumerate(all_facts, start=1):
            if f.status == "CANDIDATE":
                idx = i
                break
        _say(agent, case.id, f"确认事实{idx}", cid)

    r = _say(agent, case.id, "继续", cid)  # may propose claims
    if r.current_node == "N7_CONFIRM_CLAIMS":
        # ensure proposal exists
        if "生成" in (r.message or "") or r.pending_count:
            pass
        else:
            r = _say(agent, case.id, "继续", cid)
    r = _say(agent, case.id, "确认诉讼请求1", cid)
    r = _say(agent, case.id, "继续", cid)  # N7 complete → must run N8 → N9
    assert r.current_node == "N9_REVIEW", r.message
    assert r.workflow_status == "WAITING_USER"
    writers = list(
        db_session.scalars(
            select(SkillExecution).where(SkillExecution.skill_code.like("%Writer%"))
        )
    )
    assert writers
    assert any(s.status == "SUCCEEDED" for s in writers)
    _no_zombie_running(db_session, case.id)


def test_retry_after_n4_failure_reexecutes(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, _, _ = _seed_case_with_materials(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    boom = _BoomAnalyst(LLMTimeoutError("LLM request timed out"))
    agent = _agent(
        db_session,
        actor_id,
        analyst=CaseAnalystService(db_session, engine=boom),  # type: ignore[arg-type]
    )
    r = _say(agent, case.id, "开始处理这个案件")
    cid = r.conversation_id
    _accept_all_evidence(agent, case.id, cid, db_session)
    r = _say(agent, case.id, "继续", cid)
    assert r.workflow_status == "WAITING_RETRY"

    # Swap in working analyst via new agent (deterministic default)
    agent2 = _agent(db_session, actor_id)
    r = _say(agent2, case.id, "重试", cid)
    assert r.current_node == "N5_CONFIRM_PARTIES"
    assert r.workflow_status == "WAITING_USER"
    _no_zombie_running(db_session, case.id)
