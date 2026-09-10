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
    Claim,
    ClaimDirection,
    ClaimFactLink,
    ClaimIssueLink,
    DocumentDraft,
    EvidenceItem,
    EvidenceItemSpan,
    ExtractedContent,
    Fact,
    FactEvidenceLink,
    HumanDecision,
    Issue,
    IssueEvidenceLink,
    IssueFactLink,
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

    def get_fact_version(self, fact_key: UUID, version: int) -> Fact | None:
        return self.session.scalars(
            select(Fact).where(Fact.fact_key == fact_key, Fact.version == version)
        ).first()

    def get_fact_row(self, fact_id: UUID) -> Fact | None:
        return self.session.get(Fact, fact_id)

    def get_current_issue(self, issue_key: UUID) -> Issue | None:
        return self.session.scalars(
            select(Issue).where(Issue.issue_key == issue_key, Issue.is_current.is_(True))
        ).first()

    def get_issue_version(self, issue_key: UUID, version: int) -> Issue | None:
        return self.session.scalars(
            select(Issue).where(Issue.issue_key == issue_key, Issue.version == version)
        ).first()

    def list_issue_fact_links(
        self, issue_key: UUID, issue_version: int
    ) -> list[IssueFactLink]:
        return list(
            self.session.scalars(
                select(IssueFactLink).where(
                    IssueFactLink.issue_key == issue_key,
                    IssueFactLink.issue_version == issue_version,
                    IssueFactLink.status == "ACTIVE",
                )
            )
        )

    def list_issue_evidence_links(
        self, issue_key: UUID, issue_version: int
    ) -> list[IssueEvidenceLink]:
        return list(
            self.session.scalars(
                select(IssueEvidenceLink).where(
                    IssueEvidenceLink.issue_key == issue_key,
                    IssueEvidenceLink.issue_version == issue_version,
                )
            )
        )

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

    def get_current_relief_claim(self, claim_key: UUID) -> Claim | None:
        return self.session.scalars(
            select(Claim).where(Claim.claim_key == claim_key, Claim.is_current.is_(True))
        ).first()

    def get_relief_claim_version(self, claim_key: UUID, version: int) -> Claim | None:
        return self.session.scalars(
            select(Claim).where(Claim.claim_key == claim_key, Claim.version == version)
        ).first()

    def list_claim_issue_links(
        self, claim_key: UUID, claim_version: int
    ) -> list[ClaimIssueLink]:
        return list(
            self.session.scalars(
                select(ClaimIssueLink).where(
                    ClaimIssueLink.claim_key == claim_key,
                    ClaimIssueLink.claim_version == claim_version,
                    ClaimIssueLink.status == "ACTIVE",
                )
            )
        )

    def list_claim_fact_links(
        self, claim_key: UUID, claim_version: int
    ) -> list[ClaimFactLink]:
        return list(
            self.session.scalars(
                select(ClaimFactLink).where(
                    ClaimFactLink.claim_key == claim_key,
                    ClaimFactLink.claim_version == claim_version,
                    ClaimFactLink.status == "ACTIVE",
                )
            )
        )

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
