"""LLM Evidence Organizer engine tests (FakeLLM + Application validation)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

from backend.application.evidence_organizer import EvidenceOrganizerService
from backend.domain.services import DomainService
from backend.llm.fake import FakeLLMClient
from backend.llm.organizer import LLMEvidenceOrganizerEngine
from backend.models import EvidenceItem
from backend.schemas.evidence_proposal import EvidenceItemProposal, OrganizerInput
from backend.skills.evidence_organizer import SpanView
from backend.workflow.seed import ensure_pleading_prep_template


def _span(quote: str = "合同约定服务费100万元") -> SpanView:
    return SpanView(
        span_id=uuid.uuid4(),
        quote=quote,
        page=1,
        paragraph=1,
        material_id=uuid.uuid4(),
        ec_id=uuid.uuid4(),
    )


def test_l_valid_span_proposal() -> None:
    span = _span()
    client = FakeLLMClient(
        responses=[
            {
                "proposals": [
                    {
                        "proposal_id": str(uuid.uuid4()),
                        "title": "服务费约定",
                        "summary": "合同约定服务费100万元",
                        "category": "CONTRACT",
                        "source_span_ids": [str(span.span_id)],
                        "confidence": 0.7,
                        "organizer_reason": "span supports fee term",
                    }
                ]
            }
        ]
    )
    engine = LLMEvidenceOrganizerEngine(client)
    result = engine.organize(
        OrganizerInput(case_id=uuid.uuid4(), extracted_content_ids=[uuid.uuid4()]),
        [span],
    )
    assert len(result.proposals) == 1
    assert result.proposals[0].source_span_ids == [span.span_id]


def test_m_nonexistent_span_dropped() -> None:
    span = _span()
    client = FakeLLMClient(
        responses=[
            {
                "proposals": [
                    {
                        "proposal_id": str(uuid.uuid4()),
                        "title": "幻觉",
                        "summary": "不存在的引用",
                        "category": "OTHER",
                        "source_span_ids": [str(uuid.uuid4())],
                        "confidence": 0.9,
                        "organizer_reason": "bad",
                    }
                ]
            }
        ]
    )
    result = LLMEvidenceOrganizerEngine(client).organize(
        OrganizerInput(case_id=uuid.uuid4(), extracted_content_ids=[uuid.uuid4()]),
        [span],
    )
    assert result.proposals == []


def test_n_empty_source_span_ids_dropped() -> None:
    span = _span()
    client = FakeLLMClient(
        responses=[
            {
                "proposals": [
                    {
                        "proposal_id": str(uuid.uuid4()),
                        "title": "无引用",
                        "summary": "x",
                        "category": "OTHER",
                        "source_span_ids": [],
                        "confidence": 0.5,
                        "organizer_reason": "bad",
                    }
                ]
            }
        ]
    )
    result = LLMEvidenceOrganizerEngine(client).organize(
        OrganizerInput(case_id=uuid.uuid4(), extracted_content_ids=[uuid.uuid4()]),
        [span],
    )
    assert result.proposals == []


def test_o_filename_only_hallucination_dropped() -> None:
    span = _span()
    client = FakeLLMClient(
        responses=[
            {
                "proposals": [
                    {
                        "proposal_id": str(uuid.uuid4()),
                        "title": "合同.pdf",
                        "summary": "合同.pdf",
                        "category": "OTHER",
                        "source_span_ids": [str(span.span_id)],
                        "confidence": 0.5,
                        "organizer_reason": "from filename",
                    }
                ]
            }
        ]
    )
    result = LLMEvidenceOrganizerEngine(client).organize(
        OrganizerInput(case_id=uuid.uuid4(), extracted_content_ids=[uuid.uuid4()]),
        [span],
    )
    assert result.proposals == []


def test_p_q_llm_engine_creates_pending_not_accepted(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    ensure_pleading_prep_template(db_session)
    svc = DomainService(db_session)
    case = svc.create_case(title="LLM Org", owner_user_id=owner_id)
    text = "双方签订设计优化咨询合同，约定服务费人民币700000元。"
    material = svc.register_material(
        case_id=case.id,
        filename="c.pdf",
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
    client = FakeLLMClient(
        responses=[
            {
                "proposals": [
                    {
                        "proposal_id": str(uuid.uuid4()),
                        "title": "咨询合同服务费",
                        "summary": text,
                        "category": "CONTRACT",
                        "source_span_ids": [str(span.id)],
                        "confidence": 0.8,
                        "organizer_reason": "contract fee",
                    }
                ]
            }
        ]
    )
    org = EvidenceOrganizerService(
        db_session, engine=LLMEvidenceOrganizerEngine(client)
    )
    result = org.organize(
        case_id=case.id,
        extracted_content_ids=[ec.id],
        actor_id=actor_id,
    )
    assert result.created
    for item in result.created:
        assert item.acceptance == "PENDING"
    assert not hasattr(LLMEvidenceOrganizerEngine, "accept_evidence")
    # reload via domain identity (EvidenceItem.pk may be row_id)
    from sqlalchemy import select

    current = db_session.scalars(
        select(EvidenceItem).where(EvidenceItem.id == result.created[0].id)
    ).first()
    assert current is not None
    assert current.acceptance == "PENDING"


def test_engine_cannot_emit_accept_field() -> None:
    # Schema forbids extra fields; acceptance not part of proposal
    with pytest.raises(Exception):  # noqa: B017
        EvidenceItemProposal.model_validate(
            {
                "proposal_id": str(uuid.uuid4()),
                "title": "t",
                "summary": "s",
                "category": "OTHER",
                "source_span_ids": [str(uuid.uuid4())],
                "confidence": 0.5,
                "organizer_reason": "r",
                "acceptance": "ACCEPTED",
            }
        )
