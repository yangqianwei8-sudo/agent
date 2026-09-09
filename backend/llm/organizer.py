"""LLM Evidence Organizer engine — proposals only; Application validates spans."""

from __future__ import annotations

import json
import uuid
from typing import Any

from pydantic import ValidationError

from backend.llm.client import LLMClient
from backend.llm.errors import LLMError, LLMSchemaValidationError
from backend.llm.prompts import EVIDENCE_ORGANIZER_SYSTEM, PROMPT_VERSION
from backend.schemas.evidence_proposal import (
    EvidenceItemProposal,
    OrganizerEngineResult,
    OrganizerInput,
)
from backend.skills.evidence_organizer import SpanView

_MAX_SPANS_PER_CALL = 40
_MAX_QUOTE_CHARS = 1200


class LLMEvidenceOrganizerEngine:
    def __init__(self, client: LLMClient) -> None:
        self.client = client
        self.prompt_version = PROMPT_VERSION
        self.last_meta: dict[str, Any] = {}

    def organize(
        self,
        inp: OrganizerInput,
        spans: list[SpanView],
    ) -> OrganizerEngineResult:
        if not spans:
            return OrganizerEngineResult(proposals=[])

        allowed = {str(s.span_id) for s in spans}
        batch = spans[:_MAX_SPANS_PER_CALL]
        user_prompt = _build_organizer_prompt(inp, batch)

        try:
            result = self.client.complete_json(
                system_prompt=EVIDENCE_ORGANIZER_SYSTEM,
                user_prompt=user_prompt,
                schema_name="evidence_organizer_v1",
            )
        except LLMError:
            raise

        self.last_meta = {
            "model": result.model,
            "provider": result.provider,
            "prompt_version": PROMPT_VERSION,
            "request_id": result.request_id,
            "usage": result.usage.model_dump(),
            "latency_ms": result.latency_ms,
        }

        payload = result.parsed_json
        if not isinstance(payload, dict):
            raise LLMSchemaValidationError("organizer root must be object")
        raw_list = payload.get("proposals")
        if raw_list is None:
            raise LLMSchemaValidationError("organizer missing proposals")
        if not isinstance(raw_list, list):
            raise LLMSchemaValidationError("proposals must be array")

        proposals: list[EvidenceItemProposal] = []
        for raw in raw_list:
            if not isinstance(raw, dict):
                continue
            span_ids = raw.get("source_span_ids") or []
            if not span_ids:
                continue
            if any(str(sid) not in allowed for sid in span_ids):
                continue
            if _looks_filename_only(raw):
                continue
            try:
                row = dict(raw)
                if "proposal_id" not in row:
                    row["proposal_id"] = str(uuid.uuid4())
                prop = EvidenceItemProposal.model_validate(row)
            except ValidationError:
                continue
            proposals.append(prop)

        return OrganizerEngineResult(proposals=proposals)


def _build_organizer_prompt(inp: OrganizerInput, spans: list[SpanView]) -> str:
    items = []
    for s in spans:
        quote = (s.quote or "")[:_MAX_QUOTE_CHARS]
        items.append(
            {
                "source_span_id": str(s.span_id),
                "page": s.page,
                "paragraph": s.paragraph,
                "material_id": str(s.material_id),
                "quote": quote,
            }
        )
    return (
        f"case_id={inp.case_id}\n"
        f"extracted_content_ids={[str(x) for x in inp.extracted_content_ids]}\n"
        "只能引用下列 source_span_id。禁止编造 span。\n"
        f"{json.dumps(items, ensure_ascii=False)}\n"
        "请输出 JSON。"
    )


def _looks_filename_only(raw: dict[str, Any]) -> bool:
    title = str(raw.get("title") or "")
    summary = str(raw.get("summary") or "")
    blob = f"{title} {summary}".lower()
    if any(x in blob for x in (".pdf", ".docx", ".md", "文件名", "filename")) and (
        len(summary.strip()) < 8 or summary.strip().lower().endswith((".pdf", ".docx", ".md"))
    ):
        return True
    return False
