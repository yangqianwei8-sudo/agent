"""Live DeepSeek acceptance — Pleading Quality V1 (3 cases).

Run:
  python backend/scripts/live_pleading_quality_acceptance.py

Output:
  pleading_quality_live_acceptance.md
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.chdir(ROOT)

from backend.application.pleading_readiness import PleadingReadinessService  # noqa: E402
from backend.application.pleading_writer import PleadingWriterService  # noqa: E402
from backend.domain.services import DomainService  # noqa: E402
from backend.infrastructure.config import get_settings  # noqa: E402
from backend.infrastructure.db import get_session_factory  # noqa: E402
from backend.llm.factory import build_pleading_writer_engine  # noqa: E402
from backend.schemas.pleading_readiness import ReadinessStatus  # noqa: E402
from backend.tests.integration.test_pleading_writer import (  # noqa: E402
    _seed_writer_world,
    _writer_args,
)
from backend.tests.unit.test_pleading_quality import (  # noqa: E402
    _seed_fumao_ready,
)


def _seed_case_b(session, *, owner_id: uuid.UUID, actor_id: uuid.UUID):
    """Contract party != defendant with liability bridge — simplified payment."""
    svc = DomainService(session)
    case = svc.create_case(title="Case B 责任桥梁", owner_user_id=owner_id)
    plaintiff = svc.create_party(
        case_id=case.id,
        role="PLAINTIFF",
        name="成都智图设计有限公司",
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

    statements = [
        "2024年3月，甲方四川富茂置业有限公司与乙方成都智图设计有限公司签订设计优化合同。",
        "合同约定设计优化服务费总价500000元。",
        "中梁地产集团有限公司出具债务加入函，确认加入债务并承担全部500000元付款义务。",
        "2024年5月10日，成都智图设计有限公司向四川富茂置业有限公司提交设计成果，对方签收。",
        "合同约定成果提交并签收后7日内付款，该付款条件已成就。",
        "中梁地产集团有限公司已支付100000元，尚欠400000元未付。",
        "付款义务已到期，应于2024年6月30日前结清。",
    ]
    facts, evidences = [], []
    for i, text in enumerate(statements):
        material = svc.register_material(
            case_id=case.id,
            filename=f"b{i}.pdf",
            mime="application/pdf",
            byte_size=len(text),
            content_hash=f"h-{uuid.uuid4().hex[:10]}",
            storage_key=f"k-{uuid.uuid4().hex[:10]}",
            created_by=actor_id,
        )
        ec = svc.create_extracted_content(
            material_id=material.id,
            extraction_method="pdfplumber",
            extraction_version="v1",
            full_text=text,
            actor_id=actor_id,
        )
        span = svc.create_source_span(
            material_id=material.id,
            extracted_content_id=ec.id,
            character_start=0,
            character_end=len(text),
            quote=text,
            extraction_method="pdfplumber",
            extraction_version="v1",
        )
        evidence = svc.create_evidence_item(
            case_id=case.id,
            number=str(i + 1),
            title=f"证据{i + 1}",
            category="CONTRACT",
            summary=text,
            source_span_ids=[span.id],
            actor_id=actor_id,
        )
        svc.accept_evidence(evidence.id, actor_id=actor_id)
        evidence = svc.repo.get_current_evidence(evidence.id)
        assert evidence is not None
        fact = svc.propose_fact(
            case_id=case.id,
            statement=text,
            evidence_links=[
                {
                    "evidence_item_id": evidence.id,
                    "evidence_item_version": evidence.version,
                }
            ],
            actor_id=actor_id,
        )
        svc.confirm_fact(fact.fact_key, actor_id=actor_id)
        fact = svc.repo.get_current_fact(fact.fact_key)
        assert fact is not None
        facts.append(fact)
        evidences.append(evidence)

    claim = svc.create_claim_direction(
        case_id=case.id,
        payload={
            "overall_strategy": "请求中梁支付剩余设计费",
            "claims": [
                {
                    "claim_type": "PAYMENT",
                    "description": "剩余设计优化服务费",
                    "amount": 400000,
                    "currency": "CNY",
                    "supporting_fact_ids": [str(f.fact_key) for f in facts],
                }
            ],
        },
        actor_id=actor_id,
    )
    claim = svc.confirm_claim_direction(claim.claim_direction_key, actor_id=actor_id)
    return case, [plaintiff.party_key, defendant.party_key], facts, evidences, claim


def _run_case(
    session,
    *,
    label: str,
    case,
    parties,
    facts,
    evidences,
    claim,
    actor_id: uuid.UUID,
    engine,
) -> dict:
    readiness = PleadingReadinessService(session).evaluate(case.id)
    writer = PleadingWriterService(session, engine=engine)
    result = writer.write(
        **_writer_args(case, parties, facts, evidences, claim),
        actor_id=actor_id,
    )
    body = result.draft.body_structured_json if result.draft else {}
    full_text = body.get("full_text", "")
    validation = body.get("validation") or {}
    return {
        "label": label,
        "readiness": readiness.status.value,
        "blocking": [i.code for i in readiness.blocking_issues],
        "claims": body.get("sections", {}).get("claims", ""),
        "facts": body.get("sections", {}).get("facts_and_reasons", ""),
        "evidence": body.get("evidence_directory_text") or body.get("sections", {}).get("evidence", ""),
        "court": body.get("sections", {}).get("court", ""),
        "signature": body.get("sections", {}).get("signature", ""),
        "validator_passed": validation.get("passed"),
        "repair_count": validation.get("repair_count", 0),
        "full_text": full_text,
        "warnings": result.warnings,
    }


def main() -> int:
    settings = get_settings()
    print(f"LLM_MODE={settings.llm_mode} model={settings.llm_model}")
    session = get_session_factory()()
    actor_id = uuid.uuid4()
    owner_id = actor_id
    engine = build_pleading_writer_engine(settings=settings)
    out_path = ROOT / "pleading_quality_live_acceptance.md"
    sections: list[str] = []

    try:
        # Case A — standard payment dispute
        _, case_a, parties_a, facts_a, ev_a, claim_a = _seed_writer_world(
            session, owner_id=owner_id, actor_id=actor_id
        )
        session.commit()
        sections.append(_format_case(_run_case(
            session,
            label="Case A — 普通设计优化服务合同付款纠纷",
            case=case_a,
            parties=parties_a,
            facts=facts_a,
            evidences=ev_a,
            claim=claim_a,
            actor_id=actor_id,
            engine=engine,
        )))

        # Case B — liability bridge
        case_b, parties_b, facts_b, ev_b, claim_b = _seed_case_b(
            session, owner_id=owner_id, actor_id=actor_id
        )
        session.commit()
        sections.append(_format_case(_run_case(
            session,
            label="Case B — 合同主体≠被告，有 confirmed liability bridge",
            case=case_b,
            parties=parties_b,
            facts=facts_b,
            evidences=ev_b,
            claim=claim_b,
            actor_id=actor_id,
            engine=engine,
        )))

        # Case C — full amount chain (富茂/中梁 READY)
        case_c, parties_c, facts_c, ev_c, claim_c = _seed_fumao_ready(
            session, owner_id=owner_id, actor_id=actor_id
        )
        session.commit()
        r_c = PleadingReadinessService(session).evaluate(case_c.id)
        assert r_c.status == ReadinessStatus.READY, [i.code for i in r_c.blocking_issues]
        sections.append(_format_case(_run_case(
            session,
            label="Case C — 完整金额链/到期/催告（富茂→中梁）",
            case=case_c,
            parties=parties_c,
            facts=facts_c,
            evidences=ev_c,
            claim=claim_c,
            actor_id=actor_id,
            engine=engine,
        )))

        header = (
            f"# Pleading Quality V1 — Live Acceptance\n\n"
            f"Generated: {datetime.now(UTC).isoformat()}\n"
            f"LLM_MODE={settings.llm_mode} model={settings.llm_model}\n\n"
        )
        out_path.write_text(header + "\n---\n\n".join(sections), encoding="utf-8")
        print(f"Wrote {out_path}")
        return 0
    finally:
        session.close()


def _format_case(data: dict) -> str:
    return (
        f"## {data['label']}\n\n"
        f"- Readiness: `{data['readiness']}`"
        f"{(' blockers=' + str(data['blocking'])) if data['blocking'] else ''}\n"
        f"- Validator passed: `{data['validator_passed']}`\n"
        f"- repair_count: `{data['repair_count']}`\n\n"
        f"### 诉讼请求\n\n```\n{data['claims']}\n```\n\n"
        f"### 事实与理由\n\n```\n{data['facts']}\n```\n\n"
        f"### 证据目录\n\n```\n{data['evidence']}\n```\n\n"
        f"### 法院/落款\n\n```\n{data['court']}\n\n{data['signature']}\n```\n\n"
        f"### 全文\n\n```\n{data['full_text']}\n```\n"
    )


if __name__ == "__main__":
    raise SystemExit(main())
