"""Post-checks for live LLM case."""

from __future__ import annotations

import json
from urllib.request import Request, urlopen

from sqlalchemy import create_engine, text

from backend.infrastructure.config import get_settings

CASE = "0ad1054b-8242-4e77-9990-ff9ea1cc0dde"


def agent(msg: str, cid=None):
    body = json.dumps({"message": msg, "conversation_id": cid}, ensure_ascii=False).encode()
    r = Request(
        f"http://127.0.0.1:8000/cases/{CASE}/agent/messages",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urlopen(r, timeout=120) as resp:
        return json.loads(resp.read().decode())


def main() -> None:
    r1 = agent("好")
    r2 = agent("可以")
    print("hao", r1["intent"], r1.get("error_code"))
    print("keyi", r2["intent"], r2.get("error_code"))

    eng = create_engine(get_settings().database_url)
    with eng.connect() as c:
        rows = c.execute(
            text(
                """
                select skill_code, status, error_code,
                       metrics_json -> 'llm' as llm,
                       metrics_json -> 'output' -> 'conflicts' as conflicts
                from skill_executions
                order by created_at desc
                limit 6
                """
            )
        ).mappings()
        for row in rows:
            print(dict(row))


if __name__ == "__main__":
    main()
