"""Issue Matrix V1 — read-only application projection for dispute analysis."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.domain.errors import NotFoundError
from backend.models import (
    Case,
    Issue,
    IssueEvidenceLink,
    IssueFactLink,
    LegalTheory,
)
from backend.repositories.base import Repository
from backend.schemas.issue_matrix import (
    GapItem,
    IssueMatrixItem,
    IssueMatrixView,
    MatrixEvidenceRef,
    MatrixFactRef,
)


class IssueMatrixService:
    """Build Issue Matrix for a single case (read-only, explicit versions)."""

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
    ) -> IssueMatrixView:
        case = self.session.get(Case, case_id)
        if case is None:
            raise NotFoundError("case not found")

        issues = list(
            self.session.scalars(
                select(Issue)
                .where(Issue.case_id == case_id, Issue.is_current.is_(True))
                .order_by(Issue.order_index.asc(), Issue.created_at.asc())
            )
        )

        items: list[IssueMatrixItem] = []
        aggregate_gaps: list[GapItem] = []
        confirmed_count = 0
        candidate_count = 0

        for issue in issues:
            if issue.status == "REJECTED" and not include_rejected:
                continue
            if issue.status == "SUPERSEDED" and not include_superseded:
                continue
            if issue.status == "CANDIDATE" and not include_candidates:
                continue

            item = self._build_item(issue)
            items.append(item)
            aggregate_gaps.extend(item.fact_gaps)
            aggregate_gaps.extend(item.evidence_gaps)
            if issue.status == "CONFIRMED":
                confirmed_count += 1
            elif issue.status == "CANDIDATE":
                candidate_count += 1

        theories = self._legal_theories(case_id)
        return IssueMatrixView(
            case_id=str(case_id),
            items=items,
            aggregate_gaps=aggregate_gaps,
            confirmed_issue_count=confirmed_count,
            candidate_issue_count=candidate_count,
            legal_theories=theories,
        )

    def _build_item(self, issue: Issue) -> IssueMatrixItem:
        fact_links = self.repo.list_issue_fact_links(issue.issue_key, issue.version)
        ev_links = self.repo.list_issue_evidence_links(issue.issue_key, issue.version)

        supporting_facts: list[MatrixFactRef] = []
        adverse_facts: list[MatrixFactRef] = []
        context_facts: list[MatrixFactRef] = []
        for link in fact_links:
            ref = self._fact_ref(link)
            if ref is None:
                continue
            if link.role == "SUPPORT":
                supporting_facts.append(ref)
            elif link.role == "ADVERSE":
                adverse_facts.append(ref)
            else:
                context_facts.append(ref)

        supporting_evidence: list[MatrixEvidenceRef] = []
        adverse_evidence: list[MatrixEvidenceRef] = []
        context_evidence: list[MatrixEvidenceRef] = []
        for link in ev_links:
            ref = self._evidence_ref(link)
            if ref is None:
                continue
            if link.role == "SUPPORT":
                supporting_evidence.append(ref)
            elif link.role == "ADVERSE":
                adverse_evidence.append(ref)
            else:
                context_evidence.append(ref)

        fact_gaps, evidence_gaps = self._compute_gaps(
            issue,
            supporting_facts=supporting_facts,
            context_facts=context_facts,
        )

        return IssueMatrixItem(
            issue_key=str(issue.issue_key),
            issue_version=issue.version,
            statement=issue.statement,
            status=issue.status,
            source_type=issue.source_type,
            order_index=issue.order_index,
            parent_issue_key=(
                str(issue.parent_issue_key) if issue.parent_issue_key else None
            ),
            supporting_facts=supporting_facts,
            adverse_facts=adverse_facts,
            context_facts=context_facts,
            supporting_evidence=supporting_evidence,
            adverse_evidence=adverse_evidence,
            context_evidence=context_evidence,
            fact_gaps=fact_gaps,
            evidence_gaps=evidence_gaps,
            lawyer_confirmation_state=self._confirmation_state(issue),
            stale_state={
                "stale": issue.stale,
                "stale_reason": issue.stale_reason,
                "stale_at": issue.stale_at.isoformat() if issue.stale_at else None,
            },
        )

    def _fact_ref(self, link: IssueFactLink) -> MatrixFactRef | None:
        fact = self.repo.get_fact_version(link.fact_key, link.fact_version)
        if fact is None or fact.case_id != link.case_id:
            return None
        return MatrixFactRef(
            fact_key=str(link.fact_key),
            fact_version=link.fact_version,
            statement=fact.statement,
            status=fact.status,
            role=link.role,
            link_id=str(link.id),
            explanation=link.explanation,
        )

    def _evidence_ref(self, link: IssueEvidenceLink) -> MatrixEvidenceRef | None:
        ev = self.repo.get_evidence_version(link.evidence_item_id, link.evidence_item_version)
        if ev is None or ev.case_id != link.case_id:
            return None
        return MatrixEvidenceRef(
            evidence_item_id=str(link.evidence_item_id),
            evidence_item_version=link.evidence_item_version,
            title=ev.title,
            acceptance=ev.acceptance,
            role=link.role,
            link_id=str(link.id),
            explanation=link.explanation,
        )

    def _compute_gaps(
        self,
        issue: Issue,
        *,
        supporting_facts: list[MatrixFactRef],
        context_facts: list[MatrixFactRef],
    ) -> tuple[list[GapItem], list[GapItem]]:
        fact_gaps: list[GapItem] = []
        evidence_gaps: list[GapItem] = []

        confirmed_support = [
            f for f in supporting_facts if f.status == "CONFIRMED" and f.role == "SUPPORT"
        ]
        if issue.status in {"CANDIDATE", "CONFIRMED"} and not confirmed_support:
            fact_gaps.append(
                GapItem(
                    type="FACT_GAP",
                    issue_key=str(issue.issue_key),
                    issue_version=issue.version,
                    description="该争点尚无已确认的支持事实关联。",
                )
            )

        for fact_ref in confirmed_support + [
            f for f in context_facts if f.status == "CONFIRMED"
        ]:
            if not self._fact_has_evidence(fact_ref.fact_key, fact_ref.fact_version):
                evidence_gaps.append(
                    GapItem(
                        type="EVIDENCE_GAP",
                        issue_key=str(issue.issue_key),
                        issue_version=issue.version,
                        related_fact_key=fact_ref.fact_key,
                        related_fact_version=fact_ref.fact_version,
                        description=(
                            f"已确认事实（v{fact_ref.fact_version}）缺少有效证据支撑。"
                        ),
                    )
                )

        return fact_gaps, evidence_gaps

    def _fact_has_evidence(self, fact_key: str, fact_version: int) -> bool:
        fact = self.repo.get_fact_version(UUID(fact_key), fact_version)
        if fact is None:
            return False
        links = self.repo.list_fact_links(fact.id)
        for link in links:
            if link.status != "ACTIVE":
                continue
            ev = self.repo.get_evidence_version(link.evidence_item_id, link.evidence_item_version)
            if ev is not None and ev.acceptance == "ACCEPTED":
                return True
        return False

    @staticmethod
    def _confirmation_state(issue: Issue) -> str:
        if issue.status == "REJECTED":
            return "REJECTED"
        if issue.status == "SUPERSEDED":
            return "SUPERSEDED"
        if issue.status == "CANDIDATE":
            return "AI_CANDIDATE" if issue.source_type == "AI_PROPOSED" else "CANDIDATE"
        if issue.source_type == "LAWYER_CREATED":
            return "LAWYER_CREATED"
        if issue.source_type == "LAWYER_REFINED":
            return "LAWYER_REFINED"
        return "LAWYER_CONFIRMED"

    def _legal_theories(self, case_id: UUID) -> list[dict]:
        rows = list(
            self.session.scalars(
                select(LegalTheory)
                .where(LegalTheory.case_id == case_id)
                .order_by(LegalTheory.created_at.asc())
            )
        )
        out: list[dict] = []
        for row in rows:
            supporting = row.supporting_fact_ids or []
            sanitized = self._sanitize_theory_fact_refs(case_id, supporting)
            out.append(
                {
                    "id": str(row.id),
                    "theory_summary": row.theory_summary,
                    "layer": row.layer,
                    "supporting_fact_ids": sanitized,
                    "unsupported_fact_refs_removed": len(supporting) - len(sanitized),
                    "stale": row.stale,
                }
            )
        return out

    def _sanitize_theory_fact_refs(
        self, case_id: UUID, refs: list
    ) -> list[str]:
        """Only expose confirmed fact keys belonging to this case."""
        confirmed: list[str] = []
        for raw in refs:
            key = UUID(str(raw))
            fact = self.repo.get_current_fact(key)
            if (
                fact is not None
                and fact.case_id == case_id
                and fact.status == "CONFIRMED"
            ):
                confirmed.append(str(key))
        return confirmed
