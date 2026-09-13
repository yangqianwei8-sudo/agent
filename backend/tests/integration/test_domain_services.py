"""Integration tests — DomainService core flows."""

from __future__ import annotations

import hashlib
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.domain.errors import ImmutableError, ValidationError
from backend.domain.services import DomainService, ensure_material_bytes_immutable
from backend.models import (
    AuditLog,
    DraftCitation,
    EvidenceItem,
    Fact,
    FactEvidenceLink,
    HumanDecision,
    LegalTheory,
    WorkflowInstance,
    WorkflowTemplate,
)


def _svc(session: Session) -> DomainService:
    return DomainService(session)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_create_and_archive_case(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    svc = _svc(db_session)
    case = svc.create_case(title="审图合同纠纷", owner_user_id=owner_id)
    assert case.status == "OPEN"
    archived = svc.archive_case(case.id, actor_id=actor_id)
    assert archived.status == "ARCHIVED"
    audits = db_session.scalars(select(AuditLog).where(AuditLog.case_id == case.id)).all()
    assert len(audits) >= 2


def test_material_immutable_and_parse(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    svc = _svc(db_session)
    case = svc.create_case(title="C1", owner_user_id=owner_id)
    material = svc.register_material(
        case_id=case.id,
        filename="合同.pdf",
        mime="application/pdf",
        byte_size=10,
        content_hash="abc",
        storage_key="s3://x",
        created_by=actor_id,
    )
    assert material.parse_status == "PENDING"
    with pytest.raises(ImmutableError):
        ensure_material_bytes_immutable(material, {"content_hash": "changed"})
    text = "甲方乙方签订设计合同。"
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
    assert span.quote_hash == _hash(text)
    assert material.parse_status == "SUCCEEDED"


def test_evidence_versioning_and_citation_pins_old_version(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    svc = _svc(db_session)
    case = svc.create_case(title="C2", owner_user_id=owner_id)
    material = svc.register_material(
        case_id=case.id,
        filename="a.pdf",
        mime="application/pdf",
        byte_size=1,
        content_hash="h1",
        storage_key="k1",
        created_by=actor_id,
    )
    text = "证据正文内容足够长"
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
        case_id=case.id,
        number="1",
        title="设计合同",
        category="合同",
        source_span_ids=[span.id],
        actor_id=actor_id,
    )
    assert item.version == 1
    same_id = item.id
    renumbered = svc.amend_evidence_item(item.id, actor_id=actor_id, number="E1")
    assert renumbered.version == 1
    assert renumbered.number == "E1"
    amended = svc.amend_evidence_item(item.id, actor_id=actor_id, title="设计合同（修订）")
    assert amended.version == 2
    assert amended.id == same_id
    old = db_session.scalars(
        select(EvidenceItem).where(EvidenceItem.id == same_id, EvidenceItem.version == 1)
    ).one()
    assert old.is_current is False

    draft = svc.create_document_draft(
        case_id=case.id,
        body_structured_json={"sections": []},
        citations=[
            {
                "block_id": "b1",
                "citation_kind": "EVIDENCE",
                "evidence_item_id": same_id,
                "evidence_item_version": 1,
            }
        ],
        actor_id=actor_id,
    )
    cite = db_session.scalars(
        select(DraftCitation).where(DraftCitation.draft_id == draft.id)
    ).one()
    assert cite.evidence_item_id == same_id
    assert cite.evidence_item_version == 1


def test_fact_lifecycle(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    svc = _svc(db_session)
    case = svc.create_case(title="C3", owner_user_id=owner_id)
    material = svc.register_material(
        case_id=case.id,
        filename="a.pdf",
        mime="application/pdf",
        byte_size=1,
        content_hash="h2",
        storage_key="k2",
        created_by=actor_id,
    )
    text = "被告于2025年6月1日收到设计成果。"
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
    item = svc.create_evidence_item(
        case_id=case.id,
        number="1",
        title="签收单",
        category="书证",
        source_span_ids=[span.id],
        actor_id=actor_id,
    )
    with pytest.raises(ValidationError):
        svc.propose_fact(case_id=case.id, statement="x", evidence_links=[])

    fact = svc.propose_fact(
        case_id=case.id,
        statement=text,
        evidence_links=[
            {
                "evidence_item_id": item.id,
                "evidence_item_version": item.version,
                "source_span_id": span.id,
            }
        ],
        actor_id=actor_id,
    )
    assert fact.status == "CANDIDATE"
    confirmed = svc.confirm_fact(fact.fact_key, actor_id=actor_id)
    assert confirmed.status == "CONFIRMED"
    assert confirmed.confirm_decision_id is not None
    decisions = db_session.scalars(select(HumanDecision)).all()
    assert any(d.id == confirmed.confirm_decision_id for d in decisions)

    amended = svc.amend_fact(
        fact.fact_key,
        new_statement="被告于2025年6月3日收到设计成果。",
        actor_id=actor_id,
    )
    assert amended.version == 2
    assert amended.status == "CONFIRMED"
    old = db_session.get(Fact, confirmed.id)
    assert old is not None
    assert old.status == "SUPERSEDED"

    other = svc.propose_fact(
        case_id=case.id,
        statement="无关背景事实",
        evidence_links=[
            {"evidence_item_id": item.id, "evidence_item_version": item.version}
        ],
        actor_id=actor_id,
    )
    svc.confirm_fact(other.fact_key, actor_id=actor_id)
    rejected = svc.reject_fact(other.fact_key, actor_id=actor_id)
    assert rejected.status == "REJECTED"


def test_party_amend_stale_rules(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    svc = _svc(db_session)
    case = svc.create_case(title="C4", owner_user_id=owner_id)
    material = svc.register_material(
        case_id=case.id,
        filename="a.pdf",
        mime="application/pdf",
        byte_size=1,
        content_hash="h3",
        storage_key="k3",
        created_by=actor_id,
    )
    text = "合同文本"
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
    item = svc.create_evidence_item(
        case_id=case.id,
        number="1",
        title="合同",
        category="合同",
        source_span_ids=[span.id],
        actor_id=actor_id,
    )
    party = svc.create_party(
        case_id=case.id, role="DEFENDANT", name="B公司", party_type="ORG", actor_id=actor_id
    )
    svc.confirm_party(party.party_key, actor_id=actor_id)
    fact_touch = svc.propose_fact(
        case_id=case.id,
        statement="B公司未支付设计费",
        evidence_links=[{"evidence_item_id": item.id, "evidence_item_version": 1}],
        actor_id=actor_id,
    )
    svc.confirm_fact(fact_touch.fact_key, actor_id=actor_id)
    fact_unrelated = svc.propose_fact(
        case_id=case.id,
        statement="设计成果已交付",
        evidence_links=[{"evidence_item_id": item.id, "evidence_item_version": 1}],
        actor_id=actor_id,
    )
    svc.confirm_fact(fact_unrelated.fact_key, actor_id=actor_id)
    draft = svc.create_document_draft(
        case_id=case.id,
        body_structured_json={"x": 1},
        citations=[
            {
                "block_id": "1",
                "citation_kind": "FACT",
                "fact_key": fact_touch.fact_key,
                "fact_version": 1,
            }
        ],
        status="IN_REVIEW",
        actor_id=actor_id,
    )
    new_party = svc.amend_party(party.party_key, name="C公司", actor_id=actor_id)
    assert new_party.version == 2
    db_session.refresh(fact_touch)
    db_session.refresh(fact_unrelated)
    db_session.refresh(draft)
    assert fact_touch.stale is True
    assert fact_unrelated.stale is False
    assert draft.status == "STALE"


def test_claim_direction_supporting_facts(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    svc = _svc(db_session)
    case = svc.create_case(title="C5", owner_user_id=owner_id)
    material = svc.register_material(
        case_id=case.id,
        filename="a.pdf",
        mime="application/pdf",
        byte_size=1,
        content_hash="h4",
        storage_key="k4",
        created_by=actor_id,
    )
    text = "应付设计费"
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
    item = svc.create_evidence_item(
        case_id=case.id,
        number="1",
        title="账单",
        category="书证",
        source_span_ids=[span.id],
        actor_id=actor_id,
    )
    fact = svc.propose_fact(
        case_id=case.id,
        statement="被告欠付设计费",
        evidence_links=[{"evidence_item_id": item.id, "evidence_item_version": 1}],
        actor_id=actor_id,
    )
    missing = {
        "overall_strategy": "主张价款",
        "claims": [
            {
                "claim_type": "PAYMENT",
                "description": "支付设计费",
                "amount": 1,
                "currency": "CNY",
                "supporting_fact_ids": [str(uuid.uuid4())],
            }
        ],
    }
    claim = svc.create_claim_direction(
        _legacy_compat=True, case_id=case.id, payload=missing, actor_id=actor_id
    )
    with pytest.raises(ValidationError):
        svc.confirm_claim_direction(claim.claim_direction_key, actor_id=actor_id)

    candidate_payload = {
        "overall_strategy": "主张价款",
        "claims": [
            {
                "claim_type": "PAYMENT",
                "description": "支付设计费",
                "amount": 100,
                "currency": "CNY",
                "supporting_fact_ids": [str(fact.fact_key)],
            }
        ],
    }
    claim2 = svc.create_claim_direction(_legacy_compat=True,
        case_id=case.id, payload=candidate_payload, actor_id=actor_id
    )
    with pytest.raises(ValidationError):
        svc.confirm_claim_direction(claim2.claim_direction_key, actor_id=actor_id)

    svc.confirm_fact(fact.fact_key, actor_id=actor_id)
    confirmed = svc.confirm_claim_direction(claim2.claim_direction_key, actor_id=actor_id)
    assert confirmed.status == "CONFIRMED"

    # stale supporting fact blocks confirm of a new candidate
    svc.reject_fact(fact.fact_key, actor_id=actor_id)


def test_exclude_evidence_and_extract_rebuilt(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    svc = _svc(db_session)
    case = svc.create_case(title="C6", owner_user_id=owner_id)
    material = svc.register_material(
        case_id=case.id,
        filename="a.pdf",
        mime="application/pdf",
        byte_size=1,
        content_hash="h5",
        storage_key="k5",
        created_by=actor_id,
    )
    text = "唯一证据内容"
    ec1 = svc.create_extracted_content(
        material_id=material.id,
        extraction_method="pdfplumber",
        extraction_version="v1",
        full_text=text,
        actor_id=actor_id,
    )
    span = svc.create_source_span(
        material_id=material.id,
        extracted_content_id=ec1.id,
        character_start=0,
        character_end=len(text),
        quote=text,
        extraction_method="pdfplumber",
        extraction_version="v1",
    )
    item = svc.create_evidence_item(
        case_id=case.id,
        number="1",
        title="唯一证据",
        category="书证",
        source_span_ids=[span.id],
        actor_id=actor_id,
    )
    fact = svc.propose_fact(
        case_id=case.id,
        statement="仅由该证据支持的事实",
        evidence_links=[{"evidence_item_id": item.id, "evidence_item_version": 1}],
        actor_id=actor_id,
    )
    svc.confirm_fact(fact.fact_key, actor_id=actor_id)
    svc.exclude_evidence(item.id, actor_id=actor_id)
    db_session.refresh(fact)
    assert fact.stale is True
    links = db_session.scalars(
        select(FactEvidenceLink).where(FactEvidenceLink.fact_id == fact.id)
    ).all()
    assert all(link.status == "VOID" for link in links)

    svc.create_extracted_content(
        material_id=material.id,
        extraction_method="pdfplumber",
        extraction_version="v2",
        full_text=text + "追加",
        actor_id=actor_id,
        previous_extracted_content_id=ec1.id,
    )
    db_session.refresh(item)
    # item may already be EXCLUDED; rebuild marks current evidence with that span weakly stale
    current = db_session.scalars(
        select(EvidenceItem).where(EvidenceItem.id == item.id, EvidenceItem.is_current.is_(True))
    ).one()
    assert current.stale is True
    assert current.stale_reason == "EXTRACT_REBUILT"


def test_draft_and_workflow_instance_independence(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    svc = _svc(db_session)
    case = svc.create_case(title="C7", owner_user_id=owner_id)
    template = WorkflowTemplate(code="PLEADING_PREP", name="起诉准备", version=1)
    db_session.add(template)
    db_session.flush()
    instance = WorkflowInstance(
        case_id=case.id,
        template_id=template.id,
        template_version=1,
        status="SUCCEEDED",
    )
    db_session.add(instance)
    db_session.flush()
    draft = svc.create_document_draft(
        case_id=case.id,
        body_structured_json={"ok": True},
        citations=[{"block_id": "a", "citation_kind": "ANNOTATION"}],
        status="IN_REVIEW",
        actor_id=actor_id,
    )
    assert instance.status == "SUCCEEDED"
    assert draft.status == "IN_REVIEW"
    approved = svc.approve_document_draft(draft.id, actor_id=actor_id)
    assert approved.status == "APPROVED_BY_LAWYER"
    db_session.refresh(instance)
    assert instance.status == "SUCCEEDED"


def test_claim_direction_changed_stales_theory_and_draft(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    svc = _svc(db_session)
    case = svc.create_case(title="C8", owner_user_id=owner_id)
    material = svc.register_material(
        case_id=case.id,
        filename="a.pdf",
        mime="application/pdf",
        byte_size=1,
        content_hash="h6",
        storage_key="k6",
        created_by=actor_id,
    )
    text = "价款事实"
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
    item = svc.create_evidence_item(
        case_id=case.id,
        number="1",
        title="账单",
        category="书证",
        source_span_ids=[span.id],
        actor_id=actor_id,
    )
    fact = svc.propose_fact(
        case_id=case.id,
        statement="应付100",
        evidence_links=[{"evidence_item_id": item.id, "evidence_item_version": 1}],
        actor_id=actor_id,
    )
    svc.confirm_fact(fact.fact_key, actor_id=actor_id)
    payload = {
        "overall_strategy": "主张价款",
        "claims": [
            {
                "claim_type": "PAYMENT",
                "description": "支付设计费",
                "amount": 100,
                "currency": "CNY",
                "supporting_fact_ids": [str(fact.fact_key)],
            }
        ],
    }
    claim = svc.create_claim_direction(
        _legacy_compat=True, case_id=case.id, payload=payload, actor_id=actor_id
    )
    svc.confirm_claim_direction(claim.claim_direction_key, actor_id=actor_id)
    theory = LegalTheory(
        case_id=case.id,
        theory_summary="合同之债",
        supporting_fact_ids=[str(fact.fact_key)],
        layer="CANDIDATE",
    )
    db_session.add(theory)
    db_session.flush()
    draft = svc.create_document_draft(
        case_id=case.id,
        body_structured_json={"a": 1},
        citations=[{"block_id": "1", "citation_kind": "ANNOTATION"}],
        status="IN_REVIEW",
        actor_id=actor_id,
    )
    new_payload = {
        "overall_strategy": "主张价款及违约金",
        "claims": [
            {
                "claim_type": "PAYMENT",
                "description": "支付设计费",
                "amount": 200,
                "currency": "CNY",
                "supporting_fact_ids": [str(fact.fact_key)],
            }
        ],
    }
    svc.amend_claim_direction(
        claim.claim_direction_key,
        payload=new_payload,
        actor_id=actor_id,
        _legacy_compat=True,
    )
    db_session.refresh(theory)
    db_session.refresh(draft)
    assert theory.stale is True
    assert draft.status == "STALE"
    db_session.refresh(fact)
    assert fact.stale is False


def test_human_decision_insert_only_semantics(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    svc = _svc(db_session)
    case = svc.create_case(title="C9", owner_user_id=owner_id)
    party = svc.create_party(
        case_id=case.id, role="PLAINTIFF", name="甲", party_type="ORG", actor_id=actor_id
    )
    confirmed = svc.confirm_party(party.party_key, actor_id=actor_id)
    decision_id = confirmed.confirm_decision_id
    assert decision_id is not None
    decision = db_session.get(HumanDecision, decision_id)
    assert decision is not None
    # Domain has no update API for decisions; rows remain as inserted.
    assert decision.result == "CONFIRMED"
