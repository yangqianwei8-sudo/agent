"""Parser Tool DTOs — never written directly to DB by tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

PAGE_BREAK_FORM_FEED = "\f"
PAGE_BREAK_POLICY = "form_feed"


@dataclass
class SpanProposal:
    character_start: int
    character_end: int
    quote: str
    page: int | None = None
    paragraph: int | None = None
    bbox_json: dict[str, Any] | None = None
    confidence: float | None = None
    weak_localization: bool = False


@dataclass
class ParseSuccessDTO:
    full_text: str
    page_count: int | None
    layout_json: dict[str, Any]
    spans: list[SpanProposal]
    extraction_method: str
    extraction_version: str
    needs_ocr: bool = False
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParseFailureDTO:
    error_code: str
    error_detail: str
    extraction_method: str
    extraction_version: str
    needs_ocr: bool = False
    meta: dict[str, Any] = field(default_factory=dict)


ParseResultDTO = ParseSuccessDTO | ParseFailureDTO


def build_page_map(
    page_texts: list[str],
    *,
    separator: str = PAGE_BREAK_FORM_FEED,
) -> tuple[str, dict[str, Any]]:
    """Join pages with explicit separator; page_map uses [start, end) over page text only."""
    page_map: list[dict[str, int]] = []
    parts: list[str] = []
    cursor = 0
    for i, text in enumerate(page_texts):
        page_no = i + 1
        start = cursor
        end = start + len(text)
        page_map.append({"page": page_no, "start": start, "end": end})
        parts.append(text)
        cursor = end
        if i < len(page_texts) - 1:
            cursor += len(separator)
    full_text = separator.join(parts)
    layout = {
        "page_map": page_map,
        "page_break_policy": PAGE_BREAK_POLICY,
        "separator": separator,
    }
    return full_text, layout
