"""LLM Case Analyst engine tests (FakeLLM + Application gates)."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.application.case_analyst import CaseAnalystService
from backend.domain.errors import ValidationError
from backend.domain.services import DomainService
from backend.llm.analyst import LLMCaseAnalystEngine
from backend.llm.fake import FakeLLMClient
from backend.models import Fact
from backend.schemas.case_analyst import AnalystInput, EvidenceRef
from backend.skills.case_analyst import EvidenceView, SpanSnippet
from backend.workflow.seed import ensure_pleading_prep_template


def _view(
    *,
    eid: uuid.UUID | None = None,
    version: int = 1,
    title: str = "付款凭证",
    summary: str = "已付款300000元",
) -> EvidenceView:
    eid = eid or uuid.uuid4()
    return EvidenceView(
        evidence_item_id=eid,
        evidence_item_version=version,
        title=title,
        summary=summary,
        category="PAYMENT",
        source_spans=[
            SpanSnippet(
                source_span_id=uuid.uuid4(),
                quote=summary,
                page=1,
                paragraph=1,
                material_id=uuid.uuid4(),
            )
        ],
    )


def test_r_valid_accepted_refs_fact_candidate() -> None:
    view = _view()
    client = FakeLLMClient(
        responses=[
            {
                "facts": [
                    {
                        "proposal_id": str(uuid.uuid4()),
                        "statement": "被告已付款300000元",
                        "fact_type": "PAYMENT",
                        "supporting_evidence_refs": [
                            {
                                "evidence_item_id": str(view.evidence_item_id),
                                "evidence_item_version": view.evidence_item_version,
                            }
                        ],
                        "confidence": 0.7,
                        "analyst_reason": "payment voucher",
                        "uncertainties": [],
                    }
                ],
                "issues": [],
                "legal_theories": [],
                "conflicts": [],
                "missing_evidence": [],
            }
        ]
    )
    result = LLMCaseAnalystEngine(client).analyze(
        AnalystInput(
            case_id=uuid.uuid4(),
            accepted_evidence_refs=[
                EvidenceRef(
                    evidence_item_id=view.evidence_item_id,
                    evidence_item_version=view.evidence_item_version,
                )
            ],
        ),
        [view],
    )
    assert len(result.facts) == 1
    assert result.facts[0].statement


def _seed_material_span(svc: DomainService, case_id: uuid.UUID, actor_id: uuid.UUID, text: str):
    material = svc.register_material(
        case_id=case_id,
        filename=f"m-{uuid.uuid4().hex[:6]}.pdf",
        mime="application/pdf",
        byte_size=len(text),
        content_hash=f"h-{uuid.uuid4().hex[:12]}",
        storage_key=f"k-{uuid.uuid4().hex[:12]}",
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
    return span


def test_s_t_pending_excluded_not_in_engine_input_contract(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    ensure_pleading_prep_template(db_session)
    svc = DomainService(db_session)
    case = svc.create_case(title="Analyst gate", owner_user_id=owner_id)
    text = "付款记录"
    span = _seed_material_span(svc, case.id, actor_id, text)
    pending = svc.create_evidence_item(
        case_id=case.id,
        number="1",
        title="pending",
        summary=text,
        category="PAYMENT",
        source_span_ids=[span.id],
        actor_id=actor_id,
    )
    client = FakeLLMClient(responses=[{"facts": [], "conflicts": []}])
    analyst = CaseAnalystService(db_session, engine=LLMCaseAnalystEngine(client))
    try:
        analyst.analyze(
            case_id=case.id,
            accepted_evidence_refs=[
                {
                    "evidence_item_id": pending.id,
                    "evidence_item_version": pending.version,
                }
            ],
            actor_id=actor_id,
        )
        raised = False
    except ValidationError:
        raised = True
    assert raised, "PENDING evidence must be rejected by Application"

    accepted = svc.accept_evidence(pending.id, actor_id=actor_id)
    span2 = _seed_material_span(svc, case.id, actor_id, text)
    excluded = svc.create_evidence_item(
        case_id=case.id,
        number="2",
        title="excluded",
        summary=text,
        category="PAYMENT",
        source_span_ids=[span2.id],
        actor_id=actor_id,
    )
    excluded = svc.exclude_evidence(excluded.id, actor_id=actor_id)
    try:
        analyst.analyze(
            case_id=case.id,
            accepted_evidence_refs=[
                {
                    "evidence_item_id": excluded.id,
                    "evidence_item_version": excluded.version,
                }
            ],
            actor_id=actor_id,
        )
        raised2 = False
    except ValidationError:
        raised2 = True
    assert raised2, "EXCLUDED evidence must be rejected"
    _ = accepted


def test_u_wrong_version_rejected(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    ensure_pleading_prep_template(db_session)
    svc = DomainService(db_session)
    case = svc.create_case(title="ver", owner_user_id=owner_id)
    text = "合同签署"
    span = _seed_material_span(svc, case.id, actor_id, text)
    item = svc.create_evidence_item(
        case_id=case.id,
        number="1",
        title="合同",
        summary=text,
        category="CONTRACT",
        source_span_ids=[span.id],
        actor_id=actor_id,
    )
    item = svc.accept_evidence(item.id, actor_id=actor_id)
    client = FakeLLMClient(responses=[{"facts": []}])
    analyst = CaseAnalystService(db_session, engine=LLMCaseAnalystEngine(client))
    try:
        analyst.analyze(
            case_id=case.id,
            accepted_evidence_refs=[
                {"evidence_item_id": item.id, "evidence_item_version": item.version + 5}
            ],
            actor_id=actor_id,
        )
        ok = False
    except ValidationError:
        ok = True
    assert ok


def test_v_w_x_candidate_legal_isolation_and_conflict(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    ensure_pleading_prep_template(db_session)
    svc = DomainService(db_session)
    case = svc.create_case(title="conflict", owner_user_id=owner_id)
    texts = ["已付款300000元", "已付款500000元"]
    items = []
    for i, text in enumerate(texts):
        span = _seed_material_span(svc, case.id, actor_id, text)
        ev = svc.create_evidence_item(
            case_id=case.id,
            number=str(i + 1),
            title=f"付款{i}",
            summary=text,
            category="PAYMENT",
            source_span_ids=[span.id],
            actor_id=actor_id,
        )
        items.append(svc.accept_evidence(ev.id, actor_id=actor_id))

    payload = {
        "facts": [
            {
                "proposal_id": str(uuid.uuid4()),
                "statement": "已付款300000元",
                "fact_type": "PAYMENT",
                "supporting_evidence_refs": [
                    {
                        "evidence_item_id": str(items[0].id),
                        "evidence_item_version": items[0].version,
                    }
                ],
                "confidence": 0.6,
                "analyst_reason": "voucher A",
                "uncertainties": ["与另一金额冲突"],
            },
            {
                "proposal_id": str(uuid.uuid4()),
                "statement": "被告构成根本违约应承担责任",
                "fact_type": "OTHER",
                "supporting_evidence_refs": [
                    {
                        "evidence_item_id": str(items[0].id),
                        "evidence_item_version": items[0].version,
                    }
                ],
                "confidence": 0.9,
                "analyst_reason": "legal conclusion sneak",
                "uncertainties": [],
            },
        ],
        "issues": [],
        "legal_theories": [
            {
                "proposal_id": str(uuid.uuid4()),
                "theory_summary": "被告可能构成违约",
                "related_evidence_refs": [],
                "analyst_reason": "legal channel",
            }
        ],
        "conflicts": [
            {
                "type": "EVIDENCE_CONFLICT",
                "description": "付款金额 300000 vs 500000",
                "evidence_refs": [
                    {
                        "evidence_item_id": str(items[0].id),
                        "evidence_item_version": items[0].version,
                    },
                    {
                        "evidence_item_id": str(items[1].id),
                        "evidence_item_version": items[1].version,
                    },
                ],
            }
        ],
        "missing_evidence": [
            {
                "description": "缺少完整银行流水",
                "reason": "金额冲突需进一步证明",
                "related_evidence_refs": [],
            }
        ],
    }
    client = FakeLLMClient(responses=[payload])
    result = CaseAnalystService(
        db_session, engine=LLMCaseAnalystEngine(client)
    ).analyze(
        case_id=case.id,
        accepted_evidence_refs=[
            {"evidence_item_id": items[0].id, "evidence_item_version": items[0].version},
            {"evidence_item_id": items[1].id, "evidence_item_version": items[1].version},
        ],
        actor_id=actor_id,
    )
    assert result.conflicts
    assert result.missing_evidence
    for f in result.created_facts:
        assert f.status == "CANDIDATE"
    statements = [f.statement for f in result.created_facts]
    assert not any("根本违约" in s for s in statements)
    confirmed = list(
        db_session.scalars(
            select(Fact).where(Fact.case_id == case.id, Fact.status == "CONFIRMED")
        )
    )
    assert confirmed == []
