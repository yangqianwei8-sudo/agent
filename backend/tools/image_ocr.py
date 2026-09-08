"""Image / scan-PDF OCR tool interface + deterministic V1 stub.

Does not claim real OCR accuracy. No Repository / Domain writes.
"""

from __future__ import annotations

from backend.tools.dto import (
    PAGE_BREAK_FORM_FEED,
    ParseFailureDTO,
    ParseResultDTO,
    ParseSuccessDTO,
    SpanProposal,
    build_page_map,
)


class ImageOcr:
    """V1 stub OCR — deterministic contract tests only."""

    EXTRACTION_METHOD = "image_ocr_stub"
    EXTRACTION_VERSION = "v1-stub"

    def __init__(self, *, fail: bool = False, with_bbox: bool = True) -> None:
        self.fail = fail
        self.with_bbox = with_bbox

    def parse_image(self, data: bytes, *, page: int = 1) -> ParseResultDTO:
        if self.fail:
            return ParseFailureDTO(
                error_code="OCR_FAILED",
                error_detail="stub OCR forced failure",
                extraction_method=self.EXTRACTION_METHOD,
                extraction_version=self.EXTRACTION_VERSION,
            )
        if not data:
            return ParseFailureDTO(
                error_code="OCR_EMPTY_INPUT",
                error_detail="empty image bytes",
                extraction_method=self.EXTRACTION_METHOD,
                extraction_version=self.EXTRACTION_VERSION,
            )

        # Deterministic text from length — not real OCR
        text = f"[OCR_STUB page={page} bytes={len(data)}] SAMPLE RECOGNIZED TEXT"
        full_text, layout = build_page_map([text], separator=PAGE_BREAK_FORM_FEED)
        bbox = (
            {"x0": 0.0, "y0": 0.0, "x1": 100.0, "y1": 20.0, "page": page}
            if self.with_bbox
            else {"weak_localization": True}
        )
        span = SpanProposal(
            character_start=0,
            character_end=len(full_text),
            quote=full_text,
            page=page,
            paragraph=None,
            bbox_json=bbox,
            confidence=0.5 if self.with_bbox else None,
            weak_localization=not self.with_bbox,
        )
        return ParseSuccessDTO(
            full_text=full_text,
            page_count=1,
            layout_json=layout,
            spans=[span],
            extraction_method=self.EXTRACTION_METHOD,
            extraction_version=self.EXTRACTION_VERSION,
            meta={"stub": True, "real_ocr": False},
        )

    def parse_scanned_pdf_pages(self, page_images: list[bytes]) -> ParseResultDTO:
        """OCR each rendered page image; atomic — any page failure fails all."""
        if self.fail:
            return ParseFailureDTO(
                error_code="OCR_FAILED",
                error_detail="stub OCR forced failure",
                extraction_method=self.EXTRACTION_METHOD,
                extraction_version=self.EXTRACTION_VERSION,
            )
        if not page_images:
            return ParseFailureDTO(
                error_code="OCR_NO_PAGES",
                error_detail="no page images provided",
                extraction_method=self.EXTRACTION_METHOD,
                extraction_version=self.EXTRACTION_VERSION,
            )

        page_texts: list[str] = []
        for i, img in enumerate(page_images):
            if not img:
                return ParseFailureDTO(
                    error_code="OCR_PAGE_FAILED",
                    error_detail=f"empty image for page {i + 1}",
                    extraction_method=self.EXTRACTION_METHOD,
                    extraction_version=self.EXTRACTION_VERSION,
                )
            page_texts.append(
                f"[OCR_STUB page={i + 1} bytes={len(img)}] SAMPLE RECOGNIZED TEXT"
            )

        full_text, layout = build_page_map(page_texts, separator=PAGE_BREAK_FORM_FEED)
        spans: list[SpanProposal] = []
        for entry in layout["page_map"]:
            start, end = entry["start"], entry["end"]
            if end <= start:
                continue
            quote = full_text[start:end]
            bbox = (
                {
                    "x0": 0.0,
                    "y0": 0.0,
                    "x1": 100.0,
                    "y1": 20.0,
                    "page": entry["page"],
                }
                if self.with_bbox
                else {"weak_localization": True}
            )
            spans.append(
                SpanProposal(
                    character_start=start,
                    character_end=end,
                    quote=quote,
                    page=entry["page"],
                    paragraph=None,
                    bbox_json=bbox,
                    confidence=0.5 if self.with_bbox else None,
                    weak_localization=not self.with_bbox,
                )
            )

        return ParseSuccessDTO(
            full_text=full_text,
            page_count=len(page_texts),
            layout_json=layout,
            spans=spans,
            extraction_method=self.EXTRACTION_METHOD,
            extraction_version=self.EXTRACTION_VERSION,
            meta={"stub": True, "real_ocr": False},
        )
