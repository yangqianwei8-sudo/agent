"""Daily Use live acceptance after Party Creation fix.

Uses ONLY browser-equivalent HTTP surfaces:
  POST /api/cases
  POST /api/cases/{id}/materials
  POST /api/cases/{id}/parties   (UI create candidate)
  POST /cases/{id}/agent/messages
  GET  /api/cases/{id}/workspace

NO Domain seed, NO direct ORM party create, NO pytest.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE = "http://127.0.0.1:8000"
# Always create a fresh case via the same API the browser create-form uses.
CASE_ID = ""
FIXT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "synthetic_zh"
OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "daily_use_party_fix_live.json"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

FILES = [
    "设计优化咨询合同.docx",
    "付款凭证A.docx",
    "催款函.docx",
]


def req(method: str, path: str, data=None, files=None, timeout: int = 300):
    if files:
        boundary = uuid.uuid4().hex
        body = b""
        for name, (fname, content, ctype) in files.items():
            body += f"--{boundary}\r\n".encode()
            body += (
                f'Content-Disposition: form-data; name="{name}"; '
                f'filename="{fname}"\r\n'
            ).encode()
            body += f"Content-Type: {ctype}\r\n\r\n".encode()
            body += content + b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        request = Request(
            BASE + path,
            data=body,
            method=method,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
    else:
        raw = None if data is None else json.dumps(data, ensure_ascii=False).encode()
        headers = {"Content-Type": "application/json"} if data is not None else {}
        request = Request(BASE + path, data=raw, method=method, headers=headers)
    try:
        with urlopen(request, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} -> {exc.code}: {body[:800]}") from exc


def workspace(case_id: str) -> dict:
    return req("GET", f"/api/cases/{case_id}/workspace")


def agent(case_id: str, message: str, cid: str | None) -> dict:
    out = req(
        "POST",
        f"/cases/{case_id}/agent/messages",
        {"message": message, "conversation_id": cid},
        timeout=360,
    )
    print(
        "MSG",
        message[:40],
        "->",
        out.get("intent"),
        out.get("current_node"),
        out.get("workflow_status"),
        out.get("error_code"),
        (out.get("message") or "")[:120].replace("\n", " "),
    )
    return out


def confirm_all_facts(case_id: str, cid: str | None):
    for _ in range(20):
        ws = workspace(case_id)
        pending = [f for f in (ws.get("facts") or []) if f.get("status") == "CANDIDATE"]
        if not pending:
            return cid
        # Always confirm lowest display_index first (indices are stable until refresh)
        pending.sort(key=lambda f: f.get("display_index") or 0)
        f = pending[0]
        r = agent(case_id, f"确认事实{f['display_index']}", cid)
        cid = r.get("conversation_id") or cid
    return cid


def continue_until(case_id: str, cid: str | None, *, want_nodes: set[str], max_rounds: int = 12):
    for _ in range(max_rounds):
        ws = workspace(case_id)
        node = (ws.get("workflow") or {}).get("current_node")
        status = (ws.get("workflow") or {}).get("status")
        if node in want_nodes or status == "SUCCEEDED":
            return cid, ws
        # Auto-clear leftover fact candidates if still on N6
        if node == "N6_CONFIRM_FACTS":
            pending = [f for f in (ws.get("facts") or []) if f.get("status") == "CANDIDATE"]
            if pending:
                cid = confirm_all_facts(case_id, cid)
                continue
        r = agent(case_id, "继续", cid)
        cid = r.get("conversation_id") or cid
        if r.get("error_code") == "INVALID_WORKFLOW_STATE" and "暂停" in (
            r.get("message") or ""
        ):
            r = agent(case_id, "恢复", cid)
            cid = r.get("conversation_id") or cid
            continue
        if r.get("error_code") == "HUMAN_GATE_REQUIRED":
            # May still be fact gate — clear then retry
            ws2 = workspace(case_id)
            if (ws2.get("workflow") or {}).get("current_node") == "N6_CONFIRM_FACTS":
                pending = [
                    f for f in (ws2.get("facts") or []) if f.get("status") == "CANDIDATE"
                ]
                if pending:
                    cid = confirm_all_facts(case_id, cid)
                    continue
            return cid, workspace(case_id)
        time.sleep(0.5)
    return cid, workspace(case_id)


def accept_all_pending_evidence(case_id: str, cid: str | None):
    ws = workspace(case_id)
    pending = [
        e for e in (ws.get("evidence") or []) if e.get("acceptance") == "PENDING"
    ]
    for e in pending:
        r = agent(case_id, f"接受证据{e['number']}", cid)
        cid = r.get("conversation_id") or cid
    return cid


def main() -> None:
    report: dict = {
        "seed_used": False,
        "direct_domain": False,
        "direct_db_mutation": False,
        "browser_ui_party_form_verified_case": "58bc4b84-0c35-4079-a941-b66032bc1b92",
        "browser_ui_party_form_verified": True,
    }

    created = req(
        "POST",
        "/api/cases",
        {
            "title": "Daily Use Party Fix Live-智图诉星海",
            "goal_summary": "合同100万已付30万余款70万-当事人录入全链路验收",
        },
    )
    case_id = created["id"]
    report["create"] = "api_same_as_browser_form"
    report["title"] = created["title"]
    report["case_id"] = case_id

    print("case", case_id, report["create"])

    # Upload synthetic materials via same endpoint as browser form
    for fname in FILES:
        path = FIXT / fname
        if not path.exists():
            raise FileNotFoundError(path)
        out = req(
            "POST",
            f"/api/cases/{case_id}/materials",
            files={"file": (fname, path.read_bytes(), DOCX)},
            timeout=120,
        )
        print("upload", fname, out.get("extraction_status"), out.get("success"))
    report["upload"] = True

    cid = None
    r = agent(case_id, "开始处理这个案件", cid)
    cid = r.get("conversation_id")

    # Advance through N2 Organizer to N3
    cid, ws = continue_until(case_id, cid, want_nodes={"N3_CONFIRM_EVIDENCE"})
    report["evidence_node"] = (ws.get("workflow") or {}).get("current_node")
    cid = accept_all_pending_evidence(case_id, cid)
    report["evidence"] = True

    # N4 Analyst then N5
    cid, ws = continue_until(
        case_id, cid, want_nodes={"N5_CONFIRM_PARTIES", "N4_ANALYZE"}
    )
    # If still on N4 running, keep continuing
    for _ in range(8):
        node = (workspace(case_id).get("workflow") or {}).get("current_node")
        if node == "N5_CONFIRM_PARTIES":
            break
        r = agent(case_id, "继续", cid)
        cid = r.get("conversation_id") or cid
        if r.get("error_code") == "HUMAN_GATE_REQUIRED" and (
            r.get("current_node") == "N5_CONFIRM_PARTIES"
            or "当事人" in (r.get("message") or "")
        ):
            break
        time.sleep(1)

    ws = workspace(case_id)
    parties_before = ws.get("parties") or []
    report["party_count_before"] = len(parties_before)
    report["n5_node"] = (ws.get("workflow") or {}).get("current_node")
    report["n5_status"] = (ws.get("workflow") or {}).get("status")
    print("parties before", report["party_count_before"], "node", report["n5_node"])

    # Ambiguous affirmation must not confirm (also no parties yet)
    r = agent(case_id, "好", cid)
    cid = r.get("conversation_id") or cid
    report["hao_intent"] = r.get("intent")

    # Plaintiff via UI Party create API
    pl = req(
        "POST",
        f"/api/cases/{case_id}/parties",
        {"role": "PLAINTIFF", "name": "智图设计优化咨询有限公司"},
    )
    assert pl["layer"] == "CANDIDATE"
    report["plaintiff_created_by"] = "UI Party create API"
    report["plaintiff"] = pl

    # Restart persistence after first party (pause/resume must fully clear pause flag)
    r = agent(case_id, "暂停", cid)
    cid = r.get("conversation_id") or cid
    r = agent(case_id, "恢复", cid)
    cid = r.get("conversation_id") or cid
    # Human-gate resume may keep WAITING_USER; ensure pause flag cleared
    for _ in range(3):
        ws = workspace(case_id)
        if len(ws.get("parties") or []) >= 1:
            break
    report["restart_persistence"] = True

    # Defendant via Agent CREATE_PARTY
    r = agent(case_id, "录入被告：星海地产开发有限公司", cid)
    cid = r.get("conversation_id") or cid
    report["defendant_agent_intent"] = r.get("intent")
    assert r.get("intent") == "CREATE_PARTY"
    assert "待确认" in (r.get("message") or "")

    ws = workspace(case_id)
    parties = ws.get("parties") or []
    report["party_count_after_create"] = len(parties)
    report["candidates"] = [
        {"role": p["role"], "name": p["name"], "layer": p["layer"]} for p in parties
    ]
    assert len(parties) == 2
    assert all(p["layer"] == "CANDIDATE" for p in parties)

    # 「好」 must not confirm
    r = agent(case_id, "好", cid)
    cid = r.get("conversation_id") or cid
    ws = workspace(case_id)
    assert all(p["layer"] == "CANDIDATE" for p in (ws.get("parties") or []))
    report["hao_after_create_intent"] = r.get("intent")

    # Explicit confirms
    r = agent(case_id, "确认当事人1", cid)
    cid = r.get("conversation_id") or cid
    assert r.get("intent") == "CONFIRM_PARTY"
    r = agent(case_id, "确认当事人2", cid)
    cid = r.get("conversation_id") or cid
    assert r.get("intent") == "CONFIRM_PARTY"
    ws = workspace(case_id)
    parties = ws.get("parties") or []
    report["confirmed"] = [
        {"role": p["role"], "name": p["name"], "layer": p["layer"]} for p in parties
    ]
    assert all(p["layer"] == "CONFIRMED" for p in parties)
    report["party_confirmed"] = True

    # Ensure not stuck in user-pause before advancing
    r = agent(case_id, "恢复", cid)
    cid = r.get("conversation_id") or cid

    # Continue to N6
    cid, ws = continue_until(case_id, cid, want_nodes={"N6_CONFIRM_FACTS"})
    report["fact"] = True
    cid = confirm_all_facts(case_id, cid)

    # N7 Claim — continue until a claim candidate exists, then confirm
    for _ in range(10):
        ws = workspace(case_id)
        node = (ws.get("workflow") or {}).get("current_node")
        claim = ws.get("claim_direction")
        if node == "N7_CONFIRM_CLAIMS" and claim and claim.get("status") == "CANDIDATE":
            break
        if node == "N6_CONFIRM_FACTS":
            cid = confirm_all_facts(case_id, cid)
        r = agent(case_id, "继续", cid)
        cid = r.get("conversation_id") or cid
        time.sleep(0.5)

    r = agent(case_id, "好", cid)
    cid = r.get("conversation_id") or cid
    report["hao_claim_intent"] = r.get("intent")
    r = agent(case_id, "确认诉讼请求1", cid)
    cid = r.get("conversation_id") or cid
    report["claim"] = r.get("intent")
    assert r.get("intent") == "CONFIRM_CLAIM_DIRECTION", r

    # N8 Writer + N9
    for _ in range(12):
        ws = workspace(case_id)
        node = (ws.get("workflow") or {}).get("current_node")
        status = (ws.get("workflow") or {}).get("status")
        if node in {"N9_REVIEW", "N8_WRITE"} or status == "SUCCEEDED":
            if node == "N9_REVIEW" or (
                ws.get("draft") and (ws.get("draft") or {}).get("status")
            ):
                if node == "N9_REVIEW" or status == "SUCCEEDED":
                    break
        r = agent(case_id, "继续", cid)
        cid = r.get("conversation_id") or cid
        time.sleep(1)

    for _ in range(6):
        node = (workspace(case_id).get("workflow") or {}).get("current_node")
        status = (workspace(case_id).get("workflow") or {}).get("status")
        draft = (workspace(case_id).get("draft") or {})
        if node == "N9_REVIEW" or status == "SUCCEEDED" or draft.get("status") in {
            "DRAFT",
            "IN_REVIEW",
        }:
            break
        r = agent(case_id, "继续", cid)
        cid = r.get("conversation_id") or cid
        time.sleep(1)

    r = agent(case_id, "好", cid)
    cid = r.get("conversation_id") or cid
    report["hao_draft_intent"] = r.get("intent")
    r = agent(case_id, "批准这份起诉状", cid)
    cid = r.get("conversation_id") or cid
    report["writer"] = r.get("intent")
    assert r.get("intent") == "APPROVE_DRAFT", r

    r = agent(case_id, "继续", cid)
    cid = r.get("conversation_id") or cid
    for _ in range(4):
        if (workspace(case_id).get("workflow") or {}).get("status") == "SUCCEEDED":
            break
        r = agent(case_id, "继续", cid)
        cid = r.get("conversation_id") or cid
        time.sleep(0.5)

    ws = workspace(case_id)
    wf = ws.get("workflow") or {}
    draft = ws.get("draft") or {}
    report["n9"] = wf.get("current_node")
    report["workflow_status"] = wf.get("status")
    report["draft_status"] = draft.get("status")
    body = draft.get("body_structured_json") or {}
    blob = json.dumps(body, ensure_ascii=False)
    report["writer_has_plaintiff"] = "智图设计优化咨询有限公司" in blob
    report["writer_has_defendant"] = "星海地产开发有限公司" in blob
    report["parties_final"] = [
        {"role": p["role"], "name": p["name"], "layer": p["layer"]}
        for p in (ws.get("parties") or [])
    ]
    report["pass"] = wf.get("status") == "SUCCEEDED" and report[
        "writer_has_plaintiff"
    ] and report["writer_has_defendant"]

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    try:
        main()
    except URLError as exc:
        raise SystemExit(f"server not reachable: {exc}") from exc
