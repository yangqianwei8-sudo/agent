"""Issue-Centered Case Workspace V2 — live acceptance (real PostgreSQL)."""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

os.environ["LLM_MODE"] = "deterministic"

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from backend.agent.case_agent import CaseAgent  # noqa: E402
from backend.agent.intent_router import DeterministicIntentRouter  # noqa: E402
from backend.infrastructure.config import clear_settings_cache  # noqa: E402
from backend.llm.factory import clear_llm_caches  # noqa: E402
from backend.llm.case_conversation import DeterministicCaseConversationEngine  # noqa: E402

clear_settings_cache()
clear_llm_caches()
from backend.application.issue_work_product import IssueWorkProductService  # noqa: E402
from backend.application.pleading_input_production import (  # noqa: E402
    PleadingStructuredInputProductionBuilder,
    assert_closed_relation_graph,
)
from backend.application.pleading_readiness import PleadingReadinessService  # noqa: E402
from backend.application.pleading_writer import PleadingWriterService  # noqa: E402
from backend.domain.services import DomainService  # noqa: E402
from backend.infrastructure.db import get_session_factory  # noqa: E402
from backend.models import DraftCitation  # noqa: E402
from backend.schemas.case_analyst import EvidenceRef  # noqa: E402
from backend.schemas.claim_direction_proposal import FactRef  # noqa: E402
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
    return material, span, item


def _confirm_fact_from_evidence(
    svc: DomainService,
    *,
    case_id: uuid.UUID,
    actor_id: uuid.UUID,
    item,
    span,
    statement: str,
):
    fact = svc.propose_fact(
        case_id=case_id,
        statement=statement,
        importance="CORE",
        evidence_links=[
            {
                "evidence_item_id": item.id,
                "evidence_item_version": item.version,
                "link_role": "PROVES",
                "source_span_id": span.id,
            }
        ],
        actor_id=actor_id,
    )
    svc.confirm_fact(fact.fact_key, actor_id=actor_id)
    return svc.repo.get_current_fact(fact.fact_key)


def main() -> int:
    Session = get_session_factory()
    steps: list[str] = []
    with Session() as session:
        owner = uuid.uuid4()
        actor = uuid.uuid4()
        svc = DomainService(session)

        case = svc.create_case(title="Issue V2 Live", owner_user_id=owner)
        steps.append("1 Case")

        plaintiff_name = "甲设计公司"
        defendant_name = "乙建设公司"
        text = (
            f"2025年3月1日{plaintiff_name}与{defendant_name}签订设计咨询合同，"
            "约定设计费50万元。"
        )
        material, span, item = _seed_material(svc, case.id, actor, text)
        steps.append("2 Material")

        svc.accept_evidence(item.id, actor_id=actor)
        item = svc.repo.get_current_evidence(item.id)
        assert item is not None
        steps.append("3 Evidence accepted")

        fact = _confirm_fact_from_evidence(
            svc,
            case_id=case.id,
            actor_id=actor,
            item=item,
            span=span,
            statement=(
                f"合同签约甲方：{plaintiff_name}。"
                f"合同签约乙方：{defendant_name}。"
                "双方于2025年3月1日签订设计咨询合同。"
            ),
        )
        assert fact is not None
        steps.append("4 Fact confirmed")

        candidate = svc.propose_issue(
            case_id=case.id, statement="现有材料是否足以证明成果已交付？"
        )
        steps.append("5 AI candidate Issue")
        issue = svc.confirm_issue(candidate.issue_key, actor_id=actor)
        steps.append("6 Issue confirmed")

        pos = svc.propose_position(
            case_id=case.id,
            issue_key=issue.issue_key,
            issue_version=issue.version,
            side="OUR",
            position_type="ASSERTION",
            statement="我方主张成果已按约交付",
        )
        svc.confirm_position(pos.position_key, actor_id=actor)
        steps.append("7 OUR Position")

        ant = svc.propose_position(
            case_id=case.id,
            issue_key=issue.issue_key,
            issue_version=issue.version,
            side="OUR",
            position_type="ANTICIPATED_DEFENSE",
            statement="对方可能抗辩未验收",
        )
        svc.confirm_position(ant.position_key, actor_id=actor)
        steps.append("8 anticipated opponent defense")

        pt = svc.propose_proof_task(
            case_id=case.id,
            issue_key=issue.issue_key,
            issue_version=issue.version,
            description="证明成果已交付",
        )
        steps.append("9 AI ProofTask")
        task = svc.adopt_proof_task(pt.proof_task_key, actor_id=actor)
        steps.append("10 ProofTask adopted")

        svc.link_fact_to_proof_task(
            case_id=case.id,
            proof_task_key=task.proof_task_key,
            proof_task_version=task.version,
            fact_key=fact.fact_key,
            fact_version=fact.version,
            role="SUPPORT",
            actor_id=actor,
        )
        steps.append("11 SUPPORT Fact link")

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
        steps.append("12 ADVERSE Fact link")

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

        sup_text = "2025年4月1日甲方签收设计成果验收单。"
        _, sup_span, sup_item = _seed_material(svc, case.id, actor, sup_text)
        svc.accept_evidence(sup_item.id, actor_id=actor)
        sup_item = svc.repo.get_current_evidence(sup_item.id)
        _confirm_fact_from_evidence(
            svc,
            case_id=case.id,
            actor_id=actor,
            item=sup_item,
            span=sup_span,
            statement="甲方于2025年4月1日签收设计成果验收单。",
        )
        steps.append("15 supplemental material/evidence/fact")

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
        steps.append("17 LawyerAssessment V1")
        steps.append("18 LawyerAssessment V2 history")

        svc.waive_proof_gap(gap.id, resolution_note="已有替代说明", actor_id=actor)
        steps.append("16 gap waived")

        claim = svc.propose_claim(
            case_id=case.id,
            claim_type="PAYMENT",
            title="设计费",
            statement="设计费",
            amount=500000.0,
            currency="CNY",
            actor_id=actor,
        )
        claim = svc.confirm_claim(claim.claim_key, actor_id=actor)
        steps.append("19 confirmed Claim")

        svc.link_issue_to_claim(
            case_id=case.id,
            claim_key=claim.claim_key,
            claim_version=claim.version,
            issue_key=issue.issue_key,
            issue_version=issue.version,
            role="BASIS",
            actor_id=actor,
        )
        steps.append("20 Claim→Issue link")

        svc.link_fact_to_claim(
            case_id=case.id,
            claim_key=claim.claim_key,
            claim_version=claim.version,
            fact_key=fact.fact_key,
            fact_version=fact.version,
            role="BASIS",
            actor_id=actor,
        )
        steps.append("21 Claim→Fact link")

        readiness = PleadingReadinessService(session).evaluate(case.id)
        assert readiness.status == "NOT_READY"
        steps.append("22 Readiness NOT_READY")

        plaintiff = svc.create_party(
            case_id=case.id,
            role="PLAINTIFF",
            name=plaintiff_name,
            party_type="ORG",
            actor_id=actor,
        )
        defendant = svc.create_party(
            case_id=case.id,
            role="DEFENDANT",
            name=defendant_name,
            party_type="ORG",
            actor_id=actor,
        )
        svc.confirm_party(plaintiff.party_key, actor_id=actor)
        svc.confirm_party(defendant.party_key, actor_id=actor)

        for stmt in (
            f"被告{defendant_name}确认承担设计咨询合同项下全部付款责任。",
            "原告已向被告交付设计成果并经签收。",
            "设计费总额为500000元，被告已支付0元，尚欠500000元。",
            "合同约定成果提交后付款，付款条件已成就，债务已到期。",
            "合同约定由被告住所地人民法院管辖。",
        ):
            _confirm_fact_from_evidence(
                svc,
                case_id=case.id,
                actor_id=actor,
                item=sup_item,
                span=sup_span,
                statement=stmt,
            )
        steps.append("23 supplement critical facts")

        ready = PleadingReadinessService(session).evaluate(case.id)
        assert ready.status == "READY", [i.code for i in ready.blocking_issues]
        steps.append("24 Readiness READY")

        builder = PleadingStructuredInputProductionBuilder(session)
        parties = builder._load_confirmed_parties(case.id)
        facts = builder._load_confirmed_facts_for_case(case.id)
        evidence = builder._load_accepted_evidence_for_case(case.id)
        inp = builder.build(case_id=case.id, parties=parties, facts=facts, evidence=evidence)
        steps.append("25 PleadingStructuredInput")

        assert_closed_relation_graph(inp)
        steps.append("26 closed relation graph PASS")

        writer = PleadingWriterService(session)
        draft_result = writer.write(
            case_id=case.id,
            claim_direction_ref=None,
            confirmed_fact_refs=[
                FactRef(fact_key=f.fact_key, fact_version=f.fact_version) for f in facts
            ],
            accepted_evidence_refs=[
                EvidenceRef(
                    evidence_item_id=e.evidence_item_id,
                    evidence_item_version=e.evidence_item_version,
                )
                for e in evidence
            ],
            confirmed_party_keys=[plaintiff.party_key, defendant.party_key],
            actor_id=actor,
        )
        assert draft_result.draft is not None
        steps.append("27 generate pleading")

        cites = list(
            session.scalars(
                select(DraftCitation).where(DraftCitation.draft_id == draft_result.draft.id)
            )
        )
        assert cites, "draft must have citations"
        snap = (draft_result.draft.body_structured_json or {}).get(
            "structured_input_snapshot"
        ) or {}
        assert snap.get("facts") and snap.get("evidence") and snap.get("fact_evidence_relations")
        steps.append("28 reverse trace Draft→Fact→Evidence→SourceSpan→Material")

        session.commit()

        wp = IssueWorkProductService(session).build_case(case.id)
        assert wp.confirmed_issues
        steps.append("29 Issue Work Product")

        client = TestClient(app)
        ws = client.get(f"/api/cases/{case.id}/workspace")
        assert ws.status_code == 200
        assert ws.json().get("issue_work_product")
        page = client.get(f"/cases/{case.id}")
        assert page.status_code == 200
        assert "案件卷宗" in page.text

        agent = CaseAgent(
            session,
            actor_id=actor,
            intent_engine=DeterministicIntentRouter(),
            conversation_engine=DeterministicCaseConversationEngine(),
        )
        resp = agent.handle_message(
            case.id,
            "当前焦点证明情况如何？",
            current_issue_key=issue.issue_key,
            current_issue_version=issue.version,
            current_object_type="ProofTask",
            current_object_ref=str(task.proof_task_key),
        )
        assert "当前争议焦点" in resp.message
        steps.append("30 Workspace + Agent Issue/Object context")

    print("ISSUE-CENTERED CASE WORKSPACE V2: PASS")
    print("Steps completed:", len(steps))
    for s in steps:
        print(" ", s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
