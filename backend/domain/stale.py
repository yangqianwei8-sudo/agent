"""Stale / dependency invalidation rules (blueprint §5)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.domain.enums import LinkStatus, StaleEvent, StaleReason
from backend.models import (
    CaseParty,
    ClaimDirection,
    DocumentDraft,
    EvidenceItem,
    Fact,
    FactEvidenceLink,
    Issue,
    LegalTheory,
    TimelineEvent,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _mark_stale(obj: Any, reason: StaleReason) -> None:
    obj.stale = True
    obj.stale_reason = reason.value
    obj.stale_at = _now()


def _party_name_in_statement(statement: str, names: set[str]) -> bool:
    return any(name and name in statement for name in names)


def invalidate_dependencies(
    session: Session,
    event: StaleEvent,
    *,
    case_id: UUID,
    payload: dict[str, Any],
) -> list[str]:
    """Apply directed stale rules. Returns human-readable affected descriptors."""
    affected: list[str] = []

    if event == StaleEvent.PARTY_CHANGED:
        affected.extend(_party_changed(session, case_id, payload))
    elif event == StaleEvent.FACT_REJECTED:
        affected.extend(_fact_rejected(session, case_id, payload))
    elif event == StaleEvent.FACT_AMENDED:
        affected.extend(_fact_amended(session, case_id, payload))
    elif event == StaleEvent.CLAIM_DIRECTION_CHANGED:
        affected.extend(_claim_changed(session, case_id, payload))
    elif event in {StaleEvent.CLAIM_CHANGED, StaleEvent.ISSUE_CHANGED}:
        affected.extend(_formal_input_changed(session, case_id, payload, event))
    elif event == StaleEvent.EVIDENCE_EXCLUDED:
        affected.extend(_evidence_excluded(session, case_id, payload))
    elif event == StaleEvent.EXTRACTED_CONTENT_REBUILT:
        affected.extend(_extract_rebuilt(session, case_id, payload))
    else:
        raise ValueError(f"Unknown stale event: {event}")

    session.flush()
    return affected


def _party_changed(session: Session, case_id: UUID, payload: dict[str, Any]) -> list[str]:
    affected: list[str] = []
    old_name = payload.get("old_name") or ""
    old_party_key = payload.get("old_party_key")
    names = {old_name} if old_name else set()

    facts = session.scalars(
        select(Fact).where(Fact.case_id == case_id, Fact.is_current.is_(True))
    ).all()
    for fact in facts:
        touches = _party_name_in_statement(fact.statement, names)
        if touches:
            _mark_stale(fact, StaleReason.PARTY_CHANGED)
            affected.append(f"fact:{fact.fact_key}")

    for model in (Issue, LegalTheory, ClaimDirection):
        rows = session.scalars(select(model).where(model.case_id == case_id)).all()
        for row in rows:
            if getattr(row, "is_current", True) is False:
                continue
            _mark_stale(row, StaleReason.PARTY_CHANGED)
            affected.append(f"{model.__tablename__}:{row.id}")

    drafts = session.scalars(
        select(DocumentDraft).where(
            DocumentDraft.case_id == case_id,
            DocumentDraft.status.in_(["DRAFT", "IN_REVIEW", "APPROVED_BY_LAWYER"]),
        )
    ).all()
    for draft in drafts:
        draft.status = "STALE"
        draft.stale_reason = StaleReason.PARTY_CHANGED.value
        draft.updated_at = _now()
        affected.append(f"draft:{draft.id}")

    # Evidence / materials / spans intentionally not stale.
    _ = old_party_key
    return affected


def _fact_rejected(session: Session, case_id: UUID, payload: dict[str, Any]) -> list[str]:
    affected: list[str] = []
    fact_id = payload["fact_id"]
    fact_key = payload["fact_key"]

    links = session.scalars(
        select(FactEvidenceLink).where(FactEvidenceLink.fact_id == fact_id)
    ).all()
    for link in links:
        link.status = LinkStatus.VOID.value
        affected.append(f"link:{link.id}")

    for model in (TimelineEvent, Issue):
        rows = session.scalars(select(model).where(model.case_id == case_id)).all()
        for row in rows:
            if getattr(row, "is_current", True) is False:
                continue
            related = row.related_fact_ids or []
            related_keys = {str(x) for x in related}
            if str(fact_key) in related_keys or (
                getattr(row, "fact_key", None) is not None and row.fact_key == fact_key
            ):
                _mark_stale(row, StaleReason.FACT_REJECTED)
                affected.append(f"{model.__tablename__}:{row.id}")

    theories = session.scalars(select(LegalTheory).where(LegalTheory.case_id == case_id)).all()
    for theory in theories:
        supporting = {str(x) for x in (theory.supporting_fact_ids or [])}
        if str(fact_key) in supporting:
            _mark_stale(theory, StaleReason.FACT_REJECTED)
            affected.append(f"legal_theory:{theory.id}")

    claims = session.scalars(
        select(ClaimDirection).where(
            ClaimDirection.case_id == case_id, ClaimDirection.is_current.is_(True)
        )
    ).all()
    for claim in claims:
        payload_claims = (claim.payload or {}).get("claims") or []
        supporting: set[str] = set()
        for item in payload_claims:
            supporting.update(str(x) for x in (item.get("supporting_fact_ids") or []))
        if str(fact_key) in supporting:
            _mark_stale(claim, StaleReason.FACT_REJECTED)
            affected.append(f"claim_direction:{claim.id}")

    _stale_drafts_citing_fact(session, case_id, fact_key, StaleReason.FACT_REJECTED, affected)
    return affected


def _fact_amended(session: Session, case_id: UUID, payload: dict[str, Any]) -> list[str]:
    affected: list[str] = []
    old_fact_id = payload["old_fact_id"]
    old_fact_key = payload["fact_key"]
    new_fact_id = payload.get("new_fact_id")

    old_links = session.scalars(
        select(FactEvidenceLink).where(FactEvidenceLink.fact_id == old_fact_id)
    ).all()
    for link in old_links:
        link.status = LinkStatus.VOID.value
        affected.append(f"link_void:{link.id}")
        if new_fact_id:
            session.add(
                FactEvidenceLink(
                    fact_id=new_fact_id,
                    evidence_item_id=link.evidence_item_id,
                    evidence_item_version=link.evidence_item_version,
                    source_span_id=link.source_span_id,
                    link_role=link.link_role,
                    explanation=link.explanation,
                    status=LinkStatus.ACTIVE.value,
                )
            )

    claims = session.scalars(
        select(ClaimDirection).where(
            ClaimDirection.case_id == case_id, ClaimDirection.is_current.is_(True)
        )
    ).all()
    for claim in claims:
        payload_claims = (claim.payload or {}).get("claims") or []
        supporting: set[str] = set()
        for item in payload_claims:
            supporting.update(str(x) for x in (item.get("supporting_fact_ids") or []))
        if str(old_fact_key) in supporting:
            _mark_stale(claim, StaleReason.FACT_AMENDED)
            affected.append(f"claim_direction:{claim.id}")

    _stale_drafts_citing_fact(session, case_id, old_fact_key, StaleReason.FACT_AMENDED, affected)
    return affected


def _formal_input_changed(
    session: Session,
    case_id: UUID,
    payload: dict[str, Any],
    event: StaleEvent,
) -> list[str]:
    """Mark active drafts stale when confirmed Claim/Issue versions change."""
    _ = payload
    reason = (
        StaleReason.CLAIM_CHANGED
        if event == StaleEvent.CLAIM_CHANGED
        else StaleReason.ISSUE_CHANGED
    )
    affected: list[str] = []
    drafts = session.scalars(
        select(DocumentDraft).where(
            DocumentDraft.case_id == case_id,
            DocumentDraft.status.in_(["DRAFT", "IN_REVIEW", "APPROVED_BY_LAWYER"]),
        )
    ).all()
    for draft in drafts:
        draft.status = "STALE"
        draft.stale_reason = reason.value
        draft.updated_at = _now()
        affected.append(f"draft:{draft.id}")
    return affected


def _claim_changed(session: Session, case_id: UUID, payload: dict[str, Any]) -> list[str]:
    affected: list[str] = []
    _ = payload
    theories = session.scalars(select(LegalTheory).where(LegalTheory.case_id == case_id)).all()
    for theory in theories:
        _mark_stale(theory, StaleReason.CLAIM_DIRECTION_CHANGED)
        affected.append(f"legal_theory:{theory.id}")

    drafts = session.scalars(
        select(DocumentDraft).where(
            DocumentDraft.case_id == case_id,
            DocumentDraft.status.in_(["DRAFT", "IN_REVIEW", "APPROVED_BY_LAWYER"]),
        )
    ).all()
    for draft in drafts:
        draft.status = "STALE"
        draft.stale_reason = StaleReason.CLAIM_DIRECTION_CHANGED.value
        draft.updated_at = _now()
        affected.append(f"draft:{draft.id}")
    return affected


def _evidence_excluded(session: Session, case_id: UUID, payload: dict[str, Any]) -> list[str]:
    affected: list[str] = []
    evidence_id = payload["evidence_item_id"]
    evidence_version = payload.get("evidence_item_version")

    links = session.scalars(
        select(FactEvidenceLink).where(
            FactEvidenceLink.evidence_item_id == evidence_id,
            FactEvidenceLink.status == LinkStatus.ACTIVE.value,
        )
    ).all()
    touched_fact_ids: set[UUID] = set()
    for link in links:
        if evidence_version is not None and link.evidence_item_version != evidence_version:
            continue
        link.status = LinkStatus.VOID.value
        touched_fact_ids.add(link.fact_id)
        affected.append(f"link:{link.id}")

    session.flush()

    for fact_id in touched_fact_ids:
        fact = session.get(Fact, fact_id)
        if fact is None:
            continue
        active = session.scalars(
            select(FactEvidenceLink).where(
                FactEvidenceLink.fact_id == fact.id,
                FactEvidenceLink.status == LinkStatus.ACTIVE.value,
            )
        ).all()
        if not active:
            _mark_stale(fact, StaleReason.EVIDENCE_EXCLUDED)
            affected.append(f"fact:{fact.fact_key}")

    drafts = session.scalars(
        select(DocumentDraft).where(DocumentDraft.case_id == case_id)
    ).all()
    from backend.models import DraftCitation

    for draft in drafts:
        if draft.status == "STALE":
            continue
        cites = session.scalars(
            select(DraftCitation).where(
                DraftCitation.draft_id == draft.id,
                DraftCitation.evidence_item_id == evidence_id,
            )
        ).all()
        if cites:
            draft.status = "STALE"
            draft.stale_reason = StaleReason.EVIDENCE_EXCLUDED.value
            draft.updated_at = _now()
            affected.append(f"draft:{draft.id}")
    return affected


def _extract_rebuilt(session: Session, case_id: UUID, payload: dict[str, Any]) -> list[str]:
    affected: list[str] = []
    old_extracted_content_id = payload["old_extracted_content_id"]
    from backend.models import EvidenceItemSpan, SourceSpan

    spans = session.scalars(
        select(SourceSpan).where(SourceSpan.extracted_content_id == old_extracted_content_id)
    ).all()
    span_ids = {s.id for s in spans}
    if not span_ids:
        return affected

    item_spans = session.scalars(
        select(EvidenceItemSpan).where(EvidenceItemSpan.source_span_id.in_(span_ids))
    ).all()
    keys = {(es.evidence_item_id, es.evidence_item_version) for es in item_spans}
    for eid, ever in keys:
        item = session.scalars(
            select(EvidenceItem).where(
                EvidenceItem.id == eid,
                EvidenceItem.version == ever,
                EvidenceItem.case_id == case_id,
            )
        ).first()
        if item and item.is_current:
            _mark_stale(item, StaleReason.EXTRACT_REBUILT)
            affected.append(f"evidence:{item.id}@v{item.version}")
    return affected


def _stale_drafts_citing_fact(
    session: Session,
    case_id: UUID,
    fact_key: UUID,
    reason: StaleReason,
    affected: list[str],
) -> None:
    from backend.models import DraftCitation

    drafts = session.scalars(
        select(DocumentDraft).where(
            DocumentDraft.case_id == case_id,
            DocumentDraft.status.in_(["DRAFT", "IN_REVIEW", "APPROVED_BY_LAWYER"]),
        )
    ).all()
    for draft in drafts:
        cites = session.scalars(
            select(DraftCitation).where(
                DraftCitation.draft_id == draft.id,
                DraftCitation.fact_key == fact_key,
            )
        ).all()
        if cites:
            draft.status = "STALE"
            draft.stale_reason = reason.value
            draft.updated_at = _now()
            affected.append(f"draft:{draft.id}")


def party_mentions_name(party: CaseParty, statement: str) -> bool:
    return party.name in statement
