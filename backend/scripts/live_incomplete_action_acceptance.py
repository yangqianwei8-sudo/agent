"""Live Incomplete Action Clarification UX — real DeepSeek via HTTP."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import httpx
from sqlalchemy import func, select

from backend.domain.services import DomainService
from backend.infrastructure.db import get_engine, get_session_factory
from backend.models import (
    CaseParty,
    EvidenceItem,
    Fact,
    HumanDecision,
    WorkflowInstance,
)

BASE = "http://127.0.0.1:8000"
OUT = Path("backend/tests/fixtures/incomplete_action_live_result.json")


def _snap(session, case_id: uuid.UUID) -> dict:
    wf = session.scalars(
        select(WorkflowInstance)
        .where(WorkflowInstance.case_id == case_id)
        .order_by(WorkflowInstance.created_at.desc())
    ).first()
    return {
        "parties": int(
            session.scalar(
                select(func.count())
                .select_from(CaseParty)
                .where(CaseParty.case_id == case_id, CaseParty.is_current.is_(True))
            )
            or 0
        ),
        "decisions": int(
            session.scalar(
                select(func.count())
                .select_from(HumanDecision)
                .where(HumanDecision.case_id == case_id)
            )
            or 0
        ),
        "evidence_accepted": int(
            session.scalar(
                select(func.count())
                .select_from(EvidenceItem)
                .where(
                    EvidenceItem.case_id == case_id,
                    EvidenceItem.is_current.is_(True),
                    EvidenceItem.acceptance == "ACCEPTED",
                )
            )
            or 0
        ),
        "facts_confirmed": int(
            session.scalar(
                select(func.count())
                .select_from(Fact)
                .where(
                    Fact.case_id == case_id,
                    Fact.is_current.is_(True),
                    Fact.status == "CONFIRMED",
                )
            )
            or 0
        ),
        "wf_status": wf.status if wf else None,
    }


def _delta(before: dict, after: dict) -> dict:
    return {
        k: {"before": before.get(k), "after": after.get(k)}
        for k in before
        if after.get(k) != before.get(k)
    }


def main() -> None:
    get_engine()
    Session = get_session_factory()
    actor = uuid.UUID("00000000-0000-4000-8000-000000000001")

    with Session() as session:
        svc = DomainService(session)
        case = svc.create_case(
            title="Incomplete Action UX Live",
            owner_user_id=actor,
            goal_summary="验收缺参澄清体验",
            actor_id=actor,
        )
        text = "合同摘录。甲方四川富茂置业有限公司。乙方四川维海科技有限公司。"
        material = svc.register_material(
            case_id=case.id,
            filename="合同.pdf",
            mime="application/pdf",
            byte_size=len(text),
            content_hash=f"h-{uuid.uuid4().hex[:12]}",
            storage_key=f"k-{uuid.uuid4().hex[:12]}",
            created_by=actor,
        )
        ec = svc.create_extracted_content(
            material_id=material.id,
            extraction_method="pdfplumber",
            extraction_version="v1",
            full_text=text,
            actor_id=actor,
        )
        span = svc.create_source_span(
            material_id=material.id,
            extracted_content_id=ec.id,
            character_start=0,
            character_end=len(text),
            quote=text,
            extraction_method="pdfplumber",
            extraction_version="v1",
            page=1,
        )
        evs = []
        for i in range(1, 4):
            evs.append(
                svc.create_evidence_item(
                    case_id=case.id,
                    number=str(i),
                    title=f"证据材料{i}",
                    category="CONTRACT",
                    summary=f"摘录{i}",
                    source_span_ids=[span.id],
                    actor_id=actor,
                )
            )
        # Accept evidence1 so facts can be proposed; leave 2/3 pending
        accepted = svc.accept_evidence(evs[0].id, actor_id=actor)
        facts = []
        for i in range(2):
            facts.append(
                svc.propose_fact(
                    case_id=case.id,
                    statement=f"候选事实{i + 1}：双方存在合同关系相关陈述足够长。",
                    actor_id=actor,
                    evidence_links=[
                        {
                            "evidence_item_id": accepted.id,
                            "evidence_item_version": accepted.version,
                        }
                    ],
                )
            )
        # Confirm fact2 so amend path is Domain-legal; fact1 stays candidate for confirm turn
        svc.confirm_fact(facts[1].fact_key, actor_id=actor)
        session.commit()
        case_id = case.id

    turns = [
        "把证据接受一下",
        "2",
        "事实也确认一下",
        "哪一条？",
        "事实1",
        "帮我录入一个被告",
        "四川富茂置业有限公司",
        "证据3能证明什么？",
        "把它接受了",
        "帮我改事实2",
        "改成被告已支付10万元，尚欠20万元",
        "算了，先不改",
    ]

    results: list[dict] = []
    conversation_id = None
    ok = True
    notes: list[str] = []

    with httpx.Client(base_url=BASE, timeout=120.0) as client:
        for i, msg in enumerate(turns, start=1):
            with Session() as session:
                before = _snap(session, case_id)
            payload: dict = {"message": msg}
            if conversation_id:
                payload["conversation_id"] = str(conversation_id)
            res = client.post(f"/cases/{case_id}/agent/messages", json=payload)
            data = (
                res.json()
                if res.headers.get("content-type", "").startswith("application/json")
                else {"error": res.text}
            )
            if res.status_code >= 400:
                ok = False
                notes.append(f"T{i} http={res.status_code}")
            conversation_id = data.get("conversation_id") or conversation_id
            with Session() as session:
                after = _snap(session, case_id)
            row = {
                "turn": i,
                "message": msg,
                "http": res.status_code,
                "intent": data.get("intent"),
                "routing_status": data.get("routing_status"),
                "safety_result": data.get("safety_result"),
                "missing_fields": data.get("missing_fields"),
                "pending_action": data.get("pending_action"),
                "recent_focus": data.get("recent_focus"),
                "reply": (data.get("message") or "")[:280],
                "mutation_delta": _delta(before, after),
                "human_decision_delta": after["decisions"] - before["decisions"],
                "workflow_delta": {
                    "status": (before["wf_status"], after["wf_status"]),
                },
            }
            results.append(row)
            print(
                f"T{i} intent={row['intent']} routing={row['routing_status']} "
                f"safety={row['safety_result']} delta={row['mutation_delta']} "
                f"reply={row['reply'][:70]}"
            )

    # Hard expectations
    t1 = results[0]
    if t1["intent"] == "UNKNOWN":
        ok = False
        notes.append("T1 must not be UNKNOWN")
    if t1.get("human_decision_delta", 0) != 0:
        ok = False
        notes.append("T1 mutation")
    if results[1]["intent"] != "ACCEPT_EVIDENCE" and results[1][
        "mutation_delta"
    ].get("evidence_accepted", {}).get("after", 0) <= results[1][
        "mutation_delta"
    ].get("evidence_accepted", {}).get("before", 0):
        # soft: T2 should accept evidence 2
        if not results[1]["mutation_delta"]:
            # check absolute
            pass
    if results[5]["intent"] == "UNKNOWN":
        ok = False
        notes.append("T6 create defendant must not UNKNOWN")
    if results[11]["pending_action"] not in (None, {}):
        # cancel should clear — if T10/T11 left pending, T12 clears
        if results[11].get("safety_result") != "CANCELLED" and results[11].get(
            "pending_action"
        ):
            notes.append("T12 pending not cleared")
            ok = False

    # No UNKNOWN for clear incomplete actions in early turns
    for idx in (0, 2, 5):
        if results[idx]["intent"] == "UNKNOWN":
            ok = False
            notes.append(f"T{idx + 1} UNKNOWN")

    report = {
        "case_id": str(case_id),
        "conversation_id": str(conversation_id) if conversation_id else None,
        "ok": ok,
        "notes": notes,
        "turns": results,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("WROTE", OUT, "ok=", ok, "notes=", notes)
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
