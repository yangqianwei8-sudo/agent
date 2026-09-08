"""Repository helpers — thin persistence accessors."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import (
    AuditLog,
    Case,
    CaseMaterial,
    CaseParty,
    ClaimDirection,
    DocumentDraft,
    EvidenceItem,
    EvidenceItemSpan,
    ExtractedContent,
    Fact,
    FactEvidenceLink,
    HumanDecision,
    SourceSpan,
    WorkflowInstance,
)


class Repository:
    """Minimal repository facade used by DomainService."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, obj: object) -> None:
        self.session.add(obj)

    def flush(self) -> None:
        self.session.flush()

    def get_case(self, case_id: UUID) -> Case | None:
        return self.session.get(Case, case_id)

    def get_material(self, material_id: UUID) -> CaseMaterial | None:
        return self.session.get(CaseMaterial, material_id)

    def get_extracted_content(self, ec_id: UUID) -> ExtractedContent | None:
        return self.session.get(ExtractedContent, ec_id)

    def get_source_span(self, span_id: UUID) -> SourceSpan | None:
        return self.session.get(SourceSpan, span_id)

    def get_current_evidence(self, evidence_id: UUID) -> EvidenceItem | None:
        return self.session.scalars(
            select(EvidenceItem).where(
                EvidenceItem.id == evidence_id, EvidenceItem.is_current.is_(True)
            )
        ).first()

    def get_evidence_version(self, evidence_id: UUID, version: int) -> EvidenceItem | None:
        return self.session.scalars(
            select(EvidenceItem).where(
                EvidenceItem.id == evidence_id, EvidenceItem.version == version
            )
        ).first()

    def get_current_fact(self, fact_key: UUID) -> Fact | None:
        return self.session.scalars(
            select(Fact).where(Fact.fact_key == fact_key, Fact.is_current.is_(True))
        ).first()

    def get_fact_row(self, fact_id: UUID) -> Fact | None:
        return self.session.get(Fact, fact_id)

    def get_current_party(self, party_key: UUID) -> CaseParty | None:
        return self.session.scalars(
            select(CaseParty).where(
                CaseParty.party_key == party_key, CaseParty.is_current.is_(True)
            )
        ).first()

    def get_current_claim(self, claim_direction_key: UUID) -> ClaimDirection | None:
        return self.session.scalars(
            select(ClaimDirection).where(
                ClaimDirection.claim_direction_key == claim_direction_key,
                ClaimDirection.is_current.is_(True),
            )
        ).first()

    def get_draft(self, draft_id: UUID) -> DocumentDraft | None:
        return self.session.get(DocumentDraft, draft_id)

    def get_workflow_instance(self, instance_id: UUID) -> WorkflowInstance | None:
        return self.session.get(WorkflowInstance, instance_id)

    def list_evidence_spans(self, evidence_id: UUID, version: int) -> list[EvidenceItemSpan]:
        return list(
            self.session.scalars(
                select(EvidenceItemSpan).where(
                    EvidenceItemSpan.evidence_item_id == evidence_id,
                    EvidenceItemSpan.evidence_item_version == version,
                )
            )
        )

    def list_fact_links(self, fact_id: UUID) -> list[FactEvidenceLink]:
        return list(
            self.session.scalars(
                select(FactEvidenceLink).where(FactEvidenceLink.fact_id == fact_id)
            )
        )

    def add_audit(self, audit: AuditLog) -> None:
        self.session.add(audit)

    def add_decision(self, decision: HumanDecision) -> None:
        self.session.add(decision)
