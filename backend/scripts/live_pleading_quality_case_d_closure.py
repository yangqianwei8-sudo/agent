"""Final Closure — Case D: one CaseMaterial, multiple EvidenceItems → one directory row.

Run:
  python backend/scripts/live_pleading_quality_case_d_closure.py

Output:
  pleading_quality_case_d_closure.md
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.chdir(ROOT)

from sqlalchemy import select  # noqa: E402

from backend.application.pleading_readiness import PleadingReadinessService  # noqa: E402
from backend.application.pleading_writer import PleadingWriterService  # noqa: E402
from backend.domain.services import DomainService  # noqa: E402
from backend.infrastructure.config import get_settings  # noqa: E402
from backend.infrastructure.db import get_session_factory  # noqa: E402
from backend.llm.factory import build_pleading_writer_engine  # noqa: E402
from backend.models import CaseMaterial, EvidenceItemSpan, SourceSpan  # noqa: E402
from backend.schemas.pleading_readiness import ReadinessStatus  # noqa: E402


CONTRACT_FILENAME = "设计优化咨询服务合同.pdf"
CONTRACT_TEXT = (
    "设计优化咨询服务合同\n"
    "甲方：中梁地产集团有限公司\n"
    "乙方：四川维海科技有限公司\n"
    "一、服务范围：设计优化咨询及成果提交。\n"
    "二、收费标准：按经确认优化金额的8%计取，上限30万元。\n"
    "三、付款条件：成果提交并签收后15日内支付。\n"
)

SPAN_PARTS = [
    ("合同主体", "甲方：中梁地产集团有限公司\n乙方：四川维海科技有限公司\n"),
    ("服务范围", "一、服务范围：设计优化咨询及成果提交。\n"),
    ("收费规则", "二、收费标准：按经确认优化金额的8%计取，上限30万元。\n"),
    ("付款条件", "三、付款条件：成果提交并签收后15日内支付。\n"),
]

EVIDENCE_DEFS = [
    ("1", "合同主体摘录", "证明合同签约主体"),
    ("2", "服务范围摘录", "证明服务内容约定"),
    ("3", "收费规则摘录", "证明收费标准"),
    ("4", "付款条件摘录", "证明付款条件"),
]


def _seed_case_d(session, *, owner_id: uuid.UUID, actor_id: uuid.UUID):
    svc = DomainService(session)
    case = svc.create_case(title="Case D 单合同多证据", owner_user_id=owner_id)
    plaintiff = svc.create_party(
        case_id=case.id,
        role="PLAINTIFF",
        name="四川维海科技有限公司",
        party_type="ORG",
        actor_id=actor_id,
    )
    defendant = svc.create_party(
        case_id=case.id,
        role="DEFENDANT",
        name="中梁地产集团有限公司",
        party_type="ORG",
        actor_id=actor_id,
    )
    svc.confirm_party(plaintiff.party_key, actor_id=actor_id)
    svc.confirm_party(defendant.party_key, actor_id=actor_id)

    material = svc.register_material(
        case_id=case.id,
        filename=CONTRACT_FILENAME,
        mime="application/pdf",
        byte_size=len(CONTRACT_TEXT),
        content_hash=f"h-{uuid.uuid4().hex[:10]}",
        storage_key=f"k-{uuid.uuid4().hex[:10]}",
        created_by=actor_id,
    )
    ec = svc.create_extracted_content(
        material_id=material.id,
        extraction_method="pdfplumber",
        extraction_version="v1",
        full_text=CONTRACT_TEXT,
        actor_id=actor_id,
    )

    spans = []
    offset = 0
    for _label, part in SPAN_PARTS:
        start = CONTRACT_TEXT.find(part, offset)
        if start < 0:
            start = offset
        end = start + len(part)
        spans.append(
            svc.create_source_span(
                material_id=material.id,
                extracted_content_id=ec.id,
                character_start=start,
                character_end=end,
                quote=part,
                extraction_method="pdfplumber",
                extraction_version="v1",
            )
        )
        offset = end

    evidences = []
    for (num, title, _summary), span in zip(EVIDENCE_DEFS, spans, strict=True):
        ev = svc.create_evidence_item(
            case_id=case.id,
            number=num,
            title=title,
            category="CONTRACT",
            summary=span.quote[:80],
            source_span_ids=[span.id],
            actor_id=actor_id,
        )
        svc.accept_evidence(ev.id, actor_id=actor_id)
        ev = svc.repo.get_current_evidence(ev.id)
        assert ev is not None
        evidences.append(ev)

    extra_facts = [
        "2024年6月20日，原告向被告提交优化成果，被告签收确认。",
        "经确认优化金额为200万元，按8%计算应付服务费160000元。",
        "被告已支付60000元，尚欠100000元未付。",
        "付款条件已成就，债务已到期。",
    ]
    facts = []
    for span, ev in zip(spans, evidences, strict=True):
        f = svc.propose_fact(
            case_id=case.id,
            statement=f"合同载明：{span.quote.strip()}",
            evidence_links=[
                {
                    "evidence_item_id": ev.id,
                    "evidence_item_version": ev.version,
                }
            ],
            actor_id=actor_id,
        )
        svc.confirm_fact(f.fact_key, actor_id=actor_id)
        cur = svc.repo.get_current_fact(f.fact_key)
        assert cur is not None
        facts.append(cur)

    for text in extra_facts:
        material2 = svc.register_material(
            case_id=case.id,
            filename=f"supplement-{uuid.uuid4().hex[:6]}.pdf",
            mime="application/pdf",
            byte_size=len(text),
            content_hash=f"h-{uuid.uuid4().hex[:10]}",
            storage_key=f"k-{uuid.uuid4().hex[:10]}",
            created_by=actor_id,
        )
        ec2 = svc.create_extracted_content(
            material_id=material2.id,
            extraction_method="pdfplumber",
            extraction_version="v1",
            full_text=text,
            actor_id=actor_id,
        )
        span2 = svc.create_source_span(
            material_id=material2.id,
            extracted_content_id=ec2.id,
            character_start=0,
            character_end=len(text),
            quote=text,
            extraction_method="pdfplumber",
            extraction_version="v1",
        )
        ev2 = svc.create_evidence_item(
            case_id=case.id,
            number=str(len(evidences) + len(facts) + 1),
            title="补充证据",
            category="PERFORMANCE",
            summary=text,
            source_span_ids=[span2.id],
            actor_id=actor_id,
        )
        svc.accept_evidence(ev2.id, actor_id=actor_id)
        ev2 = svc.repo.get_current_evidence(ev2.id)
        assert ev2 is not None
        evidences.append(ev2)
        f2 = svc.propose_fact(
            case_id=case.id,
            statement=text,
            evidence_links=[
                {
                    "evidence_item_id": ev2.id,
                    "evidence_item_version": ev2.version,
                }
            ],
            actor_id=actor_id,
        )
        svc.confirm_fact(f2.fact_key, actor_id=actor_id)
        cur = svc.repo.get_current_fact(f2.fact_key)
        assert cur is not None
        facts.append(cur)

    claim = svc.create_claim_direction(_legacy_compat=True,
        case_id=case.id,
        payload={
            "overall_strategy": "请求支付剩余服务费",
            "claims": [
                {
                    "claim_type": "PAYMENT",
                    "description": "剩余服务费",
                    "amount": 100000,
                    "currency": "CNY",
                    "supporting_fact_ids": [str(f.fact_key) for f in facts],
                }
            ],
        },
        actor_id=actor_id,
    )
    claim = svc.confirm_claim_direction(claim.claim_direction_key, actor_id=actor_id)

    contract_evidences = evidences[:4]
    return (
        case,
        material,
        spans,
        contract_evidences,
        [plaintiff.party_key, defendant.party_key],
        facts,
        evidences,
        claim,
    )


def _provenance_report(session, material_id: uuid.UUID, evidence_ids: list) -> dict:
    out = {"material_id": str(material_id), "items": []}
    material = session.get(CaseMaterial, material_id)
    out["material_filename"] = material.filename if material else None
    for eid in evidence_ids:
        ev = session.scalars(select(EvidenceItemSpan).where(EvidenceItemSpan.evidence_item_id == eid)).all()
        span_ids = [link.source_span_id for link in ev]
        spans = [session.get(SourceSpan, sid) for sid in span_ids]
        out["items"].append(
            {
                "evidence_item_id": str(eid),
                "source_span_ids": [str(s.id) for s in spans if s],
                "material_ids": list({str(s.material_id) for s in spans if s}),
                "quotes": [s.quote[:60] for s in spans if s],
            }
        )
    return out


def main() -> int:
    settings = get_settings()
    session = get_session_factory()()
    actor_id = uuid.uuid4()
    owner_id = actor_id
    out_path = ROOT / "pleading_quality_case_d_closure.md"

    try:
        case, material, spans, contract_evs, parties, facts, evidences, claim = _seed_case_d(
            session, owner_id=owner_id, actor_id=actor_id
        )
        session.commit()

        readiness = PleadingReadinessService(session).evaluate(case.id)
        engine = build_pleading_writer_engine(settings=settings)
        result = PleadingWriterService(session, engine=engine).write(
            case_id=case.id,
            claim_direction_ref={
                "claim_direction_key": str(claim.claim_direction_key),
                "claim_direction_version": claim.version,
            },
            confirmed_fact_refs=[
                {"fact_key": str(f.fact_key), "fact_version": f.version} for f in facts
            ],
            accepted_evidence_refs=[
                {"evidence_item_id": str(e.id), "evidence_item_version": e.version}
                for e in evidences
            ],
            confirmed_party_keys=parties,
            actor_id=actor_id,
        )
        session.commit()

        body = result.draft.body_structured_json if result.draft else {}
        ev_dir = body.get("evidence_directory") or []
        contract_rows = [
            r for r in ev_dir
            if r.get("material_id") == str(material.id)
            or CONTRACT_FILENAME.replace(".pdf", "") in str(r.get("title", ""))
            or CONTRACT_FILENAME in str(r.get("material_filename", ""))
        ]
        merged = contract_rows[0].get("merged_evidence_refs") or [] if contract_rows else []

        prov = _provenance_report(
            session, material.id, [e.id for e in contract_evs]
        )

        report = {
            "generated_at": datetime.now(UTC).isoformat(),
            "llm_mode": settings.llm_mode,
            "readiness": readiness.status.value,
            "material_count_contract": 1,
            "evidence_item_count_contract": len(contract_evs),
            "source_span_count_contract": len(spans),
            "evidence_directory_total_rows": len(ev_dir),
            "evidence_directory_contract_rows": len(contract_rows),
            "merged_evidence_refs_count": len(merged),
            "validator_passed": (body.get("validation") or {}).get("passed"),
            "repair_count": (body.get("validation") or {}).get("repair_count", 0),
            "evidence_directory_text": body.get("evidence_directory_text", ""),
            "full_text": body.get("full_text", ""),
            "provenance": prov,
            "merged_evidence_refs": merged,
        }

        md = _format_md(report, readiness)
        out_path.write_text(md, encoding="utf-8")
        print(f"Wrote {out_path}")
        print(json.dumps({k: report[k] for k in report if k not in ("full_text", "evidence_directory_text")}, ensure_ascii=False, indent=2))

        if readiness.status != ReadinessStatus.READY:
            return 1
        if len(contract_rows) != 1:
            return 1
        if len(merged) != 4:
            return 1
        if not report["validator_passed"]:
            return 1
        return 0
    finally:
        session.close()


def _format_md(report: dict, readiness) -> str:
    blockers = [i.code for i in readiness.blocking_issues] if readiness else []
    return f"""# Case D — One Contract / Multiple EvidenceItems Closure

Generated: {report["generated_at"]}
LLM_MODE: {report["llm_mode"]}

## Counts

| Metric | Value |
|--------|-------|
| CaseMaterial (contract) | {report["material_count_contract"]} |
| EvidenceItem (contract) | {report["evidence_item_count_contract"]} |
| SourceSpan (contract) | {report["source_span_count_contract"]} |
| evidence_directory total rows | {report["evidence_directory_total_rows"]} |
| evidence_directory contract rows | {report["evidence_directory_contract_rows"]} |
| merged_evidence_refs | {report["merged_evidence_refs_count"]} |

## Readiness / Validator

- Readiness: `{report["readiness"]}` {f"(blockers: {blockers})" if blockers else ""}
- Validator passed: `{report["validator_passed"]}`
- repair_count: `{report["repair_count"]}`

## Evidence Directory (contract)

```
{report["evidence_directory_text"]}
```

## Provenance Preservation

```json
{json.dumps(report["provenance"], ensure_ascii=False, indent=2)}
```

## merged_evidence_refs

```json
{json.dumps(report["merged_evidence_refs"], ensure_ascii=False, indent=2)}
```

## Full Text

```
{report["full_text"]}
```
"""


if __name__ == "__main__":
    raise SystemExit(main())
