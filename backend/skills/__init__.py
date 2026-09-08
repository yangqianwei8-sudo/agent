"""Skills package — Organizer + Analyst + Claim Direction + Pleading Writer."""

from backend.skills.case_analyst import (
    CaseAnalystEngine,
    DeterministicCaseAnalystStub,
    EvidenceView,
    ScriptedCaseAnalystEngine,
    looks_like_legal_conclusion,
)
from backend.skills.civil_complaint_renderer import build_body_structured, render_civil_complaint
from backend.skills.claim_direction import (
    ClaimDirectionEngine,
    DeterministicClaimDirectionStub,
    FactView,
    PartyView,
    ScriptedClaimDirectionEngine,
    extract_amounts_from_text,
)
from backend.skills.evidence_organizer import (
    DeterministicOrganizerStub,
    OrganizerEngine,
    ScriptedOrganizerEngine,
    SpanView,
)
from backend.skills.pleading_writer import (
    DeterministicPleadingWriterStub,
    PleadingWriterEngine,
    ScriptedPleadingWriterEngine,
    looks_like_fabricated_law_citation,
    looks_like_invented_interest,
)

__all__ = [
    "CaseAnalystEngine",
    "ClaimDirectionEngine",
    "DeterministicCaseAnalystStub",
    "DeterministicClaimDirectionStub",
    "DeterministicOrganizerStub",
    "DeterministicPleadingWriterStub",
    "EvidenceView",
    "FactView",
    "OrganizerEngine",
    "PartyView",
    "PleadingWriterEngine",
    "ScriptedCaseAnalystEngine",
    "ScriptedClaimDirectionEngine",
    "ScriptedOrganizerEngine",
    "ScriptedPleadingWriterEngine",
    "SpanView",
    "build_body_structured",
    "extract_amounts_from_text",
    "looks_like_fabricated_law_citation",
    "looks_like_invented_interest",
    "looks_like_legal_conclusion",
    "render_civil_complaint",
]
