"""Live Real-LLM Stage-2 acceptance: ClaimDirection + Writer + N7/N9 gates."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from sqlalchemy import create_engine, text

from backend.domain.services import DomainService
from backend.infrastructure.config import clear_settings_cache, get_settings
from backend.infrastructure.db import get_session_factory

BASE = "http://127.0.0.1:8000"
FIXT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "synthetic_zh"
OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "llm_stage2_live_result.json"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def req(method: str, path: str, data=None, files=None):
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
        with urlopen(request, timeout=300) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} -> {exc.code}: {body[:500]}") from exc


def db_query(sql: str, **params):
    clear_settings_cache()
    eng = create_engine(get_settings().database_url)
    with eng.connect() as conn:
        return [dict(r) for r in conn.execute(text(sql), params).mappings()]


def ensure_candidate_parties(case_id: str, actor_id: str) -> None:
    """Acceptance harness: seed CANDIDATE parties if Analyst produced none (same as V1)."""
    existing = db_query(
        "select count(*) as c from case_parties where case_id = :cid and is_current",
        cid=case_id,
    )[0]["c"]
    if existing:
        return
    Session = get_session_factory()
    with Session() as session:
        svc = DomainService(session)
        svc.create_party(
            case_id=uuid.UUID(case_id),
            role="PLAINTIFF",
            name="智图设计优化咨询有限公司",
            party_type="ORG",
            actor_id=uuid.UUID(actor_id),
        )
        svc.create_party(
            case_id=uuid.UUID(case_id),
            role="DEFENDANT",
            name="星海地产开发有限公司",
            party_type="ORG",
            actor_id=uuid.UUID(actor_id),
        )
        session.commit()
    print("seeded CANDIDATE parties for lawyer confirmation")


def main() -> None:
    case = req(
        "POST",
        "/api/cases",
        {
            "title": "真实LLM Stage2-智图诉星海-设计优化咨询费",
            "goal_summary": "合同1000000已付300000余款700000",
        },
    )
    case_id = case["id"]
    print("case", case_id)

    for fname in [
        "设计优化咨询合同.docx",
        "付款凭证A.docx",
        "对方主张付款函.docx",
        "催款函.docx",
    ]:
        content = (FIXT / fname).read_bytes()
        out = req(
            "POST",
            f"/api/cases/{case_id}/materials",
            files={"file": (fname, content, DOCX)},
        )
        print("upload", fname, out.get("extraction_status"), out.get("success"))

    cid = None
    log: list[dict] = []

    def agent(msg: str):
        nonlocal cid
        r = req(
            "POST",
            f"/cases/{case_id}/agent/messages",
            {"message": msg, "conversation_id": cid},
        )
        cid = r.get("conversation_id")
        entry = {
            "msg": msg,
            "intent": r.get("intent"),
            "node": r.get("current_node"),
            "error": r.get("error_code"),
            "message": (r.get("message") or "")[:220],
        }
        log.append(entry)
        print(
            "MSG",
            msg,
            "->",
            entry["intent"],
            entry["node"],
            entry["error"],
            entry["message"].replace("\n", " ")[:140],
        )
        return r

    agent("现在做到哪了？")
    agent("开始处理这个案件")
    for _ in range(12):
        r = agent("继续")
        if r.get("current_node") == "N3_CONFIRM_EVIDENCE":
            break
        if r.get("error_code") in {"LLM_REQUEST_FAILED", "ENGINE_FAILED"}:
            break

    ws = req("GET", f"/api/cases/{case_id}/workspace")
    print("AI", ws.get("ai"))
    pending = [e["number"] for e in ws["evidence"] if e["acceptance"] == "PENDING"]
    # Keep conflict letter accepted? Stage1 excluded催款; keep conflict ACCEPTED for analyst
    for n in pending:
        agent(f"接受证据{n}")

    for _ in range(12):
        r = agent("继续")
        ws = req("GET", f"/api/cases/{case_id}/workspace")
        node = (ws.get("workflow") or {}).get("current_node")
        if node in {"N5_CONFIRM_PARTIES", "N6_CONFIRM_FACTS", "N7_CONFIRM_CLAIMS"}:
            if ws.get("parties") or ws.get("facts"):
                break
        if r.get("error_code") in {"LLM_REQUEST_FAILED", "ENGINE_FAILED"}:
            break

    # Confirm parties (seed CANDIDATE if Analyst omitted — Domain gate still requires lawyer confirm)
    actor = "00000000-0000-4000-8000-000000000001"
    ensure_candidate_parties(case_id, actor)

    for _ in range(8):
        ws = req("GET", f"/api/cases/{case_id}/workspace")
        node = (ws.get("workflow") or {}).get("current_node")
        if node != "N5_CONFIRM_PARTIES":
            break
        parties = ws.get("parties") or []
        pending_p = [
            p["display_index"]
            for p in parties
            if str(p.get("layer") or "").upper() == "CANDIDATE"
        ]
        if not pending_p:
            agent("继续")
            break
        for i in pending_p:
            agent(f"确认当事人{i}")
        agent("继续")

    # Confirm facts — reject conflict 500000 claim; confirm others
    for _ in range(20):
        ws = req("GET", f"/api/cases/{case_id}/workspace")
        node = (ws.get("workflow") or {}).get("current_node")
        if node == "N7_CONFIRM_CLAIMS":
            break
        if node not in {"N6_CONFIRM_FACTS", "N5_CONFIRM_PARTIES"}:
            agent("继续")
            continue
        facts = ws.get("facts") or []
        changed = False
        for f in facts:
            st = (f.get("status") or "").upper()
            if st != "CANDIDATE":
                continue
            stmt = f.get("statement") or ""
            idx = f.get("display_index")
            if "500000" in stmt or "五十万" in stmt:
                agent(f"拒绝事实{idx}")
            else:
                agent(f"确认事实{idx}")
            changed = True
        agent("继续")
        if not changed and node == "N6_CONFIRM_FACTS":
            # maybe still waiting — continue once more
            pass

    # Pre-N7 DB check
    confirmed_facts = db_query(
        """
        select statement, status, stale, version
        from facts
        where case_id = :cid and is_current and status = 'CONFIRMED' and stale = false
        order by created_at
        """,
        cid=case_id,
    )
    confirmed_parties = db_query(
        """
        select role, name, layer
        from case_parties
        where case_id = :cid and is_current and layer = 'CONFIRMED'
        """,
        cid=case_id,
    )
    print("confirmed_facts", len(confirmed_facts), "parties", len(confirmed_parties))

    # Enter N7 propose
    for _ in range(6):
        ws = req("GET", f"/api/cases/{case_id}/workspace")
        node = (ws.get("workflow") or {}).get("current_node")
        if node == "N7_CONFIRM_CLAIMS":
            break
        agent("继续")

    agent("继续")  # propose claims via real LLM
    claims_before = db_query(
        """
        select claim_direction_key::text, status, version,
               payload->'claims'->0->>'amount' as amount,
               payload->'claims'->0->>'claim_type' as claim_type
        from claim_directions
        where case_id = :cid and is_current
        order by created_at desc
        """,
        cid=case_id,
    )
    print("claims_after_propose", claims_before)

    # N7 ambiguous
    hd0 = db_query(
        "select count(*) as c from human_decisions where case_id = :cid",
        cid=case_id,
    )[0]["c"]
    agent("好")
    claims_mid = db_query(
        """
        select status from claim_directions
        where case_id = :cid and is_current order by created_at desc limit 1
        """,
        cid=case_id,
    )
    hd1 = db_query(
        "select count(*) as c from human_decisions where case_id = :cid",
        cid=case_id,
    )[0]["c"]
    print("after_hao claim", claims_mid, "hd", hd0, "->", hd1)

    agent("确认诉讼请求1")
    claims_ok = db_query(
        """
        select status, payload->'claims'->0->>'amount' as amount
        from claim_directions
        where case_id = :cid and is_current and status = 'CONFIRMED'
        """,
        cid=case_id,
    )
    print("confirmed_claims", claims_ok)

    agent("继续")  # N7 complete -> N8
    for _ in range(4):
        r = agent("继续")  # Writer
        if r.get("current_node") in {"N9_REVIEW", "N8_WRITE"}:
            if r.get("current_node") == "N9_REVIEW" or "起诉状" in (r.get("message") or ""):
                break

    drafts = db_query(
        """
        select id::text, status, version,
               body_structured_json->'claims'->0->>'amount' as amount
        from document_drafts
        where case_id = :cid
        order by created_at desc
        """,
        cid=case_id,
    )
    print("drafts", drafts)

    # N9 ambiguous
    agent("好")
    drafts_mid = db_query(
        """
        select status from document_drafts
        where case_id = :cid order by created_at desc limit 1
        """,
        cid=case_id,
    )
    print("after_hao draft", drafts_mid)

    agent("批准这份起诉状")
    drafts_ok = db_query(
        """
        select status from document_drafts
        where case_id = :cid order by created_at desc limit 1
        """,
        cid=case_id,
    )
    print("after_approve", drafts_ok)

    for _ in range(4):
        r = agent("继续")
        if r.get("workflow_status") == "SUCCEEDED" or (
            (req("GET", f"/api/cases/{case_id}/workspace").get("workflow") or {}).get("status")
            == "SUCCEEDED"
        ):
            break

    ws = req("GET", f"/api/cases/{case_id}/workspace")
    skills = db_query(
        """
        select se.skill_code, se.status, se.error_code,
               se.metrics_json -> 'llm' ->> 'engine_mode' as engine_mode,
               se.metrics_json -> 'llm' ->> 'model' as model,
               se.metrics_json -> 'llm' ->> 'prompt_version' as prompt_version
        from skill_executions se
        join node_runs nr on nr.id = se.node_run_id
        join workflow_instances wi on wi.id = nr.instance_id
        where wi.case_id = :cid
        order by se.created_at
        """,
        cid=case_id,
    )
    cites = db_query(
        """
        select dc.block_id, dc.citation_kind,
               dc.fact_key::text, dc.fact_version,
               dc.evidence_item_id::text, dc.evidence_item_version
        from draft_citations dc
        join document_drafts d on d.id = dc.draft_id
        where d.case_id = :cid
        order by dc.created_at
        limit 10
        """,
        cid=case_id,
    )
    # provenance sample
    provenance = []
    for c in cites[:3]:
        if not c.get("fact_key"):
            continue
        row = db_query(
            """
            select f.statement,
                   e.id::text as evidence_id, e.version as evidence_version,
                   ss.id::text as span_id, ec.id::text as ec_id, cm.id::text as material_id
            from facts f
            join fact_evidence_links fel on fel.fact_id = f.id
            join evidence_items e on e.id = fel.evidence_item_id and e.is_current
            join evidence_item_spans ess on ess.evidence_item_id = e.id
            join source_spans ss on ss.id = ess.source_span_id
            join extracted_contents ec on ec.id = ss.extracted_content_id
            join case_materials cm on cm.id = ec.material_id
            where f.fact_key = :fk and f.version = :fv and f.is_current
            limit 1
            """,
            fk=c["fact_key"],
            fv=c["fact_version"],
        )
        provenance.append({"citation": c, "chain": row[0] if row else None})

    result = {
        "case_id": case_id,
        "ai": ws.get("ai"),
        "workflow": ws.get("workflow"),
        "confirmed_facts": [
            {"statement": f["statement"][:120], "version": f["version"]}
            for f in confirmed_facts
        ],
        "claims": claims_ok,
        "drafts": drafts_ok,
        "skills": skills,
        "citations_sample": cites[:5],
        "provenance_sample": provenance,
        "n7_hao_claim_status": claims_mid,
        "n7_hao_hd_unchanged": hd0 == hd1,
        "n9_hao_draft_status": drafts_mid,
        "agent_log": log[-40:],
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("wrote", OUT)
    print("final_workflow", ws.get("workflow"))


if __name__ == "__main__":
    try:
        main()
    except URLError as exc:
        raise SystemExit(f"server not reachable: {exc}") from exc
