"""Live Real-LLM Stage-1 acceptance against running FastAPI (DOCX materials)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

BASE = "http://127.0.0.1:8000"
FIXT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "synthetic_zh"
OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "llm_live_result.json"
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
    with urlopen(request, timeout=240) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> None:
    case = req(
        "POST",
        "/api/cases",
        {"title": "真实LLM验收2-智图诉星海", "goal_summary": "设计优化咨询服务费"},
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

    def agent(msg: str):
        nonlocal cid
        r = req(
            "POST",
            f"/cases/{case_id}/agent/messages",
            {"message": msg, "conversation_id": cid},
        )
        cid = r.get("conversation_id")
        print(
            "MSG",
            msg,
            "->",
            r.get("intent"),
            r.get("current_node"),
            r.get("error_code"),
            (r.get("message") or "")[:160].replace("\n", " "),
        )
        return r

    agent("现在做到哪了？")
    agent("开始处理这个案件")
    for _ in range(8):
        r = agent("继续")
        if r.get("current_node") == "N3_CONFIRM_EVIDENCE":
            break
        if r.get("error_code") == "LLM_REQUEST_FAILED":
            print("LLM failed; stop")
            break

    ws = req("GET", f"/api/cases/{case_id}/workspace")
    print("AI", ws.get("ai"))
    print("evidence_count", len(ws["evidence"]))
    for e in ws["evidence"]:
        print(" EV", e["number"], e["acceptance"], (e["title"] or "")[:60])

    amb = agent("好")
    print("ambiguous", amb.get("intent"), amb.get("error_code"))

    agent("查看证据")
    nums = [e["number"] for e in ws["evidence"] if e["acceptance"] == "PENDING"]
    for n in nums[:-1]:
        agent(f"接受证据{n}")
    if len(nums) >= 1:
        agent(f"排除证据{nums[-1]}")

    for _ in range(10):
        r = agent("继续")
        ws = req("GET", f"/api/cases/{case_id}/workspace")
        node = (ws.get("workflow") or {}).get("current_node")
        if ws.get("facts") and node in {
            "N5_CONFIRM_PARTIES",
            "N6_CONFIRM_FACTS",
            "N7_CONFIRM_CLAIMS",
        }:
            break
        if r.get("error_code") == "LLM_REQUEST_FAILED":
            break

    ws = req("GET", f"/api/cases/{case_id}/workspace")
    result = {
        "case_id": case_id,
        "ai": ws.get("ai"),
        "workflow": ws.get("workflow"),
        "evidence": [
            {
                "number": e["number"],
                "acceptance": e["acceptance"],
                "title": e["title"],
                "summary": (e.get("summary") or "")[:80],
            }
            for e in ws.get("evidence") or []
        ],
        "facts": [
            {
                "i": f["display_index"],
                "status": f["status"],
                "statement": (f["statement"] or "")[:100],
            }
            for f in ws.get("facts") or []
        ],
        "parties": [
            {"role": p["role"], "name": p["name"], "layer": p["layer"]}
            for p in ws.get("parties") or []
        ],
        "ambiguous_intent": amb.get("intent"),
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", OUT)
    print(
        "summary",
        "ev=",
        len(result["evidence"]),
        "facts=",
        len(result["facts"]),
        "node=",
        (result["workflow"] or {}).get("current_node"),
    )


if __name__ == "__main__":
    main()
