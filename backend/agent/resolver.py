"""Resolve user-facing labels (证据2 / 事实1) → stable refs."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.agent.dto import AgentError, AgentErrorCode
from backend.models import ClaimDirection, DocumentDraft, EvidenceItem, Fact


class TargetResolver:
    def __init__(self, session: Session, *, case_id: UUID) -> None:
        self.session = session
        self.case_id = case_id

    def resolve_evidence_by_id(self, evidence_item_id: UUID) -> EvidenceItem:
        item = self.session.scalars(
            select(EvidenceItem).where(
                EvidenceItem.id == evidence_item_id,
                EvidenceItem.is_current.is_(True),
            )
        ).first()
        if item is None:
            raise AgentError("证据不存在", code=AgentErrorCode.NOT_FOUND)
        if item.case_id != self.case_id:
            raise AgentError(
                "跨案件证据不可操作",
                code=AgentErrorCode.VALIDATION_ERROR,
            )
        return item

    def resolve_evidence_by_numbers(self, numbers: list[str]) -> list[EvidenceItem]:
        items: list[EvidenceItem] = []
        for num in numbers:
            matches = list(
                self.session.scalars(
                    select(EvidenceItem).where(
                        EvidenceItem.case_id == self.case_id,
                        EvidenceItem.is_current.is_(True),
                        EvidenceItem.number == str(num),
                    )
                )
            )
            if not matches:
                raise AgentError(
                    f"未找到证据编号 {num}",
                    code=AgentErrorCode.NOT_FOUND,
                )
            if len(matches) > 1:
                raise AgentError(
                    f"证据编号 {num} 存在歧义，请改用唯一标识",
                    code=AgentErrorCode.AMBIGUOUS_TARGET,
                )
            items.append(matches[0])
        return items

    def resolve_fact_by_display_index(self, index: int) -> Fact:
        """1-based index over current CANDIDATE facts ordered by created_at."""
        facts = list(
            self.session.scalars(
                select(Fact)
                .where(
                    Fact.case_id == self.case_id,
                    Fact.is_current.is_(True),
                    Fact.status == "CANDIDATE",
                )
                .order_by(Fact.created_at.asc(), Fact.fact_key.asc())
            )
        )
        if index < 1 or index > len(facts):
            # Also allow indexing CONFIRMED current facts for amend
            all_current = list(
                self.session.scalars(
                    select(Fact)
                    .where(
                        Fact.case_id == self.case_id,
                        Fact.is_current.is_(True),
                        Fact.status.in_(["CANDIDATE", "CONFIRMED"]),
                    )
                    .order_by(Fact.created_at.asc(), Fact.fact_key.asc())
                )
            )
            if index < 1 or index > len(all_current):
                raise AgentError(
                    f"事实编号 {index} 不存在或有歧义",
                    code=AgentErrorCode.NOT_FOUND,
                )
            return all_current[index - 1]
        return facts[index - 1]

    def resolve_party_by_display_index(self, index: int, *, role: str | None = None):
        from backend.models import CaseParty

        parties = list(
            self.session.scalars(
                select(CaseParty)
                .where(
                    CaseParty.case_id == self.case_id,
                    CaseParty.is_current.is_(True),
                )
                .order_by(CaseParty.created_at.asc(), CaseParty.party_key.asc())
            )
        )
        if role:
            parties = [p for p in parties if p.role == role]
        if index < 1 or index > len(parties):
            raise AgentError(
                f"当事人编号 {index} 不存在",
                code=AgentErrorCode.NOT_FOUND,
            )
        return parties[index - 1]

    def resolve_claim_candidate(self, index: int | None = None) -> ClaimDirection:
        claims = list(
            self.session.scalars(
                select(ClaimDirection)
                .where(
                    ClaimDirection.case_id == self.case_id,
                    ClaimDirection.is_current.is_(True),
                    ClaimDirection.status == "CANDIDATE",
                )
                .order_by(
                    ClaimDirection.created_at.asc(),
                    ClaimDirection.claim_direction_key.asc(),
                )
            )
        )
        if not claims:
            # allow confirmed for amend
            claims = list(
                self.session.scalars(
                    select(ClaimDirection).where(
                        ClaimDirection.case_id == self.case_id,
                        ClaimDirection.is_current.is_(True),
                        ClaimDirection.status == "CONFIRMED",
                        ClaimDirection.stale.is_(False),
                    )
                )
            )
        if not claims:
            raise AgentError("没有可操作的诉讼请求", code=AgentErrorCode.NOT_FOUND)
        if index is None:
            if len(claims) > 1:
                raise AgentError(
                    f"当前有 {len(claims)} 条诉讼请求候选，请指定编号",
                    code=AgentErrorCode.AMBIGUOUS_TARGET,
                )
            return claims[0]
        if index < 1 or index > len(claims):
            raise AgentError(
                f"诉讼请求编号 {index} 不存在",
                code=AgentErrorCode.NOT_FOUND,
            )
        return claims[index - 1]

    def resolve_reviewable_draft(self) -> DocumentDraft:
        draft = self.session.scalars(
            select(DocumentDraft)
            .where(
                DocumentDraft.case_id == self.case_id,
                DocumentDraft.doc_type == "CIVIL_COMPLAINT",
                DocumentDraft.status.in_(["DRAFT", "IN_REVIEW"]),
            )
            .order_by(DocumentDraft.version.desc())
        ).first()
        if draft is None:
            raise AgentError("没有可审核的起诉状草稿", code=AgentErrorCode.NOT_FOUND)
        return draft
