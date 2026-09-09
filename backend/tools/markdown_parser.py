"""Markdown text extraction tool — no Repository / Domain writes.

Preserves raw Markdown for downstream LLM use (highest-fidelity text path).
"""

from __future__ import annotations

from backend.tools.dto import ParseFailureDTO, ParseResultDTO, ParseSuccessDTO, SpanProposal


class MarkdownParser:
    EXTRACTION_METHOD = "markdown_text"
    EXTRACTION_VERSION = "v1"

    def parse(self, data: bytes, *, filename: str | None = None) -> ParseResultDTO:
        del filename  # reserved for future format checks
        if not data:
            return ParseFailureDTO(
                error_code="MD_EMPTY",
                error_detail="empty markdown file",
                extraction_method=self.EXTRACTION_METHOD,
                extraction_version=self.EXTRACTION_VERSION,
            )

        text = self._decode(data)
        # Normalize newlines; keep Markdown syntax intact for LLM.
        text = text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
        if not text.strip():
            return ParseFailureDTO(
                error_code="MD_EMPTY",
                error_detail="markdown has no extractable text",
                extraction_method=self.EXTRACTION_METHOD,
                extraction_version=self.EXTRACTION_VERSION,
            )

        blocks = self._split_blocks(text)
        separator = "\n\n"
        spans: list[SpanProposal] = []
        full_parts: list[str] = []
        paragraph_map: list[dict[str, int]] = []
        cursor = 0
        for i, block in enumerate(blocks):
            para_no = i + 1
            start = cursor
            end = start + len(block)
            paragraph_map.append({"paragraph": para_no, "start": start, "end": end})
            if end > start:
                spans.append(
                    SpanProposal(
                        character_start=start,
                        character_end=end,
                        quote=block,
                        page=None,
                        paragraph=para_no,
                        bbox_json=None,
                        confidence=1.0,
                        weak_localization=False,
                    )
                )
            full_parts.append(block)
            cursor = end
            if i < len(blocks) - 1:
                cursor += len(separator)

        full_text = separator.join(full_parts)
        if not spans:
            return ParseFailureDTO(
                error_code="MD_NO_TEXT_SPANS",
                error_detail="no extractable text spans",
                extraction_method=self.EXTRACTION_METHOD,
                extraction_version=self.EXTRACTION_VERSION,
            )

        return ParseSuccessDTO(
            full_text=full_text,
            page_count=None,
            layout_json={
                "page_map": [],
                "paragraph_map": paragraph_map,
                "page_break_policy": "none",
                "separator": separator,
            },
            spans=spans,
            extraction_method=self.EXTRACTION_METHOD,
            extraction_version=self.EXTRACTION_VERSION,
            meta={"format": "markdown", "raw_preserved": True},
        )

    def _decode(self, data: bytes) -> str:
        for encoding in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                continue
        return data.decode("utf-8", errors="replace")

    def _split_blocks(self, text: str) -> list[str]:
        """Split on blank lines; fall back to whole document as one block."""
        parts = [p.strip("\n") for p in text.split("\n\n")]
        blocks = [p for p in parts if p.strip()]
        return blocks if blocks else [text]
