"""ClaimViewService — read-only projection for versioned relief claims."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.domain.errors import NotFoundError
from backend.models import Case, Claim
from backend.repositories.base import Repository
from backend.schemas.claim_view import ClaimFactRef, ClaimIssueRef, ClaimsView, ClaimViewItem


class ClaimViewService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = Repository(session)

    def build(
        self,
        case_id: UUID,
        *,
        include_candidates: bool = True,
        include_rejected: bool = False,
        include_superseded: bool = False,
        include_history: bool = False,
    ) -> ClaimsView:
        case = self.session.get(Case, case_id)
        if case is None:
            raise NotFoundError("case not found")

        if include_history:
            include_superseded = True
            rows = list(
                self.session.scalars(
                    select(Claim)
                    .where(Claim.case_id == case_id)
                    .order_by(Claim.claim_key.asc(), Claim.version.asc())
                )
            )
        else:
            rows = list(
                self.session.scalars(
                    select(Claim)
                    .where(Claim.case_id == case_id, Claim.is_current.is_(True))
                    .order_by(Claim.created_at.asc())
                )
            )

        items: list[ClaimViewItem] = []
        confirmed = 0
        candidate = 0
        for row in rows:
            if row.status == "REJECTED" and not include_rejected:
                continue
            if row.status == "SUPERSEDED" and not include_superseded:
                continue
            if row.status == "CANDIDATE" and not include_candidates:
                continue
            item = self._build_item(row)
            items.append(item)
            if row.status == "CONFIRMED":
                confirmed += 1
            elif row.status == "CANDIDATE":
                candidate += 1

        return ClaimsView(
            case_id=str(case_id),
            items=items,
            confirmed_count=confirmed,
            candidate_count=candidate,
        )

    def _build_item(self, claim: Claim) -> ClaimViewItem:
        issue_links = self.repo.list_claim_issue_links(claim.claim_key, claim.version)
        fact_links = self.repo.list_claim_fact_links(claim.claim_key, claim.version)

        basis_issues: list[ClaimIssueRef] = []
        limitation_issues: list[ClaimIssueRef] = []
        context_issues: list[ClaimIssueRef] = []
        for link in issue_links:
            ref = self._issue_ref(link)
            if ref is None:
                continue
            if link.role == "BASIS":
                basis_issues.append(ref)
            elif link.role == "LIMITATION":
                limitation_issues.append(ref)
            else:
                context_issues.append(ref)

        basis_facts: list[ClaimFactRef] = []
        amount_basis_facts: list[ClaimFactRef] = []
        limitations: list[ClaimFactRef] = []
        for link in fact_links:
            ref = self._fact_ref(link)
            if ref is None:
                continue
            if link.role == "BASIS":
                basis_facts.append(ref)
            elif link.role == "AMOUNT_BASIS":
                amount_basis_facts.append(ref)
            elif link.role == "LIMITATION":
                limitations.append(ref)

        warnings: list[str] = []
        if claim.status == "CONFIRMED" and not basis_issues and not basis_facts:
            warnings.append("CONFIRMED claim lacks BASIS issue or fact links")
        if claim.amount_is_suggested:
            warnings.append("Amount is AI-suggested until lawyer confirms")
        if claim.stale:
            warnings.append(f"Claim is stale: {claim.stale_reason or 'unknown'}")

        return ClaimViewItem(
            claim_key=str(claim.claim_key),
            claim_version=claim.version,
            claim_type=claim.claim_type,
            title=claim.title,
            statement=claim.statement,
            amount=claim.amount,
            currency=claim.currency,
            amount_is_suggested=claim.amount_is_suggested,
            status=claim.status,
            source_type=claim.source_type,
            lawyer_confirmation_state=self._confirmation_state(claim),
            stale_state={
                "stale": claim.stale,
                "stale_reason": claim.stale_reason,
                "stale_at": claim.stale_at.isoformat() if claim.stale_at else None,
            },
            basis_issues=basis_issues,
            limitation_issues=limitation_issues,
            context_issues=context_issues,
            basis_facts=basis_facts,
            amount_basis_facts=amount_basis_facts,
            limitations=limitations,
            warnings=warnings,
        )

    def _issue_ref(self, link) -> ClaimIssueRef | None:
        issue = self.repo.get_issue_version(link.issue_key, link.issue_version)
        if issue is None or issue.case_id != link.case_id:
            return None
        return ClaimIssueRef(
            issue_key=str(link.issue_key),
            issue_version=link.issue_version,
            statement=issue.statement,
            status=issue.status,
            role=link.role,
            link_id=str(link.id),
        )

    def _fact_ref(self, link) -> ClaimFactRef | None:
        fact = self.repo.get_fact_version(link.fact_key, link.fact_version)
        if fact is None or fact.case_id != link.case_id:
            return None
        return ClaimFactRef(
            fact_key=str(link.fact_key),
            fact_version=link.fact_version,
            statement=fact.statement,
            status=fact.status,
            role=link.role,
            link_id=str(link.id),
        )

    @staticmethod
    def _confirmation_state(claim: Claim) -> str:
        if claim.status == "REJECTED":
            return "REJECTED"
        if claim.status == "SUPERSEDED":
            return "SUPERSEDED"
        if claim.status == "CANDIDATE":
            return "AI_CANDIDATE" if claim.source_type == "AI_PROPOSED" else "CANDIDATE"
        if claim.source_type == "LAWYER_CREATED":
            return "LAWYER_CREATED"
        if claim.source_type == "LAWYER_REFINED":
            return "LAWYER_REFINED"
        return "LAWYER_CONFIRMED"
