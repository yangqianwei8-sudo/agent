"""V2-P2 Issue Matrix — live acceptance against real database."""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

os.environ.setdefault("LLM_MODE", "deterministic")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.application.issue_matrix import IssueMatrixService  # noqa: E402
from backend.domain.services import DomainService  # noqa: E402
from backend.infrastructure.db import get_session_factory  # noqa: E402


def _seed(svc: DomainService, case_id: uuid.UUID, actor_id: uuid.UUID):
    text = "2025年3月1日双方签订设计咨询合同。"
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
    fact_support = svc.propose_fact(
        case_id=case_id,
        statement="双方于2025年3月1日签订合同。",
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
    svc.confirm_fact(fact_support.fact_key, actor_id=actor_id)
    fact_support = svc.repo.get_current_fact(fact_support.fact_key)
    assert fact_support
    fact_adverse = svc.propose_fact(
        case_id=case_id,
        statement="对方主张成果尚未验收。",
        importance="SUPPORTING",
        evidence_links=[
            {
                "evidence_item_id": item.id,
                "evidence_item_version": item.version,
                "link_role": "CONTEXT",
            }
        ],
        actor_id=actor_id,
    )
    svc.confirm_fact(fact_adverse.fact_key, actor_id=actor_id)
    fact_adverse = svc.repo.get_current_fact(fact_adverse.fact_key)
    assert fact_adverse
    return item, fact_support, fact_adverse


def main() -> None:
    Session = get_session_factory()
    with Session() as session:
        owner = uuid.uuid4()
        actor = uuid.uuid4()
        svc = DomainService(session)
        case = svc.create_case(title="Issue Matrix Live", owner_user_id=owner)
        item, fact_s, fact_a = _seed(svc, case.id, actor)

        candidate = svc.propose_issue(
            case_id=case.id,
            statement="现有材料是否足以证明成果已交付？",
            analyst_run_id=uuid.uuid4(),
        )
        matrix = IssueMatrixService(session).build(case.id)
        assert matrix.candidate_issue_count == 1
        assert matrix.items[0].lawyer_confirmation_state == "AI_CANDIDATE"
        assert matrix.items[0].fact_gaps

        confirmed = svc.confirm_issue(candidate.issue_key, actor_id=actor)
        svc.link_fact_to_issue(
            case_id=case.id,
            issue_key=confirmed.issue_key,
            issue_version=confirmed.version,
            fact_key=fact_s.fact_key,
            fact_version=fact_s.version,
            role="SUPPORT",
            actor_id=actor,
        )
        svc.link_fact_to_issue(
            case_id=case.id,
            issue_key=confirmed.issue_key,
            issue_version=confirmed.version,
            fact_key=fact_a.fact_key,
            fact_version=fact_a.version,
            role="ADVERSE",
            actor_id=actor,
        )
        svc.link_evidence_to_issue(
            case_id=case.id,
            issue_key=confirmed.issue_key,
            issue_version=confirmed.version,
            evidence_item_id=item.id,
            evidence_item_version=item.version,
            role="CONTEXT",
            explanation="与合同签署相关",
            actor_id=actor,
        )
        matrix = IssueMatrixService(session).build(case.id).items[0]
        assert len(matrix.supporting_facts) == 1
        assert len(matrix.adverse_facts) == 1
        assert len(matrix.context_evidence) == 1
        assert not matrix.fact_gaps

        v2 = svc.amend_issue(
            confirmed.issue_key,
            new_statement="交付事实是否已有充分书面证据支撑？",
            actor_id=actor,
        )
        matrix_v2 = IssueMatrixService(session).build(case.id).items[0]
        assert matrix_v2.issue_version == 2
        assert len(matrix_v2.supporting_facts) == 1
        v1 = svc.repo.get_issue_version(confirmed.issue_key, 1)
        assert v1 and v1.status == "SUPERSEDED"

        print("V2-P2 Issue Matrix live acceptance: PASS")
        print(f"case_id={case.id}")
        print(f"issue_key={v2.issue_key} version={v2.version}")


if __name__ == "__main__":
    main()
