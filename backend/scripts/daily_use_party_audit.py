"""Upload guide acceptance materials via the same API the browser UI uses."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

BASE = "http://127.0.0.1:8000"
CASE = "f9012fc5-122a-4376-a22c-077e3b873b82"
FIXT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "synthetic_zh"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
FILES = [
    "01_设计优化咨询合同.docx",
    "02_付款凭证.docx",
    "03_服务成果及验收说明.docx",
    "04_催款函.docx",
]


def upload(fname: str) -> dict:
    content = (FIXT / fname).read_bytes()
    boundary = uuid.uuid4().hex
    body = b""
    body += f"--{boundary}\r\n".encode()
    body += (
        f'Content-Disposition: form-data; name="file"; filename="{fname}"\r\n'
    ).encode()
    body += f"Content-Type: {DOCX}\r\n\r\n".encode()
    body += content + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    req = Request(
        f"{BASE}/api/cases/{CASE}/materials",
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode())


def agent(msg: str, cid: str | None = None) -> dict:
    payload = {"message": msg, "conversation_id": cid}
    raw = json.dumps(payload, ensure_ascii=False).encode()
    req = Request(
        f"{BASE}/cases/{CASE}/agent/messages",
        data=raw,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urlopen(req, timeout=300) as resp:
        return json.loads(resp.read().decode())


def workspace() -> dict:
    with urlopen(f"{BASE}/api/cases/{CASE}/workspace", timeout=60) as resp:
        return json.loads(resp.read().decode())


def main() -> None:
    for fname in FILES:
        out = upload(fname)
        print("upload", fname, out.get("extraction_status"), out.get("success"))

    cid = None
    for msg in ["开始处理这个案件"]:
        r = agent(msg, cid)
        cid = r.get("conversation_id")
        print("MSG", msg, "->", r.get("intent"), r.get("current_node"), (r.get("message") or "")[:120])

    for _ in range(10):
        r = agent("继续", cid)
        cid = r.get("conversation_id")
        print(
            "MSG 继续 ->",
            r.get("intent"),
            r.get("current_node"),
            r.get("error_code"),
            (r.get("message") or "")[:140].replace("\n", " "),
        )
        if r.get("current_node") == "N3_CONFIRM_EVIDENCE":
            break

    ws = workspace()
    pending = [e for e in (ws.get("evidence") or []) if e.get("acceptance") == "PENDING"]
    print("pending_evidence", len(pending))
    for e in pending:
        r = agent(f"接受证据{e['number']}", cid)
        cid = r.get("conversation_id")
        print("accept", e["number"], r.get("intent"), r.get("error_code"))

    for _ in range(8):
        r = agent("继续", cid)
        cid = r.get("conversation_id")
        print(
            "MSG 继续 ->",
            r.get("current_node"),
            r.get("error_code"),
            (r.get("message") or "")[:160].replace("\n", " "),
        )
        if r.get("current_node") in {"N5_CONFIRM_PARTIES", "N6_CONFIRM_FACTS"}:
            break

    # Human gate fuzzy check at whatever node
    r_hao = agent("好", cid)
    print("hao", r_hao.get("intent"), r_hao.get("error_code"))

    ws = workspace()
    print("AI", ws.get("ai"))
    print("workflow", ws.get("workflow"))
    print("parties_count", len(ws.get("parties") or []))
    print("parties", ws.get("parties"))
    print("facts_count", len(ws.get("facts") or []))

    # Attempt party confirm when none exist
    r_p = agent("确认当事人1", cid)
    print(
        "confirm_party1",
        r_p.get("intent"),
        r_p.get("error_code"),
        (r_p.get("message") or "")[:200],
    )

    # Continue at N5 — expect party gate block
    r_c = agent("继续", cid)
    print(
        "continue_n5",
        r_c.get("current_node"),
        r_c.get("error_code"),
        (r_c.get("message") or "")[:220].replace("\n", " "),
    )

    out = {
        "case_id": CASE,
        "workflow": ws.get("workflow"),
        "parties": ws.get("parties"),
        "hao_intent": r_hao.get("intent"),
        "confirm_party1": {
            "intent": r_p.get("intent"),
            "error": r_p.get("error_code"),
            "message": r_p.get("message"),
        },
        "continue_n5": {
            "node": r_c.get("current_node"),
            "error": r_c.get("error_code"),
            "message": r_c.get("message"),
        },
        "seed_used": False,
    }
    Path(__file__).resolve().parents[1].joinpath(
        "tests/fixtures/daily_use_acceptance.json"
    ).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote daily_use_acceptance.json")


if __name__ == "__main__":
    main()
