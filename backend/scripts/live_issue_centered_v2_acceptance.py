"""Issue-Centered Case Workspace V2 — live acceptance (real PostgreSQL)."""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

os.environ.setdefault("LLM_MODE", "deterministic")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.application.issue_work_product import IssueWorkProductService  # noqa: E402
from backend.application.pleading_readiness import PleadingReadinessService  # noqa: E402
from backend.domain.services import DomainService  # noqa: E402
from backend.infrastructure.db import get_session_factory  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from backend.main import app  # noqa: E402


def _seed_material(svc: DomainService, case_id: uuid.UUID, actor_id: uuid.UUID, text: str):
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
        summary=text[:80],
        source_span_ids=[span.id],
        actor_id=actor_id,
    )
    svc.accept_evidence(item.id, actor_id=actor_id)
    item = svc.repo.get_current_evidence(item.id)
    assert item is not None
    return material, span, item


def main() -> None:
    Session = get_session_factory()
    steps: list[str] = []
    with Session() as session:
        owner = uuid.uuid4()
        actor = uuid.uuid4()
        svc = DomainService(session)

        case = svc.create_case(title="Issue V2 Live", owner_user_id=owner)
        steps.append("1 Case")

        text = "2025年3月1日双方签订设计咨询合同，约定设计费50万元。"
        _, span, item = _seed_material(svc, case.id, actor, text)
        steps.append("2 Material + Evidence accepted")

        fact = svc.propose_fact(
            case_id=case.id,
            statement="双方于2025年3月1日签订设计咨询合同。",
            importance="CORE",
            evidence_links=[
                {
                    "evidence_item_id": item.id,
                    "evidence_item_version": item.version,
                    "link_role": "PROVES",
                    "source_span_id": span.id,
                }
            ],
            actor_id=actor,
        )
        svc.confirm_fact(fact.fact_key, actor_id=actor)
        fact = svc.repo.get_current_fact(fact.fact_key)
        steps.append("4 Fact confirmed")

        candidate = svc.propose_issue(
            case_id=case.id, statement="现有材料是否足以证明成果已交付？"
        )
        issue = svc.confirm_issue(candidate.issue_key, actor_id=actor)
        steps.append("5-6 Issue confirmed")

        pos = svc.propose_position(
            case_id=case.id,
            issue_key=issue.issue_key,
            issue_version=issue.version,
            side="OUR",
            position_type="ASSERTION",
            statement="我方主张成果已按约交付",
        )
        svc.confirm_position(pos.position_key, actor_id=actor)
        ant = svc.propose_position(
            case_id=case.id,
            issue_key=issue.issue_key,
            issue_version=issue.version,
            side="OUR",
            position_type="ANTICIPATED_DEFENSE",
            statement="对方可能抗辩未验收",
        )
        svc.confirm_position(ant.position_key, actor_id=actor)
        steps.append("7-8 Positions")

        pt = svc.propose_proof_task(
            case_id=case.id,
            issue_key=issue.issue_key,
            issue_version=issue.version,
            description="证明成果已交付",
        )
        task = svc.adopt_proof_task(pt.proof_task_key, actor_id=actor)
        svc.link_fact_to_proof_task(
            case_id=case.id,
            proof_task_key=task.proof_task_key,
            proof_task_version=task.version,
            fact_key=fact.fact_key,
            fact_version=fact.version,
            role="SUPPORT",
            actor_id=actor,
        )
        steps.append("9-11 ProofTask + SUPPORT fact")

        adverse_fact = svc.propose_fact(
            case_id=case.id,
            statement="对方声称未收到最终成果。",
            importance="SUPPORTING",
            evidence_links=[
                {
                    "evidence_item_id": item.id,
                    "evidence_item_version": item.version,
                    "link_role": "CONTEXT",
                }
            ],
            actor_id=actor,
        )
        svc.confirm_fact(adverse_fact.fact_key, actor_id=actor)
        adverse_fact = svc.repo.get_current_fact(adverse_fact.fact_key)
        svc.link_fact_to_proof_task(
            case_id=case.id,
            proof_task_key=task.proof_task_key,
            proof_task_version=task.version,
            fact_key=adverse_fact.fact_key,
            fact_version=adverse_fact.version,
            role="ADVERSE",
            actor_id=actor,
        )
        steps.append("12 ADVERSE fact link")

        svc.create_conflict(
            case_id=case.id,
            issue_key=issue.issue_key,
            issue_version=issue.version,
            description="交付时间与对方主张不一致",
            fact_refs=[
                {
                    "fact_key": str(fact.fact_key),
                    "fact_version": fact.version,
                    "role": "SIDE_A",
                },
                {
                    "fact_key": str(adverse_fact.fact_key),
                    "fact_version": adverse_fact.version,
                    "role": "SIDE_B",
                },
            ],
        )
        steps.append("13 Conflict detected")

        gap = svc.create_proof_gap(
            case_id=case.id,
            issue_key=issue.issue_key,
            issue_version=issue.version,
            proof_task_key=task.proof_task_key,
            proof_task_version=task.version,
            gap_type="EVIDENCE",
            description="缺少验收单",
            what_is_missing="验收签字材料",
        )
        steps.append("14 ProofGap created")

        a1 = svc.create_lawyer_assessment(
            case_id=case.id,
            issue_key=issue.issue_key,
            issue_version=issue.version,
            content="初版：需补强验收证据",
            actor_id=actor,
        )
        a2 = svc.amend_lawyer_assessment(
            a1.assessment_key, new_content="修订：验收单已补充后可主张", actor_id=actor
        )
        assert a2.version == 2
        steps.append("17-18 LawyerAssessment V1/V2")

        svc.waive_proof_gap(gap.id, resolution_note="已有替代说明", actor_id=actor)
        steps.append("16 gap waived")

        claim = svc.propose_claim(
            case_id=case.id,
            claim_type="PAYMENT",
            title="设计费",
            statement="请求支付设计费50万元",
            amount=500000.0,
            actor_id=actor,
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
            fact_key=fact.fact_key,
            fact_version=fact.version,
            role="BASIS",
            actor_id=actor,
        )
        steps.append("19-21 Claim + links")

        readiness = PleadingReadinessService(session).evaluate(case.id)
        assert readiness.status == "NOT_READY"
        steps.append("22 Readiness NOT_READY")

        session.commit()

        wp = IssueWorkProductService(session).build_case(case.id)
        assert wp.confirmed_issues
        steps.append("29 Issue Work Product")

        client = TestClient(app)
        ws = client.get(f"/api/cases/{case.id}/workspace")
        assert ws.status_code == 200
        assert ws.json().get("issue_work_product")
        steps.append("30 Workspace + Agent context data")

    print("ISSUE-CENTERED CASE WORKSPACE V2: PASS")
    print("Steps completed:", len(steps))
    for s in steps:
        print(" ", s)


if __name__ == "__main__":
    main()
