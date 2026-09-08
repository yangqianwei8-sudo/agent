"""Phase 4 Material / Extraction tests."""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.application.material_extraction import MaterialExtractionService
from backend.domain.errors import ValidationError
from backend.domain.services import DomainService
from backend.models import ExtractedContent, SourceSpan
from backend.tests.fixtures.generate_fixtures import generate as generate_fixtures
from backend.tools.docx_parser import DocxParser
from backend.tools.errors import UnsupportedFormatError
from backend.tools.image_ocr import ImageOcr
from backend.tools.pdf_parser import PdfParser
from backend.tools.storage import ObjectStorage

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture(scope="session", autouse=True)
def _ensure_fixtures() -> None:
    generate_fixtures()


@pytest.fixture
def storage(tmp_path: Path) -> ObjectStorage:
    return ObjectStorage(root=tmp_path / "storage")


def _register(
    session: Session,
    storage: ObjectStorage,
    *,
    owner_id: uuid.UUID,
    actor_id: uuid.UUID,
    filename: str,
    mime: str,
    data: bytes,
) -> uuid.UUID:
    case = DomainService(session).create_case(title="P4", owner_user_id=owner_id)
    digest = hashlib.sha256(data).hexdigest()
    key = f"{case.id}/{digest[:16]}_{filename}"
    storage.put_bytes(key, data)
    material = DomainService(session).register_material(
        case_id=case.id,
        filename=filename,
        mime=mime,
        byte_size=len(data),
        content_hash=digest,
        storage_key=key,
        created_by=actor_id,
    )
    return material.id


def test_pdf_multipage_layout_and_spans(
    db_session: Session, storage: ObjectStorage, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    data = (FIXTURES / "sample_text.pdf").read_bytes()
    material_id = _register(
        db_session,
        storage,
        owner_id=owner_id,
        actor_id=actor_id,
        filename="sample_text.pdf",
        mime="application/pdf",
        data=data,
    )
    svc = MaterialExtractionService(db_session, storage=storage)
    outcome = svc.extract_material(material_id, actor_id=actor_id)
    assert outcome.success is True
    ec = outcome.extracted_content
    assert ec.status == "SUCCEEDED"
    assert ec.full_text
    assert ec.layout_json is not None
    assert ec.layout_json["page_break_policy"] == "form_feed"
    assert len(ec.layout_json["page_map"]) >= 2
    assert ec.page_count >= 2
    assert outcome.spans
    for span in outcome.spans:
        assert span.page is not None and span.page >= 1
        assert ec.full_text[span.character_start : span.character_end] == span.quote
        assert hashlib.sha256(span.quote.encode()).hexdigest() == span.quote_hash
        assert span.extracted_content_id == ec.id


def test_scanned_pdf_needs_ocr_then_stub(
    db_session: Session, storage: ObjectStorage, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    data = (FIXTURES / "sample_scanned.pdf").read_bytes()
    material_id = _register(
        db_session,
        storage,
        owner_id=owner_id,
        actor_id=actor_id,
        filename="sample_scanned.pdf",
        mime="application/pdf",
        data=data,
    )
    svc = MaterialExtractionService(db_session, storage=storage)
    failed = svc.extract_material(material_id, actor_id=actor_id)
    assert failed.success is False
    assert failed.needs_ocr is True
    assert failed.extracted_content.status == "FAILED"
    spans = db_session.scalars(
        select(SourceSpan).where(
            SourceSpan.extracted_content_id == failed.extracted_content.id
        )
    ).all()
    assert spans == []

    ocr_ok = svc.extract_material(
        material_id,
        actor_id=actor_id,
        force_ocr=True,
        ocr_page_images=[b"fake-page-1", b"fake-page-2"],
        extraction_version="ocr-v1",
        previous_extracted_content_id=failed.extracted_content.id,
    )
    assert ocr_ok.success is True
    assert ocr_ok.extracted_content.extraction_method == "image_ocr_stub"
    assert ocr_ok.spans
    assert all(s.page is not None for s in ocr_ok.spans)


def test_docx_paragraph_table_page_null(
    db_session: Session, storage: ObjectStorage, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    data = (FIXTURES / "sample.docx").read_bytes()
    material_id = _register(
        db_session,
        storage,
        owner_id=owner_id,
        actor_id=actor_id,
        filename="sample.docx",
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        data=data,
    )
    svc = MaterialExtractionService(db_session, storage=storage)
    outcome = svc.extract_material(material_id, actor_id=actor_id)
    assert outcome.success is True
    assert any(s.paragraph is not None for s in outcome.spans)
    assert all(s.page is None for s in outcome.spans)
    assert "Fee | 100000" in (outcome.extracted_content.full_text or "")


def test_doc_rejected() -> None:
    with pytest.raises(UnsupportedFormatError):
        DocxParser().parse(b"fake", filename="legacy.doc")


def test_reparse_creates_new_ec_keeps_old(
    db_session: Session, storage: ObjectStorage, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    data = (FIXTURES / "sample_text.pdf").read_bytes()
    material_id = _register(
        db_session,
        storage,
        owner_id=owner_id,
        actor_id=actor_id,
        filename="sample_text.pdf",
        mime="application/pdf",
        data=data,
    )
    svc = MaterialExtractionService(db_session, storage=storage)
    first = svc.extract_material(material_id, actor_id=actor_id, extraction_version="v1")
    second = svc.extract_material(
        material_id,
        actor_id=actor_id,
        extraction_version="v2",
        previous_extracted_content_id=first.extracted_content.id,
    )
    assert first.extracted_content.id != second.extracted_content.id
    old = db_session.get(ExtractedContent, first.extracted_content.id)
    assert old is not None
    assert old.full_text == first.extracted_content.full_text
    old_spans = db_session.scalars(
        select(SourceSpan).where(SourceSpan.extracted_content_id == old.id)
    ).all()
    assert len(old_spans) == len(first.spans)


def test_failed_ec_cannot_create_span(
    db_session: Session, storage: ObjectStorage, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    data = (FIXTURES / "sample_scanned.pdf").read_bytes()
    material_id = _register(
        db_session,
        storage,
        owner_id=owner_id,
        actor_id=actor_id,
        filename="sample_scanned.pdf",
        mime="application/pdf",
        data=data,
    )
    svc = MaterialExtractionService(db_session, storage=storage)
    failed = svc.extract_material(material_id, actor_id=actor_id)
    assert failed.success is False
    with pytest.raises(ValidationError):
        DomainService(db_session).create_source_span(
            material_id=material_id,
            extracted_content_id=failed.extracted_content.id,
            character_start=0,
            character_end=1,
            quote="x",
            extraction_method="x",
            extraction_version="v1",
        )


def test_provenance_chain(
    db_session: Session, storage: ObjectStorage, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    data = (FIXTURES / "sample_text.pdf").read_bytes()
    material_id = _register(
        db_session,
        storage,
        owner_id=owner_id,
        actor_id=actor_id,
        filename="sample_text.pdf",
        mime="application/pdf",
        data=data,
    )
    svc = MaterialExtractionService(db_session, storage=storage)
    outcome = svc.extract_material(material_id, actor_id=actor_id)
    span = outcome.spans[0]
    ec = outcome.extracted_content
    material = DomainService(db_session).repo.get_material(material_id)
    assert material is not None
    assert storage.exists(material.storage_key)
    raw = storage.get_bytes(material.storage_key)
    assert storage.content_hash(raw) == material.content_hash
    assert span.extracted_content_id == ec.id
    assert ec.material_id == material.id
    assert ec.full_text is not None
    assert ec.full_text[span.character_start : span.character_end] == span.quote


def test_ocr_new_version_and_weak_bbox(
    db_session: Session, storage: ObjectStorage, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    data = (FIXTURES / "sample_image.png").read_bytes()
    material_id = _register(
        db_session,
        storage,
        owner_id=owner_id,
        actor_id=actor_id,
        filename="sample_image.png",
        mime="image/png",
        data=data,
    )
    svc = MaterialExtractionService(
        db_session, storage=storage, image_ocr=ImageOcr(with_bbox=True)
    )
    first = svc.extract_material(material_id, actor_id=actor_id, extraction_version="ocr-1")
    assert first.success is True
    assert first.spans[0].bbox_json is not None
    assert first.spans[0].bbox_json.get("weak_localization") is not True

    weak_svc = MaterialExtractionService(
        db_session, storage=storage, image_ocr=ImageOcr(with_bbox=False)
    )
    second = weak_svc.extract_material(
        material_id,
        actor_id=actor_id,
        extraction_version="ocr-2",
        previous_extracted_content_id=first.extracted_content.id,
    )
    assert second.success is True
    assert second.extracted_content.id != first.extracted_content.id
    assert second.spans[0].bbox_json is not None
    assert second.spans[0].bbox_json.get("weak_localization") is True


def test_phase4_does_not_create_evidence_item(
    db_session: Session, storage: ObjectStorage, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    from backend.models import EvidenceItem

    data = (FIXTURES / "sample_text.pdf").read_bytes()
    material_id = _register(
        db_session,
        storage,
        owner_id=owner_id,
        actor_id=actor_id,
        filename="sample_text.pdf",
        mime="application/pdf",
        data=data,
    )
    MaterialExtractionService(db_session, storage=storage).extract_material(
        material_id, actor_id=actor_id
    )
    items = db_session.scalars(select(EvidenceItem)).all()
    assert items == []


def test_pdf_parser_tool_unit() -> None:
    data = (FIXTURES / "sample_text.pdf").read_bytes()
    result = PdfParser().parse(data)
    from backend.tools.dto import ParseSuccessDTO

    assert isinstance(result, ParseSuccessDTO)
    assert result.layout_json["page_break_policy"] == "form_feed"
