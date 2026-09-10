"""V2-P1 Issue Domain — live acceptance against real database."""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

os.environ.setdefault("LLM_MODE", "deterministic")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import func, select  # noqa: E402

from backend.domain.errors import ValidationError  # noqa: E402
from backend.domain.services import DomainService  # noqa: E402
from backend.infrastructure.db import get_session_factory  # noqa: E402
from backend.models import AuditLog, HumanDecision, Issue  # noqa: E402


def _seed_evidence(svc: DomainService, case_id: uuid.UUID, actor_id: uuid.UUID):
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
    assert item is not None
    fact = svc.propose_fact(
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
    svc.confirm_fact(fact.fact_key, actor_id=actor_id)
    fact = svc.repo.get_current_fact(fact.fact_key)
    assert fact is not None
    return item, fact


def main() -> None:
    Session = get_session_factory()
    with Session() as session:
        owner = uuid.uuid4()
        actor = uuid.uuid4()
        svc = DomainService(session)

        case = svc.create_case(title="Issue Domain Live", owner_user_id=owner)
        item, fact = _seed_evidence(svc, case.id, actor)

        candidate = svc.propose_issue(
            case_id=case.id,
            statement="现有材料是否足以证明成果已交付？",
            analyst_run_id=uuid.uuid4(),
        )
        assert candidate.status == "CANDIDATE"
        assert candidate.source_type == "AI_PROPOSED"

        confirmed = svc.confirm_issue(candidate.issue_key, actor_id=actor)
        assert confirmed.status == "CONFIRMED"

        svc.link_fact_to_issue(
            case_id=case.id,
            issue_key=confirmed.issue_key,
            issue_version=confirmed.version,
            fact_key=fact.fact_key,
            fact_version=fact.version,
            role="SUPPORT",
            actor_id=actor,
        )
        svc.link_evidence_to_issue(
            case_id=case.id,
            issue_key=confirmed.issue_key,
            issue_version=confirmed.version,
            evidence_item_id=item.id,
            evidence_item_version=item.version,
            role="CONTEXT",
            explanation="与合同签署时间相关",
            actor_id=actor,
        )

        v2 = svc.amend_issue(
            confirmed.issue_key,
            new_statement="交付事实是否已有充分书面证据支撑？",
            actor_id=actor,
        )
        v1 = svc.repo.get_issue_version(confirmed.issue_key, 1)
        assert v1 is not None
        assert v1.status == "SUPERSEDED"
        assert v1.is_current is False
        assert v2.is_current is True
        assert v2.version == 2

        other = svc.create_case(title="Other case", owner_user_id=owner)
        _other_item, other_fact = _seed_evidence(svc, other.id, actor)
        try:
            svc.link_fact_to_issue(
                case_id=case.id,
                issue_key=v2.issue_key,
                issue_version=v2.version,
                fact_key=other_fact.fact_key,
                fact_version=other_fact.version,
                role="SUPPORT",
                actor_id=actor,
            )
            raise AssertionError("cross-case link should fail")
        except ValidationError:
            pass

        decisions = session.scalar(
            select(func.count()).select_from(HumanDecision).where(
                HumanDecision.case_id == case.id
            )
        )
        audits = session.scalar(
            select(func.count()).select_from(AuditLog).where(AuditLog.case_id == case.id)
        )
        issues = session.scalars(select(Issue).where(Issue.case_id == case.id)).all()
        assert decisions and decisions >= 2
        assert audits and audits >= 5
        assert len(issues) == 2
        assert sum(1 for i in issues if i.is_current) == 1

        print("V2-P1 Issue Domain live acceptance: PASS")
        print(f"case_id={case.id}")
        print(f"issue_key={v2.issue_key}")
        print(f"versions={sorted(i.version for i in issues)}")


if __name__ == "__main__":
    main()
