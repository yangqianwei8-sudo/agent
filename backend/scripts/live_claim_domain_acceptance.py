"""V2-P3 Claim Domain — live acceptance."""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

os.environ.setdefault("LLM_MODE", "deterministic")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.application.claim_view import ClaimViewService  # noqa: E402
from backend.domain.errors import ValidationError  # noqa: E402
from backend.domain.services import DomainService  # noqa: E402
from backend.infrastructure.db import get_session_factory  # noqa: E402


def _seed(svc: DomainService, case_id: uuid.UUID, actor_id: uuid.UUID):
    text = "2025年3月1日双方签订设计咨询合同，服务费100000元。"
    material = svc.register_material(
        case_id=case_id,
        filename="contract.pdf",
        mime="application/pdf",
        byte_size=len(text),
        content_hash=f"h-{uuid.uuid4().hex[:8]}",
        storage_key=f"k-{uuid.uuid4().hex[:8]}",
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
        page=1,
    )
    item = svc.create_evidence_item(
        case_id=case_id,
        number="1",
        title="合同",
        category="CONTRACT",
        summary=text[:100],
        source_span_ids=[span.id],
        actor_id=actor_id,
    )
    svc.accept_evidence(item.id, actor_id=actor_id)
    item = svc.repo.get_current_evidence(item.id)
    assert item
    fact = svc.propose_fact(
        case_id=case_id,
        statement="双方于2025年3月1日签订合同，约定服务费100000元。",
        importance="CORE",
        evidence_links=[
            {
                "evidence_item_id": item.id,
                "evidence_item_version": item.version,
                "link_role": "PROVES",
            }
        ],
        actor_id=actor_id,
    )
    svc.confirm_fact(fact.fact_key, actor_id=actor_id)
    fact = svc.repo.get_current_fact(fact.fact_key)
    assert fact
    issue = svc.propose_issue(
        case_id=case_id, statement="被告是否应支付剩余服务费？"
    )
    svc.confirm_issue(issue.issue_key, actor_id=actor_id)
    issue = svc.repo.get_current_issue(issue.issue_key)
    assert issue
    return item, fact, issue


def main() -> None:
    Session = get_session_factory()
    with Session() as session:
        owner = uuid.uuid4()
        actor = uuid.uuid4()
        svc = DomainService(session)
        case = svc.create_case(title="Claim Live", owner_user_id=owner)
        _item, fact, issue = _seed(svc, case.id, actor)

        candidate = svc.propose_claim(
            case_id=case.id,
            claim_type="PAYMENT",
            title="支付剩余服务费",
            statement="请求判令被告支付剩余服务费100000元",
            amount=100000.0,
            currency="CNY",
        )
        view = ClaimViewService(session).build(case.id)
        assert view.candidate_count == 1
        assert view.items[0].amount_is_suggested is True

        confirmed = svc.confirm_claim(candidate.claim_key, actor_id=actor)
        svc.link_issue_to_claim(
            case_id=case.id,
            claim_key=confirmed.claim_key,
            claim_version=confirmed.version,
            issue_key=issue.issue_key,
            issue_version=issue.version,
            role="BASIS",
            actor_id=actor,
        )
        svc.link_fact_to_claim(
            case_id=case.id,
            claim_key=confirmed.claim_key,
            claim_version=confirmed.version,
            fact_key=fact.fact_key,
            fact_version=fact.version,
            role="AMOUNT_BASIS",
            actor_id=actor,
        )
        item_view = ClaimViewService(session).build(case.id).items[0]
        assert len(item_view.basis_issues) == 1
        assert len(item_view.amount_basis_facts) == 1
        assert not item_view.amount_is_suggested

        v2 = svc.amend_claim(
            confirmed.claim_key,
            statement="请求判令被告支付剩余设计咨询费100000元",
            actor_id=actor,
        )
        assert v2.version == 2
        v1 = svc.repo.get_relief_claim_version(confirmed.claim_key, 1)
        assert v1 and v1.status == "SUPERSEDED"

        other = svc.create_case(title="Other", owner_user_id=owner)
        _o_item, other_fact, _ = _seed(svc, other.id, actor)
        try:
            svc.link_fact_to_claim(
                case_id=case.id,
                claim_key=v2.claim_key,
                claim_version=v2.version,
                fact_key=other_fact.fact_key,
                fact_version=other_fact.version,
                role="BASIS",
                actor_id=actor,
            )
            raise AssertionError("cross-case should fail")
        except ValidationError:
            pass

        hist = ClaimViewService(session).build(case.id, include_history=True)
        assert len(hist.items) == 2

        print("V2-P3 Claim Domain live acceptance: PASS")
        print(f"case_id={case.id} claim_key={v2.claim_key}")


if __name__ == "__main__":
    main()
