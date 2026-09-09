"""Party candidate creation — Application / API / Agent / N5 gate."""

from __future__ import annotations

import uuid
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.agent.case_agent import CaseAgent
from backend.agent.dto import AgentIntent
from backend.agent.intent_router import DeterministicIntentRouter
from backend.application.case_analyst import CaseAnalystService
from backend.application.party_management import PartyManagementService
from backend.domain.services import DomainService
from backend.infrastructure.db import get_db_session
from backend.main import app
from backend.models import CaseParty, HumanDecision

STATIC_JS = Path(__file__).resolve().parents[2] / "static" / "lawyer_agent" / "workspace.js"


@pytest.fixture
def client(
    db_session: Session,
) -> Generator[TestClient, None, None]:
    def _override() -> Generator[Session, None, None]:
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db_session] = _override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _make_case(session: Session, owner_id: uuid.UUID, title: str = "Party案件"):
    domain = DomainService(session)
    return domain.create_case(title=title, owner_user_id=owner_id, actor_id=owner_id)


def _decision_count(session: Session, case_id: uuid.UUID) -> int:
    return session.scalar(
        select(func.count()).select_from(HumanDecision).where(
            HumanDecision.case_id == case_id
        )
    )


def test_a_application_create_party_candidate(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    case = _make_case(db_session, owner_id)
    before = _decision_count(db_session, case.id)
    dto = PartyManagementService(db_session).create_party_candidate(
        case_id=case.id,
        role="PLAINTIFF",
        name="智图设计优化咨询有限公司",
        actor_id=actor_id,
    )
    assert dto.layer == "CANDIDATE"
    assert dto.role == "PLAINTIFF"
    assert dto.name == "智图设计优化咨询有限公司"
    assert dto.case_id == case.id
    assert _decision_count(db_session, case.id) == before
    row = db_session.scalars(
        select(CaseParty).where(CaseParty.party_key == dto.party_key)
    ).one()
    assert row.layer == "CANDIDATE"
    assert row.confirm_decision_id is None


def test_b_api_create_plaintiff_visible_in_workspace(client: TestClient) -> None:
    case_id = client.post("/api/cases", json={"title": "API原告"}).json()["id"]
    res = client.post(
        f"/api/cases/{case_id}/parties",
        json={"role": "PLAINTIFF", "name": "智图设计优化咨询有限公司"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["layer"] == "CANDIDATE"
    assert body["role"] == "PLAINTIFF"
    ws = client.get(f"/api/cases/{case_id}/workspace").json()
    assert len(ws["parties"]) == 1
    assert ws["parties"][0]["name"] == "智图设计优化咨询有限公司"
    assert ws["parties"][0]["layer"] == "CANDIDATE"


def test_c_api_create_defendant(client: TestClient) -> None:
    case_id = client.post("/api/cases", json={"title": "API被告"}).json()["id"]
    res = client.post(
        f"/api/cases/{case_id}/parties",
        json={"role": "DEFENDANT", "name": "星海地产开发有限公司"},
    )
    assert res.status_code == 200
    assert res.json()["role"] == "DEFENDANT"
    assert res.json()["layer"] == "CANDIDATE"


def test_d_empty_name_rejected(client: TestClient) -> None:
    case_id = client.post("/api/cases", json={"title": "空名"}).json()["id"]
    res = client.post(
        f"/api/cases/{case_id}/parties",
        json={"role": "PLAINTIFF", "name": "   "},
    )
    assert res.status_code == 400
    assert "名称" in res.json()["detail"]


def test_e_illegal_role_rejected(client: TestClient) -> None:
    case_id = client.post("/api/cases", json={"title": "非法角色"}).json()["id"]
    res = client.post(
        f"/api/cases/{case_id}/parties",
        json={"role": "甲方", "name": "某某公司"},
    )
    assert res.status_code == 400
    assert "角色" in res.json()["detail"]


def test_f_duplicate_rejected(client: TestClient) -> None:
    case_id = client.post("/api/cases", json={"title": "重复"}).json()["id"]
    payload = {"role": "PLAINTIFF", "name": "智图公司"}
    assert client.post(f"/api/cases/{case_id}/parties", json=payload).status_code == 200
    dup = client.post(f"/api/cases/{case_id}/parties", json=payload)
    assert dup.status_code == 409
    assert "已存在" in dup.json()["detail"]
    ws = client.get(f"/api/cases/{case_id}/workspace").json()
    assert len(ws["parties"]) == 1


def test_g_cross_case_scoped(client: TestClient) -> None:
    a = client.post("/api/cases", json={"title": "案A"}).json()["id"]
    b = client.post("/api/cases", json={"title": "案B"}).json()["id"]
    client.post(
        f"/api/cases/{a}/parties",
        json={"role": "PLAINTIFF", "name": "仅属A"},
    )
    wa = client.get(f"/api/cases/{a}/workspace").json()
    wb = client.get(f"/api/cases/{b}/workspace").json()
    assert len(wa["parties"]) == 1
    assert wb["parties"] == []
    missing = client.post(
        f"/api/cases/{uuid.uuid4()}/parties",
        json={"role": "PLAINTIFF", "name": "幽灵"},
    )
    assert missing.status_code == 404


def test_h_agent_create_plaintiff(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    case = _make_case(db_session, owner_id, "Agent原告")
    before = _decision_count(db_session, case.id)
    agent = CaseAgent(
        db_session,
        actor_id=actor_id,
        intent_engine=DeterministicIntentRouter(),
    )
    resp = agent.handle_message(
        case_id=case.id, message="录入原告：智图设计优化咨询有限公司"
    )
    assert resp.intent == AgentIntent.CREATE_PARTY
    assert "待确认" in resp.message
    assert "已确认" not in resp.message
    parties = list(
        db_session.scalars(
            select(CaseParty).where(
                CaseParty.case_id == case.id, CaseParty.is_current.is_(True)
            )
        )
    )
    assert len(parties) == 1
    assert parties[0].layer == "CANDIDATE"
    assert _decision_count(db_session, case.id) == before


def test_i_agent_create_defendant(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    case = _make_case(db_session, owner_id, "Agent被告")
    agent = CaseAgent(
        db_session,
        actor_id=actor_id,
        intent_engine=DeterministicIntentRouter(),
    )
    resp = agent.handle_message(
        case_id=case.id, message="录入被告：星海地产开发有限公司"
    )
    assert resp.intent == AgentIntent.CREATE_PARTY
    assert "星海地产开发有限公司" in resp.message


def test_j_ambiguous_does_not_create(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    case = _make_case(db_session, owner_id, "模糊")
    agent = CaseAgent(
        db_session,
        actor_id=actor_id,
        intent_engine=DeterministicIntentRouter(),
    )
    resp = agent.handle_message(case_id=case.id, message="原告可能是智图公司")
    assert resp.intent == AgentIntent.UNKNOWN
    count = db_session.scalar(
        select(func.count()).select_from(CaseParty).where(CaseParty.case_id == case.id)
    )
    assert count == 0


def test_k_n5_gate_requires_confirmed(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    case = _make_case(db_session, owner_id, "N5门")
    analyst = CaseAnalystService(db_session)
    party_svc = PartyManagementService(db_session)
    domain = DomainService(db_session)

    assert analyst.is_party_gate_complete(case.id) is False

    p1 = party_svc.create_party_candidate(
        case_id=case.id, role="PLAINTIFF", name="原告甲", actor_id=actor_id
    )
    p2 = party_svc.create_party_candidate(
        case_id=case.id, role="DEFENDANT", name="被告乙", actor_id=actor_id
    )
    assert analyst.is_party_gate_complete(case.id) is False

    domain.confirm_party(p1.party_key, actor_id=actor_id)
    assert analyst.is_party_gate_complete(case.id) is False

    domain.confirm_party(p2.party_key, actor_id=actor_id)
    assert analyst.is_party_gate_complete(case.id) is True


def test_l_hao_does_not_confirm(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    case = _make_case(db_session, owner_id, "好不确认")
    party = PartyManagementService(db_session).create_party_candidate(
        case_id=case.id, role="PLAINTIFF", name="原告甲", actor_id=actor_id
    )
    before = _decision_count(db_session, case.id)
    agent = CaseAgent(
        db_session,
        actor_id=actor_id,
        intent_engine=DeterministicIntentRouter(),
    )
    resp = agent.handle_message(case_id=case.id, message="好")
    assert resp.intent == AgentIntent.UNKNOWN
    db_session.refresh(
        db_session.scalars(
            select(CaseParty).where(CaseParty.party_key == party.party_key)
        ).one()
    )
    row = db_session.scalars(
        select(CaseParty).where(
            CaseParty.party_key == party.party_key, CaseParty.is_current.is_(True)
        )
    ).one()
    assert row.layer == "CANDIDATE"
    assert _decision_count(db_session, case.id) == before


def test_m_ui_confirm_still_agent_message() -> None:
    js = STATIC_JS.read_text(encoding="utf-8")
    assert "确认当事人${p.display_index}" in js
    assert "/api/cases/${caseId}/parties" in js
    assert "confirm_party" not in js
    assert "/parties/${" not in js or "confirm" not in js


def test_n_restart_persists_party(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    case = _make_case(db_session, owner_id, "重启")
    PartyManagementService(db_session).create_party_candidate(
        case_id=case.id, role="PLAINTIFF", name="原告甲", actor_id=actor_id
    )
    db_session.flush()
    agent1 = CaseAgent(
        db_session,
        actor_id=actor_id,
        intent_engine=DeterministicIntentRouter(),
    )
    agent1.handle_message(case_id=case.id, message="状态")
    # Recreate agent (simulates stop/recreate)
    agent2 = CaseAgent(
        db_session,
        actor_id=actor_id,
        intent_engine=DeterministicIntentRouter(),
    )
    parties = list(
        db_session.scalars(
            select(CaseParty).where(
                CaseParty.case_id == case.id, CaseParty.is_current.is_(True)
            )
        )
    )
    assert len(parties) == 1
    assert parties[0].layer == "CANDIDATE"
    assert CaseAnalystService(db_session).is_party_gate_complete(case.id) is False
    agent2.handle_message(
        case_id=case.id, message="录入被告：被告乙"
    )
    parties2 = list(
        db_session.scalars(
            select(CaseParty).where(
                CaseParty.case_id == case.id, CaseParty.is_current.is_(True)
            )
        )
    )
    assert len(parties2) == 2


def test_api_rejects_confirmed_flag(client: TestClient) -> None:
    case_id = client.post("/api/cases", json={"title": "禁确认"}).json()["id"]
    res = client.post(
        f"/api/cases/{case_id}/parties",
        json={
            "role": "PLAINTIFF",
            "name": "不可直接确认",
            "confirmed": True,
        },
    )
    assert res.status_code == 400
    assert "不能直接确认" in res.json()["detail"]


def test_reject_party_unblocks_wrong_candidate(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    case = _make_case(db_session, owner_id, "拒绝错误")
    svc = PartyManagementService(db_session)
    domain = DomainService(db_session)
    analyst = CaseAnalystService(db_session)
    wrong = svc.create_party_candidate(
        case_id=case.id, role="PLAINTIFF", name="错误原告", actor_id=actor_id
    )
    right_p = svc.create_party_candidate(
        case_id=case.id, role="PLAINTIFF", name="正确原告", actor_id=actor_id
    )
    right_d = svc.create_party_candidate(
        case_id=case.id, role="DEFENDANT", name="正确被告", actor_id=actor_id
    )
    domain.reject_party(wrong.party_key, actor_id=actor_id)
    domain.confirm_party(right_p.party_key, actor_id=actor_id)
    domain.confirm_party(right_d.party_key, actor_id=actor_id)
    assert analyst.is_party_gate_complete(case.id) is True


def test_deterministic_intent_create_party_phrases() -> None:
    router = DeterministicIntentRouter()
    assert router.parse("录入原告：智图公司").intent == AgentIntent.CREATE_PARTY
    assert router.parse("新增被告星海公司").intent == AgentIntent.CREATE_PARTY
    assert router.parse("添加第三人：丙方").parameters["role"] == "THIRD_PARTY"
    assert router.parse("原告可能是智图公司").intent == AgentIntent.UNKNOWN


def test_workspace_html_has_add_party_entry(client: TestClient) -> None:
    case_id = client.post("/api/cases", json={"title": "UI入口"}).json()["id"]
    page = client.get(f"/cases/{case_id}")
    assert page.status_code == 200
    assert "新增当事人" in page.text
    assert "party-form" in page.text
