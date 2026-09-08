"""LLM Case Analyst engine — candidates only; Application validates refs/versions."""

from __future__ import annotations

import json
import uuid
from typing import Any

from pydantic import ValidationError

from backend.llm.client import LLMClient
from backend.llm.errors import LLMError, LLMSchemaValidationError
from backend.llm.prompts import CASE_ANALYST_SYSTEM, PROMPT_VERSION
from backend.schemas.case_analyst import (
    AnalystEngineResult,
    AnalystInput,
    ConflictItem,
    FactProposal,
    IssueProposal,
    LegalTheoryProposal,
    MissingEvidenceItem,
)
from backend.skills.case_analyst import EvidenceView

_MAX_EVIDENCE = 40
_MAX_QUOTE = 800


class LLMCaseAnalystEngine:
    def __init__(self, client: LLMClient) -> None:
        self.client = client
        self.prompt_version = PROMPT_VERSION
        self.last_meta: dict[str, Any] = {}

    def analyze(
        self,
        inp: AnalystInput,
        evidence: list[EvidenceView],
    ) -> AnalystEngineResult:
        if not evidence:
            return AnalystEngineResult()

        allowed = {
            (str(v.evidence_item_id), int(v.evidence_item_version)) for v in evidence
        }
        batch = evidence[:_MAX_EVIDENCE]
        user_prompt = _build_analyst_prompt(inp, batch)

        try:
            result = self.client.complete_json(
                system_prompt=CASE_ANALYST_SYSTEM,
                user_prompt=user_prompt,
                schema_name="case_analyst_v1",
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
            raise LLMSchemaValidationError("analyst root must be object")

        facts = _parse_list(payload.get("facts"), FactProposal, _prep_fact)
        issues = _parse_list(payload.get("issues"), IssueProposal, _prep_issue)
        theories = _parse_list(
            payload.get("legal_theories"), LegalTheoryProposal, _prep_theory
        )
        conflicts = _parse_list(payload.get("conflicts"), ConflictItem, _prep_conflict)
        missing = _parse_list(
            payload.get("missing_evidence"), MissingEvidenceItem, _prep_missing
        )

        kept_facts: list[FactProposal] = []
        for fact in facts:
            refs = [
                r
                for r in fact.supporting_evidence_refs
                if (str(r.evidence_item_id), int(r.evidence_item_version)) in allowed
            ]
            if not refs:
                continue
            kept_facts.append(fact.model_copy(update={"supporting_evidence_refs": refs}))

        return AnalystEngineResult(
            facts=kept_facts,
            issues=issues,
            legal_theories=theories,
            conflicts=conflicts,
            missing_evidence=missing,
        )


def _parse_list(raw: Any, model_cls: type, prep) -> list:
    if not isinstance(raw, list):
        return []
    out = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        try:
            out.append(model_cls.model_validate(prep(dict(row))))
        except ValidationError:
            continue
    return out


def _ensure_pid(row: dict[str, Any]) -> dict[str, Any]:
    if "proposal_id" not in row:
        row["proposal_id"] = str(uuid.uuid4())
    return row


def _prep_refs(row: dict[str, Any], key: str) -> None:
    refs = row.get(key)
    if not isinstance(refs, list):
        return
    cleaned = []
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        eid = ref.get("evidence_item_id")
        ver = ref.get("evidence_item_version")
        if eid is None or ver is None:
            continue
        # version must already be numeric — do not invent from "latest"
        if isinstance(ver, str) and ver.isdigit():
            ver = int(ver)
        if not isinstance(ver, int):
            continue
        cleaned.append({"evidence_item_id": eid, "evidence_item_version": ver})
    row[key] = cleaned


def _prep_fact(row: dict[str, Any]) -> dict[str, Any]:
    row = _ensure_pid(row)
    _prep_refs(row, "supporting_evidence_refs")
    if not row.get("analyst_reason"):
        row["analyst_reason"] = "llm fact candidate"
    # Drop free-text dates that aren't ISO — avoid silent invention
    if isinstance(row.get("occurred_at"), str) and not row["occurred_at"][:1].isdigit():
        row["occurred_at"] = None
    return row


def _prep_issue(row: dict[str, Any]) -> dict[str, Any]:
    row = _ensure_pid(row)
    _prep_refs(row, "related_evidence_refs")
    return row


def _prep_theory(row: dict[str, Any]) -> dict[str, Any]:
    row = _ensure_pid(row)
    _prep_refs(row, "related_evidence_refs")
    return row


def _prep_conflict(row: dict[str, Any]) -> dict[str, Any]:
    _prep_refs(row, "evidence_refs")
    if not row.get("type"):
        row["type"] = "EVIDENCE_CONFLICT"
    return row


def _prep_missing(row: dict[str, Any]) -> dict[str, Any]:
    _prep_refs(row, "related_evidence_refs")
    return row


def _build_analyst_prompt(inp: AnalystInput, evidence: list[EvidenceView]) -> str:
    items = []
    for v in evidence:
        quotes = []
        for sn in v.source_spans[:3]:
            quotes.append(
                {
                    "source_span_id": str(sn.source_span_id),
                    "quote": (sn.quote or "")[:_MAX_QUOTE],
                    "page": sn.page,
                }
            )
        items.append(
            {
                "evidence_item_id": str(v.evidence_item_id),
                "evidence_item_version": v.evidence_item_version,
                "title": v.title,
                "summary": (v.summary or "")[:_MAX_QUOTE],
                "category": v.category,
                "source_spans": quotes,
            }
        )
    return (
        f"case_id={inp.case_id}\n"
        "仅可引用下列 ACCEPTED 证据及其精确 version。禁止引用 PENDING/EXCLUDED。\n"
        f"{json.dumps(items, ensure_ascii=False)}\n"
        "请输出 JSON。facts 中每条必须含 supporting_evidence_refs"
        "（evidence_item_id + evidence_item_version 整数）。"
    )
