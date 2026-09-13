"""Lawyer MVP UI — cases list/create/workspace, upload, agent via HTTP."""

from __future__ import annotations

import re
import uuid
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.application.material_upload import sanitize_filename
from backend.domain.errors import ValidationError
from backend.infrastructure.db import get_db_session
from backend.main import app
from backend.models import CaseMaterial
from backend.fixtures import generate as generate_fixtures
from backend.tools.storage import ObjectStorage

BACKEND_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = BACKEND_ROOT / "tests" / "fixtures"
STATIC_JS = BACKEND_ROOT / "static" / "lawyer_agent" / "workspace.js"


@pytest.fixture(scope="module", autouse=True)
def _ensure_fixtures() -> None:
    generate_fixtures()


@pytest.fixture
def client(
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient, None, None]:
    monkeypatch.setattr(
        "backend.application.material_upload.ObjectStorage",
        lambda *a, **k: ObjectStorage(root=tmp_path / "storage"),
    )

    def _override() -> Generator[Session, None, None]:
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db_session] = _override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_a_cases_list_accessible(client: TestClient) -> None:
    res = client.get("/cases")
    assert res.status_code == 200
    assert "案件列表" in res.text
    api = client.get("/api/cases")
    assert api.status_code == 200
    assert "cases" in api.json()


def test_b_create_case_success(client: TestClient) -> None:
    res = client.post(
        "/api/cases",
        json={"title": "MVP 创建案件", "goal_summary": "服务费争议"},
    )
    assert res.status_code == 200
    payload = res.json()
    assert payload["title"] == "MVP 创建案件"
    assert uuid.UUID(payload["id"])

    html = client.post(
        "/cases/new",
        data={"title": "表单创建案件", "goal_summary": "备注"},
        follow_redirects=False,
    )
    assert html.status_code == 303
    assert "/cases/" in html.headers["location"]


def test_c_workspace_opens(client: TestClient) -> None:
    created = client.post("/api/cases", json={"title": "工作台案件"}).json()
    case_id = created["id"]
    page = client.get(f"/cases/{case_id}")
    assert page.status_code == 200
    assert "Agent 对话" in page.text
    assert "案件材料" in page.text
    assert "待处理文件" in page.text
    ws = client.get(f"/api/cases/{case_id}/workspace")
    assert ws.status_code == 200
    body = ws.json()
    assert body["case"]["id"] == case_id
    assert "materials" in body
    assert "pending_materials" in body
    assert "material_pool" in body
    assert "conversation" in body


def test_d_pdf_upload_creates_material(
    client: TestClient, db_session: Session
) -> None:
    case_id = client.post("/api/cases", json={"title": "PDF上传"}).json()["id"]
    pdf = FIXTURES / "sample_text.pdf"
    with pdf.open("rb") as fh:
        res = client.post(
            f"/api/cases/{case_id}/materials",
            files={"file": ("合同.pdf", fh, "application/pdf")},
        )
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["parse_status"] in {"SUCCEEDED", "PARSED", "READY"} or data[
        "extraction_status"
    ] == "SUCCEEDED"
    mid = uuid.UUID(data["material_id"])
    material = db_session.get(CaseMaterial, mid)
    assert material is not None
    assert material.case_id == uuid.UUID(case_id)
    assert material.filename == "合同.pdf"


def test_e_docx_upload_success(client: TestClient) -> None:
    case_id = client.post("/api/cases", json={"title": "DOCX上传"}).json()["id"]
    docx = FIXTURES / "sample.docx"
    with docx.open("rb") as fh:
        res = client.post(
            f"/api/cases/{case_id}/materials",
            files={
                "file": (
                    "纪要.docx",
                    fh,
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )
    assert res.status_code == 200
    assert res.json()["success"] is True
    assert res.json()["extraction_status"] == "SUCCEEDED"


def test_e2_md_upload_success(client: TestClient) -> None:
    case_id = client.post("/api/cases", json={"title": "MD上传"}).json()["id"]
    body = "# 催款函\n\n请于七日内支付服务费 **100000** 元。\n".encode()
    res = client.post(
        f"/api/cases/{case_id}/materials",
        files={"file": ("催款函.md", body, "text/markdown")},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["extraction_status"] == "SUCCEEDED"
    assert data.get("usable") is True


def test_f_illegal_extension_rejected(client: TestClient) -> None:
    case_id = client.post("/api/cases", json={"title": "非法扩展名"}).json()["id"]
    res = client.post(
        f"/api/cases/{case_id}/materials",
        files={"file": ("malware.exe", b"MZ", "application/octet-stream")},
    )
    assert res.status_code == 400
    detail = res.json()["detail"].lower()
    assert "pdf" in detail or "docx" in detail or "md" in detail


def test_g_path_traversal_filename_sanitized() -> None:
    safe = sanitize_filename("../../etc/passwd.pdf")
    assert ".." not in safe
    assert "/" not in safe
    assert "\\" not in safe
    assert safe.endswith(".pdf")
    with pytest.raises(ValidationError):
        sanitize_filename("../../../")


def test_h_workspace_case_scope(client: TestClient) -> None:
    a = client.post("/api/cases", json={"title": "案件A"}).json()["id"]
    b = client.post("/api/cases", json={"title": "案件B"}).json()["id"]
    pdf = FIXTURES / "sample_text.pdf"
    with pdf.open("rb") as fh:
        client.post(
            f"/api/cases/{a}/materials",
            files={"file": ("only_a.pdf", fh, "application/pdf")},
        )
    wa = client.get(f"/api/cases/{a}/workspace").json()
    wb = client.get(f"/api/cases/{b}/workspace").json()
    assert any(m["filename"] == "only_a.pdf" for m in wa["materials"])
    assert wb["materials"] == []
    assert wa["case"]["id"] == a
    assert wb["case"]["id"] == b


def test_i_agent_message_via_ui_endpoint(client: TestClient) -> None:
    case_id = client.post("/api/cases", json={"title": "Agent消息"}).json()["id"]
    res = client.post(
        f"/cases/{case_id}/agent/messages",
        json={"message": "状态", "conversation_id": None},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["message"]
    assert body["conversation_id"]
    assert body["case_id"] == case_id
    # history appears in workspace conversation
    ws = client.get(f"/api/cases/{case_id}/workspace").json()
    roles = {m["role"] for m in ws["conversation"]}
    assert "USER" in roles
    assert "AGENT" in roles


def test_j_k_l_ui_buttons_route_through_agent_messages_not_domain() -> None:
    """Confirm buttons must use CaseAction API (delegates to CaseAgent), not Domain APIs."""
    js = STATIC_JS.read_text(encoding="utf-8")
    assert "data-work-action" in js
    assert "ACCEPT_EVIDENCE" in js
    assert "CONFIRM_FACT" in js
    assert "APPROVE_DRAFT" in js
    assert "CONFIRM_PARTY" in js
    assert "/api/cases/${caseId}/actions" in js
    assert "/cases/${caseId}/agent/messages" in js
    assert "/api/cases/${caseId}/parties" in js  # create candidate only
    # Must not call domain mutation paths from UI JS
    assert "accept_evidence" not in js
    assert "approve_document_draft" not in js
    assert "confirm_fact" not in js
    assert "confirm_party" not in js
    # API surface: structured actions only; no direct confirm REST bypass
    from backend.api.cases_api import router as cases_api_router

    paths = {getattr(r, "path", "") for r in cases_api_router.routes}
    assert any(re.search(r"/api/cases/.+/actions$", p) for p in paths)
    assert any(re.search(r"/api/cases/.+/parties$", p) for p in paths)


def test_same_filename_creates_new_material_not_overwrite(
    client: TestClient, db_session: Session
) -> None:
    case_id = client.post("/api/cases", json={"title": "同名再传"}).json()["id"]
    pdf = FIXTURES / "sample_text.pdf"
    for _ in range(2):
        with pdf.open("rb") as fh:
            res = client.post(
                f"/api/cases/{case_id}/materials",
                files={"file": ("同名.pdf", fh, "application/pdf")},
            )
        assert res.status_code == 200
    mats = list(
        db_session.scalars(
            select(CaseMaterial).where(CaseMaterial.case_id == uuid.UUID(case_id))
        )
    )
    assert len(mats) == 2
    assert mats[0].id != mats[1].id


def test_empty_file_rejected(client: TestClient) -> None:
    case_id = client.post("/api/cases", json={"title": "空文件"}).json()["id"]
    res = client.post(
        f"/api/cases/{case_id}/materials",
        files={"file": ("empty.pdf", b"", "application/pdf")},
    )
    assert res.status_code == 400
