"""Live Conversational Case Agent acceptance — real DeepSeek via HTTP."""

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
    ClaimDirection,
    DocumentDraft,
    EvidenceItem,
    Fact,
    HumanDecision,
    WorkflowInstance,
)

BASE = "http://127.0.0.1:8000"
OUT = Path("backend/tests/fixtures/conversational_live_result.json")


def _snap(session, case_id: uuid.UUID) -> dict:
    return {
        "parties": session.scalar(
            select(func.count()).select_from(CaseParty).where(CaseParty.case_id == case_id)
        ),
        "evidence_acc": session.scalar(
            select(func.count())
            .select_from(EvidenceItem)
            .where(
                EvidenceItem.case_id == case_id,
                EvidenceItem.acceptance == "ACCEPTED",
            )
        ),
        "facts_conf": session.scalar(
            select(func.count())
            .select_from(Fact)
            .where(Fact.case_id == case_id, Fact.status == "CONFIRMED")
        ),
        "claims": session.scalar(
            select(func.count())
            .select_from(ClaimDirection)
            .where(ClaimDirection.case_id == case_id)
        ),
        "drafts": session.scalar(
            select(func.count())
            .select_from(DocumentDraft)
            .where(DocumentDraft.case_id == case_id)
        ),
        "decisions": session.scalar(
            select(func.count())
            .select_from(HumanDecision)
            .where(HumanDecision.case_id == case_id)
        ),
        "wf": [
            (i.status, str(i.current_node_id) if i.current_node_id else None)
            for i in session.scalars(
                select(WorkflowInstance).where(WorkflowInstance.case_id == case_id)
            )
        ],
    }


def main() -> None:
    get_engine()
    Session = get_session_factory()
    actor = uuid.UUID("00000000-0000-4000-8000-000000000001")

    with Session() as session:
        svc = DomainService(session)
        case = svc.create_case(
            title="Conversational V1 Live — 设计优化服务费",
            owner_user_id=actor,
            goal_summary="追索设计优化咨询服务费",
            actor_id=actor,
        )
        pl = svc.create_party(
            case_id=case.id,
            role="PLAINTIFF",
            name="成都智图设计有限公司",
            party_type="ORG",
            actor_id=actor,
        )
        df = svc.create_party(
            case_id=case.id,
            role="DEFENDANT",
            name="中梁地产",
            party_type="ORG",
            actor_id=actor,
        )
        svc.confirm_party(pl.party_key, actor_id=actor)
        svc.confirm_party(df.party_key, actor_id=actor)
        text = (
            "设计优化咨询服务合同\n甲方：四川富茂置业有限公司\n乙方：成都智图设计有限公司\n"
            "第六条 服务费计算与支付：设计优化咨询服务费按优化总成本金额的8%计算，"
            "封顶300000元。付款条件为成果验收通过后30日内支付。\n"
        )
        material = svc.register_material(
            case_id=case.id,
            filename="设计优化合同.pdf",
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
        bad = svc.register_material(
            case_id=case.id,
            filename="扫描补充协议.pdf",
            mime="application/pdf",
            byte_size=10,
            content_hash=f"h-{uuid.uuid4().hex[:12]}",
            storage_key=f"k-{uuid.uuid4().hex[:12]}",
            created_by=actor,
        )
        svc.create_extracted_content(
            material_id=bad.id,
            extraction_method="pdfplumber",
            extraction_version="v1",
            status="FAILED",
            error_detail="PDF_NEEDS_OCR: 扫描件",
            actor_id=actor,
        )
        ev = svc.create_evidence_item(
            case_id=case.id,
            number="2",
            title="设计优化咨询服务费计算与支付条款",
            category="CONTRACT",
            summary="第六条：8%计费，封顶300000元",
            source_span_ids=[span.id],
            actor_id=actor,
        )
        svc.accept_evidence(ev.id, actor_id=actor)
        ev = svc.repo.get_current_evidence(ev.id)
        assert ev is not None
        fact = svc.propose_fact(
            case_id=case.id,
            statement="合同第六条约定服务费按优化总成本8%计算并封顶300000元。",
            actor_id=actor,
            evidence_links=[
                {
                    "evidence_item_id": ev.id,
                    "evidence_item_version": ev.version,
                    "source_span_id": span.id,
                }
            ],
        )
        svc.confirm_fact(fact.fact_key, actor_id=actor)
        svc.propose_fact(
            case_id=case.id,
            statement="被告中梁地产应承担付款义务。",
            actor_id=actor,
            evidence_links=[
                {
                    "evidence_item_id": ev.id,
                    "evidence_item_version": ev.version,
                    "source_span_id": span.id,
                }
            ],
        )
        case_id = case.id
        session.commit()

    print("seeded", case_id)
    with Session() as session:
        before = _snap(session, case_id)
    print("before", before)

    questions = [
        "这个案子你先给我讲一下。",
        "最大的风险是什么？",
        "为什么？",
        "合同是谁签的？",
        "那为什么现在被告是这个公司？",
        "如果只起诉合同甲方呢？",
        "两种方案哪个风险更小？",
        "合同服务费到底怎么算？",
        "30万元是固定总价还是封顶？",
        "付款条件是什么？",
        "现在能证明我们履约了吗？",
        "还缺什么证据？",
        "如果你是被告律师怎么抗辩？",
        "证据2具体能证明什么？",
        "你重新看一下第六条原文。",
    ]

    client = httpx.Client(base_url=BASE, timeout=120.0)
    conversation_id = None
    transcript: list[dict] = []
    for i, q in enumerate(questions, 1):
        res = client.post(
            f"/cases/{case_id}/agent/messages",
            json={"message": q, "conversation_id": conversation_id},
        )
        data = res.json()
        if res.status_code >= 400:
            raise SystemExit(f"HTTP {res.status_code} on turn {i}: {data}")
        conversation_id = data.get("conversation_id") or conversation_id
        intent = data.get("intent")
        msg = data.get("message") or ""
        print(f"TURN {i} intent={intent} chars={len(msg)}")
        print(" Q:", q)
        print(" A:", msg.replace("\n", " ")[:280])
        transcript.append({"q": q, "intent": intent, "a": msg})
        if intent != "CASE_CONVERSATION":
            raise SystemExit(f"Turn {i} not CASE_CONVERSATION: {intent}")

    with Session() as session:
        after = _snap(session, case_id)
    print("after", after)
    if after != before:
        raise SystemExit(f"mutation detected: {before} -> {after}")

    a7 = transcript[6]["a"]
    if not any(k in a7 for k in ("风险", "甲方", "富茂", "起诉", "方案")):
        raise SystemExit(f"follow-up weak: {a7[:200]}")

    res = client.post(
        f"/cases/{case_id}/agent/messages",
        json={"message": "确认事实1", "conversation_id": conversation_id},
    )
    data = res.json()
    print("ACTION intent", data.get("intent"), (data.get("message") or "")[:160])
    if data.get("intent") != "CONFIRM_FACT":
        raise SystemExit(f"expected CONFIRM_FACT got {data.get('intent')}")

    with Session() as session:
        final = _snap(session, case_id)
    if final["decisions"] < before["decisions"] + 1:
        raise SystemExit("confirm did not create HumanDecision")

    OUT.write_text(
        json.dumps(
            {
                "case_id": str(case_id),
                "turns": len(transcript),
                "ok": True,
                "follow_up_ok": True,
                "mutation_ok": True,
                "action_ok": True,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print("LIVE_CONVERSATION_PASS", case_id)


if __name__ == "__main__":
    main()
