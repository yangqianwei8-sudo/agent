"""Civil complaint text renderer — structured Writer result → final draft text."""

from __future__ import annotations

from backend.schemas.pleading_writer import PleadingWriterEngineResult


def render_civil_complaint(result: PleadingWriterEngineResult) -> str:
    parts = [
        result.title,
        "",
        result.parties_section,
        "",
        result.claims_section,
        "",
        result.facts_and_reasons_section,
        "",
        result.evidence_section or "",
        "",
        result.court_section,
        "",
        result.signature_section,
    ]
    return "\n".join(parts).strip() + "\n"


def build_body_structured(result: PleadingWriterEngineResult, *, full_text: str) -> dict:
    """Persist structured blocks for DraftCitation mapping + rendered text."""
    return {
        "draft_type": "CIVIL_COMPLAINT",
        "title": result.title,
        "sections": {
            "parties": result.parties_section,
            "claims": result.claims_section,
            "facts_and_reasons": result.facts_and_reasons_section,
            "evidence": result.evidence_section,
            "court": result.court_section,
            "signature": result.signature_section,
        },
        "claims": [c.model_dump(mode="json") for c in result.claims],
        "blocks": [
            {
                "block_id": b.block_id,
                "text": b.text,
                "fact_refs": [r.model_dump(mode="json") for r in b.fact_refs],
                "evidence_refs": [r.model_dump(mode="json") for r in b.evidence_refs],
            }
            for b in result.fact_blocks
        ],
        "evidence_directory": [
            e.model_dump(mode="json") for e in result.evidence_directory
        ],
        "warnings": [w.model_dump(mode="json") for w in result.warnings],
        "used_fact_refs": [r.model_dump(mode="json") for r in result.used_fact_refs],
        "used_evidence_refs": [
            r.model_dump(mode="json") for r in result.used_evidence_refs
        ],
        "used_claim_direction_ref": (
            result.used_claim_direction_ref.model_dump(mode="json")
            if result.used_claim_direction_ref
            else None
        ),
        "full_text": full_text,
    }
