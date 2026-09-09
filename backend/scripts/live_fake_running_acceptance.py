"""Live acceptance for Fake-RUNNING fix via normal HTTP Agent API (no seed/DB mutate)."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from sqlalchemy import create_engine, text

from backend.infrastructure.config import clear_settings_cache, get_settings

BASE = "http://127.0.0.1:8000"
FIXT = Path(__file__).resolve().parents[1] / "tests" / "acceptance" / "fixtures_v1"
OUT = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "fixtures"
    / "fake_running_live_result.json"
)
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF = "application/pdf"


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


def db_query(sql: str, **params):
    clear_settings_cache()
    eng = create_engine(get_settings().database_url)
    with eng.connect() as conn:
        return [dict(r) for r in conn.execute(text(sql), params).mappings()]


def main() -> None:
    continue_log: list[dict] = []
    evidence: dict = {"continue_actions": continue_log}

    case = req(
        "POST",
        "/api/cases",
        {
            "title": "FakeRunningFix-智图诉星海",
            "goal_summary": "设计优化咨询费纠纷；合同100万已付30万余款70万",
        },
    )
    case_id = case["id"]
    evidence["case_id"] = case_id
    print("case", case_id)

    uploads = [
        ("01_consulting_contract.pdf", PDF),
        ("02_delivery_receipt.docx", DOCX),
        ("03_payment_notice.pdf", PDF),
        ("04_payment_voucher.pdf", PDF),
        ("05_demand_letter.docx", DOCX),
    ]
    for fname, ctype in uploads:
        path = FIXT / fname
        if not path.exists():
            print("skip missing", fname)
            continue
        out = req(
            "POST",
            f"/api/cases/{case_id}/materials",
            files={"file": (fname, path.read_bytes(), ctype)},
        )
        print("upload", fname, out.get("extraction_status") or out.get("success"))

    cid = None

    def agent(msg: str, *, why: str = ""):
        nonlocal cid
        t0 = time.time()
        r = req(
            "POST",
            f"/cases/{case_id}/agent/messages",
            {"message": msg, "conversation_id": cid},
            timeout=300,
        )
        dur = round(time.time() - t0, 2)
        cid = r.get("conversation_id")
        entry = {
            "msg": msg,
            "why": why,
            "intent": r.get("intent"),
            "node": r.get("current_node"),
            "status": r.get("workflow_status"),
            "error": r.get("error_code"),
            "duration_s": dur,
            "message": (r.get("message") or "")[:300],
        }
        if msg.strip() == "继续":
            continue_log.append(entry)
        print(
            "MSG",
            msg,
            "->",
            entry["node"],
            entry["status"],
            entry["error"],
            f"{dur}s",
            entry["message"].replace("\n", " ")[:120],
        )
        return r

    r = agent("开始处理这个案件", why="create+start; expect auto N0-N2 to N3")
    assert r.get("current_node") == "N3_CONFIRM_EVIDENCE", r
    assert r.get("workflow_status") == "WAITING_USER", r
    evidence["after_start_node"] = r.get("current_node")

    ws = req("GET", f"/api/cases/{case_id}/workspace")
    pending = [e["number"] for e in ws["evidence"] if e["acceptance"] == "PENDING"]
    assert pending, "no pending evidence"
    for n in pending[:-1]:
        agent(f"接受证据{n}", why="N3 human gate accept")
    agent(f"排除证据{pending[-1]}", why="N3 human gate exclude")

    # Critical: ONE continue must run N4 for real and land on N5
    t_n4 = time.time()
    r = agent("继续", why="N3 complete → must execute N4 Analyst+LLM → N5")
    evidence["n3_continue_duration_s"] = round(time.time() - t_n4, 2)
    evidence["after_n3_continue_node"] = r.get("current_node")
    evidence["after_n3_continue_message"] = r.get("message")
    assert r.get("current_node") == "N5_CONFIRM_PARTIES", (
        f"expected N5 after one CONTINUE, got {r.get('current_node')}: {r.get('message')}"
    )

    n4_rows = db_query(
        """
        select nr.id, nr.status, nr.started_at, nr.ended_at,
               se.id as skill_id, se.status as skill_status, se.skill_code
        from node_runs nr
        join workflow_nodes wn on wn.id = nr.node_id
        join workflow_instances wi on wi.id = nr.instance_id
        left join skill_executions se on se.node_run_id = nr.id
        where wi.case_id = :cid and wn.code = 'N4_ANALYZE'
        order by nr.attempt
        """,
        cid=case_id,
    )
    evidence["n4_node_runs"] = [
        {
            "status": x["status"],
            "started_at": str(x["started_at"]),
            "ended_at": str(x["ended_at"]),
            "skill_code": x["skill_status"] and x["skill_code"],
            "skill_status": x["skill_status"],
        }
        for x in n4_rows
    ]
    assert n4_rows, "N4 NodeRun missing"
    assert all(x["status"] != "RUNNING" for x in n4_rows), n4_rows
    assert any(x["status"] == "SUCCEEDED" for x in n4_rows), n4_rows
    assert any(x["skill_code"] == "CaseAnalystSkill" for x in n4_rows), n4_rows
    print("N4 evidence", evidence["n4_node_runs"])

    # Parties via API (no Domain shell)
    ws = req("GET", f"/api/cases/{case_id}/workspace")
    if not ws.get("parties"):
        req(
            "POST",
            f"/api/cases/{case_id}/parties",
            {
                "role": "PLAINTIFF",
                "name": "智图设计优化咨询有限公司",
                "party_type": "ORG",
            },
        )
        req(
            "POST",
            f"/api/cases/{case_id}/parties",
            {
                "role": "DEFENDANT",
                "name": "星海地产开发有限公司",
                "party_type": "ORG",
            },
        )
    for _ in range(8):
        ws = req("GET", f"/api/cases/{case_id}/workspace")
        if (ws.get("workflow") or {}).get("current_node") != "N5_CONFIRM_PARTIES":
            break
        pending_p = [
            p["display_index"]
            for p in (ws.get("parties") or [])
            if str(p.get("layer") or "").upper() == "CANDIDATE"
        ]
        if not pending_p:
            agent("继续", why="N5 complete → N6")
            break
        for i in pending_p:
            agent(f"确认当事人{i}", why="N5 confirm party")
        agent("继续", why="N5 complete → N6")

    for _ in range(30):
        ws = req("GET", f"/api/cases/{case_id}/workspace")
        node = (ws.get("workflow") or {}).get("current_node")
        if node == "N7_CONFIRM_CLAIMS":
            break
        if node != "N6_CONFIRM_FACTS":
            agent("继续", why=f"advance from {node}")
            continue
        facts = ws.get("facts") or []
        changed = False
        for f in facts:
            if str(f.get("status") or "").upper() != "CANDIDATE":
                continue
            idx = f.get("display_index")
            stmt = f.get("statement") or ""
            if "500000" in stmt or "五十万" in stmt:
                agent(f"拒绝事实{idx}", why="N6 reject conflict")
            else:
                agent(f"确认事实{idx}", why="N6 confirm fact")
            changed = True
        agent("继续", why="N6 complete or propose next")
        if not changed:
            pass

    # N7 propose / confirm / complete → N8 → N9
    for _ in range(6):
        ws = req("GET", f"/api/cases/{case_id}/workspace")
        node = (ws.get("workflow") or {}).get("current_node")
        status = (ws.get("workflow") or {}).get("status")
        if node == "N9_REVIEW":
            break
        if status == "WAITING_RETRY":
            agent("重试", why="N7 claim propose failed → retry")
            continue
        if node != "N7_CONFIRM_CLAIMS":
            agent("继续", why=f"advance toward N7 from {node}")
            continue
        claim = ws.get("claim_direction")
        if not claim or str(claim.get("status") or "").upper() == "CANDIDATE":
            # May need propose
            if not claim:
                r = agent("继续", why="N7 propose claims")
                if r.get("error_code") or (ws.get("workflow") or {}).get("status") == "WAITING_RETRY":
                    continue
                ws = req("GET", f"/api/cases/{case_id}/workspace")
                claim = ws.get("claim_direction")
        if claim and str(claim.get("status") or "").upper() == "CANDIDATE":
            agent("确认诉讼请求1", why="N7 confirm claim")
        r = agent("继续", why="N7 complete → must execute N8 Writer → N9")
        evidence["after_n7_continue_node"] = r.get("current_node")
        if r.get("current_node") == "N9_REVIEW":
            break
        if (req("GET", f"/api/cases/{case_id}/workspace").get("workflow") or {}).get(
            "status"
        ) == "WAITING_RETRY":
            continue

    ws = req("GET", f"/api/cases/{case_id}/workspace")
    assert (ws.get("workflow") or {}).get("current_node") == "N9_REVIEW", ws.get(
        "workflow"
    )
    n8_rows = db_query(
        """
        select nr.status, se.skill_code, se.status as skill_status
        from node_runs nr
        join workflow_nodes wn on wn.id = nr.node_id
        join workflow_instances wi on wi.id = nr.instance_id
        left join skill_executions se on se.node_run_id = nr.id
        where wi.case_id = :cid and wn.code = 'N8_WRITE'
        """,
        cid=case_id,
    )
    evidence["n8_node_runs"] = n8_rows
    assert n8_rows and all(x["status"] != "RUNNING" for x in n8_rows), n8_rows

    # Unknown must not pierce gate
    r = agent("好", why="must be UNKNOWN at N9")
    evidence["hao_intent"] = r.get("intent")
    assert r.get("intent") == "UNKNOWN" or r.get("error_code"), r

    agent("批准这份起诉状", why="N9 approve draft")
    r = agent("继续", why="N9 complete after approve → SUCCEEDED")
    ws = req("GET", f"/api/cases/{case_id}/workspace")
    evidence["final_workflow"] = ws.get("workflow")
    evidence["continue_count"] = len(continue_log)
    evidence["after_approve_continue"] = {
        "node": r.get("current_node"),
        "status": r.get("workflow_status"),
        "message": (r.get("message") or "")[:200],
    }

    zombies = db_query(
        """
        select wn.code, nr.status, nr.started_at, nr.ended_at
        from node_runs nr
        join workflow_nodes wn on wn.id = nr.node_id
        join workflow_instances wi on wi.id = nr.instance_id
        where wi.case_id = :cid and nr.status = 'RUNNING'
        """,
        cid=case_id,
    )
    evidence["zombie_running_in_case"] = zombies
    assert not zombies, zombies

    wf = (ws.get("workflow") or {}).get("status")
    evidence["verdict"] = "PASS" if wf == "SUCCEEDED" else "FAIL"
    OUT.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("CONTINUE_COUNT", evidence["continue_count"])
    for c in continue_log:
        print("CONTINUE", c["why"], "->", c["node"], c["status"])
    print("FINAL", wf, "wrote", OUT)
    if wf != "SUCCEEDED":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
