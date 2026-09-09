"""PDF text extraction tool — no Repository / Domain writes."""

from __future__ import annotations

from io import BytesIO

from pypdf import PdfReader

from backend.infrastructure.config import Settings, get_settings
from backend.tools.dto import (
    PAGE_BREAK_FORM_FEED,
    ParseFailureDTO,
    ParseResultDTO,
    ParseSuccessDTO,
    SpanProposal,
    build_page_map,
)
from backend.tools.errors import ParseToolError


class PdfParser:
    EXTRACTION_METHOD = "pdfplumber_text"
    # Using pypdf for extraction; method name kept stable for audit.
    EXTRACTION_VERSION = "v1"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def parse(self, data: bytes) -> ParseResultDTO:
        try:
            reader = PdfReader(BytesIO(data))
        except Exception as exc:  # noqa: BLE001
            return ParseFailureDTO(
                error_code="PDF_OPEN_FAILED",
                error_detail=str(exc)[:500],
                extraction_method=self.EXTRACTION_METHOD,
                extraction_version=self.EXTRACTION_VERSION,
            )

        if len(reader.pages) == 0:
            return ParseFailureDTO(
                error_code="PDF_EMPTY",
                error_detail="PDF has zero pages",
                extraction_method=self.EXTRACTION_METHOD,
                extraction_version=self.EXTRACTION_VERSION,
            )

        page_texts: list[str] = []
        page_non_ws: list[int] = []
        try:
            for page in reader.pages:
                text = page.extract_text() or ""
                page_texts.append(text)
                page_non_ws.append(len("".join(text.split())))
        except Exception as exc:  # noqa: BLE001
            return ParseFailureDTO(
                error_code="PDF_PAGE_EXTRACT_FAILED",
                error_detail=str(exc)[:500],
                extraction_method=self.EXTRACTION_METHOD,
                extraction_version=self.EXTRACTION_VERSION,
            )

        if self._looks_like_scan(page_non_ws):
            return ParseFailureDTO(
                error_code="PDF_NEEDS_OCR",
                error_detail=(
                    "该 PDF 没有可提取的文字层（多为扫描件或图片型 PDF）。"
                    "将尝试通过 CamScanner CLI 转为 Markdown；"
                    "若未启用或转换失败，请手动转为 .md / .docx 后再上传。"
                ),
                extraction_method=self.EXTRACTION_METHOD,
                extraction_version=self.EXTRACTION_VERSION,
                needs_ocr=True,
                meta={"page_non_ws": page_non_ws},
            )

        # Atomic rule: any required page that failed hard already returned above.
        # Empty page text is allowed if overall not classified as scan.
        full_text, layout = build_page_map(page_texts, separator=PAGE_BREAK_FORM_FEED)
        spans: list[SpanProposal] = []
        for entry in layout["page_map"]:
            start, end = entry["start"], entry["end"]
            if end <= start:
                continue
            quote = full_text[start:end]
            spans.append(
                SpanProposal(
                    character_start=start,
                    character_end=end,
                    quote=quote,
                    page=entry["page"],
                    paragraph=None,
                    bbox_json=None,
                    confidence=1.0,
                    weak_localization=False,
                )
            )

        if not spans:
            return ParseFailureDTO(
                error_code="PDF_NO_TEXT_SPANS",
                error_detail="no extractable text spans",
                extraction_method=self.EXTRACTION_METHOD,
                extraction_version=self.EXTRACTION_VERSION,
                needs_ocr=True,
            )

        return ParseSuccessDTO(
            full_text=full_text,
            page_count=len(page_texts),
            layout_json=layout,
            spans=spans,
            extraction_method=self.EXTRACTION_METHOD,
            extraction_version=self.EXTRACTION_VERSION,
        )

    def _looks_like_scan(self, page_non_ws: list[int]) -> bool:
        total = sum(page_non_ws)
        if total < self.settings.pdf_scan_min_total_non_ws_chars:
            return True
        empty_threshold = self.settings.pdf_scan_empty_page_non_ws_chars
        empty_pages = sum(1 for n in page_non_ws if n < empty_threshold)
        ratio = empty_pages / max(len(page_non_ws), 1)
        return ratio >= self.settings.pdf_scan_empty_page_ratio


def assert_pdf_parseable_or_raise(data: bytes) -> None:
    """Helper for callers that want exceptions instead of DTO."""
    result = PdfParser().parse(data)
    if isinstance(result, ParseFailureDTO) and not result.needs_ocr:
        raise ParseToolError(result.error_detail, code=result.error_code)
