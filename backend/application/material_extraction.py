"""Application use case: Material → ExtractedContent → SourceSpan.

Does not create EvidenceItem. Does not call LLM.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from backend.domain.errors import ValidationError
from backend.domain.services import DomainService
from backend.infrastructure.config import get_settings
from backend.models import ExtractedContent, SourceSpan
from backend.tools.docx_parser import DocxParser
from backend.tools.dto import ParseFailureDTO, ParseSuccessDTO
from backend.tools.errors import UnsupportedFormatError
from backend.tools.image_ocr import ImageOcr
from backend.tools.pdf_parser import PdfParser
from backend.tools.storage import ObjectStorage


@dataclass
class ExtractionOutcome:
    extracted_content: ExtractedContent
    spans: list[SourceSpan]
    success: bool
    needs_ocr: bool = False
    meta: dict[str, Any] | None = None


class MaterialExtractionService:
    def __init__(
        self,
        session: Session,
        *,
        storage: ObjectStorage | None = None,
        pdf_parser: PdfParser | None = None,
        docx_parser: DocxParser | None = None,
        image_ocr: ImageOcr | None = None,
    ) -> None:
        self.session = session
        self.domain = DomainService(session)
        self.storage = storage or ObjectStorage()
        self.pdf_parser = pdf_parser or PdfParser()
        self.docx_parser = docx_parser or DocxParser()
        self.image_ocr = image_ocr or ImageOcr()
        self.settings = get_settings()

    def extract_material(
        self,
        material_id: UUID,
        *,
        actor_id: UUID,
        extraction_version: str | None = None,
        previous_extracted_content_id: UUID | None = None,
        force_ocr: bool = False,
        ocr_page_images: list[bytes] | None = None,
    ) -> ExtractionOutcome:
        material = self.domain.repo.get_material(material_id)
        if material is None:
            raise ValidationError("material not found")

        data = self.storage.get_bytes(material.storage_key)
        actual_hash = self.storage.content_hash(data)
        if actual_hash != material.content_hash:
            raise ValidationError("storage object hash mismatch with CaseMaterial.content_hash")
        if len(data) > self.settings.max_material_bytes:
            return self._persist_failure(
                material_id=material_id,
                actor_id=actor_id,
                method="size_check",
                version=extraction_version or "v1",
                error_code="FILE_TOO_LARGE",
                error_detail=f"file exceeds max_material_bytes={self.settings.max_material_bytes}",
                previous_extracted_content_id=previous_extracted_content_id,
            )

        filename = material.filename.lower()
        mime = (material.mime or "").lower()

        try:
            result = self._dispatch_parse(
                data,
                filename=filename,
                mime=mime,
                force_ocr=force_ocr,
                ocr_page_images=ocr_page_images,
                version=extraction_version,
            )
        except UnsupportedFormatError as exc:
            return self._persist_failure(
                material_id=material_id,
                actor_id=actor_id,
                method="format_check",
                version=extraction_version or "v1",
                error_code=exc.code,
                error_detail=exc.message,
                previous_extracted_content_id=previous_extracted_content_id,
            )

        if isinstance(result, ParseFailureDTO):
            if result.needs_ocr and not force_ocr:
                # Hand off to OCR path if caller provided page images, else fail with needs_ocr
                if ocr_page_images is not None:
                    result = self.image_ocr.parse_scanned_pdf_pages(ocr_page_images)
                else:
                    return self._persist_failure(
                        material_id=material_id,
                        actor_id=actor_id,
                        method=result.extraction_method,
                        version=result.extraction_version,
                        error_code=result.error_code,
                        error_detail=result.error_detail,
                        previous_extracted_content_id=previous_extracted_content_id,
                        needs_ocr=True,
                        meta=result.meta,
                    )

        if isinstance(result, ParseFailureDTO):
            return self._persist_failure(
                material_id=material_id,
                actor_id=actor_id,
                method=result.extraction_method,
                version=result.extraction_version,
                error_code=result.error_code,
                error_detail=result.error_detail,
                previous_extracted_content_id=previous_extracted_content_id,
                needs_ocr=result.needs_ocr,
                meta=result.meta,
            )

        assert isinstance(result, ParseSuccessDTO)
        version = extraction_version or result.extraction_version
        ec = self.domain.create_extracted_content(
            material_id=material_id,
            extraction_method=result.extraction_method,
            extraction_version=version,
            full_text=result.full_text,
            page_count=result.page_count,
            layout_json=result.layout_json,
            status="SUCCEEDED",
            actor_id=actor_id,
            previous_extracted_content_id=previous_extracted_content_id,
        )
        spans = self._persist_spans(material_id, ec, result)
        return ExtractionOutcome(
            extracted_content=ec,
            spans=spans,
            success=True,
            meta=result.meta,
        )

    def _dispatch_parse(
        self,
        data: bytes,
        *,
        filename: str,
        mime: str,
        force_ocr: bool,
        ocr_page_images: list[bytes] | None,
        version: str | None,
    ) -> ParseSuccessDTO | ParseFailureDTO:
        if filename.endswith(".doc") and not filename.endswith(".docx"):
            raise UnsupportedFormatError(".doc is not supported in V1; convert to .docx")

        if force_ocr or mime.startswith("image/"):
            if ocr_page_images is not None:
                return self.image_ocr.parse_scanned_pdf_pages(ocr_page_images)
            return self.image_ocr.parse_image(data)

        if filename.endswith(".docx") or "wordprocessingml" in mime:
            return self.docx_parser.parse(data, filename=filename)

        if filename.endswith(".pdf") or mime == "application/pdf":
            return self.pdf_parser.parse(data)

        if filename.endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp")):
            return self.image_ocr.parse_image(data)

        raise UnsupportedFormatError(f"unsupported material type: {filename or mime}")

    def _persist_spans(
        self,
        material_id: UUID,
        ec: ExtractedContent,
        result: ParseSuccessDTO,
    ) -> list[SourceSpan]:
        spans: list[SourceSpan] = []
        for proposal in result.spans:
            bbox = proposal.bbox_json
            if proposal.weak_localization and bbox is None:
                bbox = {"weak_localization": True}
            elif proposal.weak_localization and bbox is not None:
                bbox = {**bbox, "weak_localization": True}
            span = self.domain.create_source_span(
                material_id=material_id,
                extracted_content_id=ec.id,
                character_start=proposal.character_start,
                character_end=proposal.character_end,
                quote=proposal.quote,
                extraction_method=result.extraction_method,
                extraction_version=ec.extraction_version,
                page=proposal.page,
                paragraph=proposal.paragraph,
                bbox_json=bbox,
                confidence=proposal.confidence,
            )
            spans.append(span)
        return spans

    def _persist_failure(
        self,
        *,
        material_id: UUID,
        actor_id: UUID,
        method: str,
        version: str,
        error_code: str,
        error_detail: str,
        previous_extracted_content_id: UUID | None,
        needs_ocr: bool = False,
        meta: dict[str, Any] | None = None,
    ) -> ExtractionOutcome:
        ec = self.domain.create_extracted_content(
            material_id=material_id,
            extraction_method=method,
            extraction_version=version,
            status="FAILED",
            error_detail=f"{error_code}: {error_detail}",
            actor_id=actor_id,
            previous_extracted_content_id=previous_extracted_content_id,
        )
        return ExtractionOutcome(
            extracted_content=ec,
            spans=[],
            success=False,
            needs_ocr=needs_ocr,
            meta=meta,
        )
