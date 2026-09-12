"""Issue-centered workspace — HTTP service/browser smoke (no mocks-only)."""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

os.environ.setdefault("LLM_MODE", "deterministic")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.domain.services import DomainService  # noqa: E402
from backend.infrastructure.db import get_session_factory  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from backend.main import app  # noqa: E402


def main() -> int:
    checks: list[str] = []
    client = TestClient(app)

    list_res = client.get("/cases")
    assert list_res.status_code == 200
    assert "案件列表" in list_res.text
    checks.append("case list")

    create = client.post("/api/cases", json={"title": "Browser Smoke Case"})
    assert create.status_code == 200
    case_id = create.json()["id"]

    with get_session_factory()() as session:
        svc = DomainService(session)
        actor = uuid.uuid4()
        issue = svc.confirm_issue(
            svc.propose_issue(
                case_id=uuid.UUID(case_id),
                statement="浏览器冒烟焦点",
            ).issue_key,
            actor_id=actor,
        )
        session.commit()
        issue_key = str(issue.issue_key)

    ws_page = client.get(f"/cases/{case_id}")
    assert ws_page.status_code == 200
    html = ws_page.text
    for needle in (
        "案件卷宗",
        "争议焦点",
        "Agent 对话",
        "issue-workbench-panel",
        "issues-overview-panel",
        "ws-dossier",
        "agent-panel",
    ):
        assert needle in html, f"missing {needle}"
    checks.append("workspace layout")

    ws_api = client.get(f"/api/cases/{case_id}/workspace").json()
    assert ws_api.get("issue_work_product") is not None
    checks.append("issue work product")

    iwp = client.get(f"/api/cases/{case_id}/issue-work-product")
    assert iwp.status_code == 200
    checks.append("case issue work product API")

    issue_wp = client.get(f"/api/cases/{case_id}/issues/{issue_key}/work-product")
    assert issue_wp.status_code == 200
    checks.append("issue workbench API")

    agent = client.post(
        f"/cases/{case_id}/agent/messages",
        json={
            "message": "当前焦点证明情况如何？",
            "current_issue_key": issue_key,
            "current_issue_version": 1,
            "current_object_type": "Issue",
            "current_object_ref": issue_key,
        },
    )
    assert agent.status_code == 200
    assert agent.json().get("message")
    checks.append("agent issue context")

    readiness = client.get(f"/api/cases/{case_id}/workspace").json().get("pleading_readiness")
    assert readiness is not None
    checks.append("readiness panel data")

    print("ISSUE WORKSPACE BROWSER SMOKE: PASS")
    for c in checks:
        print(" ", c)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
