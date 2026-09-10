"""V2-P4 live acceptance — PleadingStructuredInput production path."""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

os.environ.setdefault("LLM_MODE", "deterministic")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.application.pleading_input_production import (  # noqa: E402
    PleadingStructuredInputProductionBuilder,
)
from backend.application.pleading_writer import PleadingWriterService  # noqa: E402
from backend.domain.services import DomainService  # noqa: E402
from backend.infrastructure.db import get_session_factory  # noqa: E402
from backend.schemas.case_analyst import EvidenceRef  # noqa: E402
from backend.schemas.claim_direction_proposal import FactRef  # noqa: E402

def main() -> int:
    factory = get_session_factory()
    with factory() as session:
        svc = DomainService(session)
        actor = uuid.uuid4()
        case = svc.create_case(title="Live P4", owner_user_id=actor)

        plaintiff = svc.create_party(
            case_id=case.id, role="PLAINTIFF", name="原告公司", party_type="ORG", actor_id=actor
        )
        defendant = svc.create_party(
            case_id=case.id, role="DEFENDANT", name="被告公司", party_type="ORG", actor_id=actor
        )
        svc.confirm_party(plaintiff.party_key, actor_id=actor)
        svc.confirm_party(defendant.party_key, actor_id=actor)

        text = "2025年3月1日签订设计合同，服务费1000000元，被告已支付300000元，尚欠700000元。"
        material = svc.register_material(
            case_id=case.id,
            filename="contract.pdf",
            mime="application/pdf",
            byte_size=len(text),
            content_hash=f"h-{uuid.uuid4().hex[:8]}",
            storage_key=f"k-{uuid.uuid4().hex[:8]}",
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
        item = svc.create_evidence_item(
            case_id=case.id,
            number="1",
            title="设计合同",
            category="CONTRACT",
            summary=text,
            source_span_ids=[span.id],
            actor_id=actor,
        )
        svc.accept_evidence(item.id, actor_id=actor)
        item = svc.repo.get_current_evidence(item.id)
        assert item

        confirmed_facts: list = []
        for stmt in (
            "合同约定服务费总价为1000000元。",
            "被告已支付300000元。",
            "原告已向被告交付设计成果并经签收。",
            "合同约定成果提交后付款，付款条件已成就。",
            "尚欠服务费700000元已到期。",
            "合同约定由被告住所地人民法院管辖。",
        ):
            proposed = svc.propose_fact(
                case_id=case.id,
                statement=stmt,
                evidence_links=[
                    {
                        "evidence_item_id": item.id,
                        "evidence_item_version": item.version,
                    }
                ],
                importance="CORE",
                actor_id=actor,
            )
            confirmed_facts.append(svc.confirm_fact(proposed.fact_key, actor_id=actor))
        amount_fact = confirmed_facts[-2]

        issue = svc.propose_issue(case_id=case.id, statement="被告是否应支付剩余服务费")
        issue = svc.confirm_issue(issue.issue_key, actor_id=actor)
        claim = svc.propose_claim(
            case_id=case.id,
            claim_type="PAYMENT",
            title="支付服务费",
            statement="请求被告支付剩余服务费700000元",
            amount=700000.0,
            currency="CNY",
        )
        claim = svc.confirm_claim(claim.claim_key, actor_id=actor)
        svc.link_issue_to_claim(
            case_id=case.id,
            claim_key=claim.claim_key,
            claim_version=claim.version,
            issue_key=issue.issue_key,
            issue_version=issue.version,
            role="BASIS",
            actor_id=actor,
        )
        svc.link_fact_to_claim(
            case_id=case.id,
            claim_key=claim.claim_key,
            claim_version=claim.version,
            fact_key=amount_fact.fact_key,
            fact_version=amount_fact.version,
            role="AMOUNT_BASIS",
            actor_id=actor,
        )
        svc.link_fact_to_issue(
            case_id=case.id,
            issue_key=issue.issue_key,
            issue_version=issue.version,
            fact_key=amount_fact.fact_key,
            fact_version=amount_fact.version,
            role="SUPPORT",
            actor_id=actor,
        )

        builder = PleadingStructuredInputProductionBuilder(session)
        parties = builder._load_confirmed_parties(case.id)
        facts = builder._load_confirmed_facts_for_case(case.id)
        evidence = builder._load_accepted_evidence_for_case(case.id)
        inp = builder.build(
            case_id=case.id, parties=parties, facts=facts, evidence=evidence
        )
        assert inp.claims[0].claim_version == claim.version
        assert inp.issues[0].issue_version == issue.version
        assert inp.facts[0].fact_version == amount_fact.version
        assert inp.evidence[0].source_spans
        assert inp.snapshot_meta.confirmation_set_hash
        hash1 = inp.snapshot_meta.confirmation_set_hash

        writer = PleadingWriterService(session)
        result = writer.write(
            case_id=case.id,
            claim_direction_ref=None,
            confirmed_fact_refs=[
                FactRef(fact_key=f.fact_key, fact_version=f.fact_version)
                for f in facts
            ],
            accepted_evidence_refs=[
                EvidenceRef(
                    evidence_item_id=item.id, evidence_item_version=item.version
                )
            ],
            confirmed_party_keys=[plaintiff.party_key, defendant.party_key],
            actor_id=actor,
        )
        assert result.draft
        snap = result.draft.body_structured_json["structured_input_snapshot"]
        assert snap["snapshot_meta"]["claim_source"] == "CLAIM_DOMAIN"

        svc.amend_claim(claim.claim_key, statement="修订诉请", actor_id=actor)
        inp2 = builder.build(
            case_id=case.id, parties=parties, facts=facts, evidence=evidence
        )
        assert inp2.snapshot_meta.confirmation_set_hash != hash1

        session.commit()
        print("PASS live_pleading_structured_input_acceptance")
        print(f"case_id={case.id} hash_before={hash1[:16]}...")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
