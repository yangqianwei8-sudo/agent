"""Domain Service — sole writer of domain truth (Phase 2)."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from backend.domain.enums import (
    CaseStatus,
    DecisionResult,
    DraftStatus,
    EvidenceAcceptance,
    FactStatus,
    LayerStatus,
    MaterialLifeStatus,
    ParseStatus,
    StaleEvent,
)
from backend.domain.errors import ConflictError, ImmutableError, NotFoundError, ValidationError
from backend.domain.stale import invalidate_dependencies
from backend.models import (
    AuditLog,
    Case,
    CaseMaterial,
    CaseParty,
    ClaimDirection,
    DocumentDraft,
    DraftCitation,
    EvidenceItem,
    EvidenceItemSpan,
    ExtractedContent,
    Fact,
    FactEvidenceLink,
    HumanDecision,
    SourceSpan,
)
from backend.repositories.base import Repository
from backend.schemas.claim_direction import validate_claim_direction_payload


def _now() -> datetime:
    return datetime.now(UTC)


def _hash_quote(quote: str) -> str:
    return hashlib.sha256(quote.encode("utf-8")).hexdigest()


def _confirmation_set_hash(parts: list[str]) -> str:
    blob = "|".join(sorted(parts))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class DomainService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = Repository(session)

    # ----- Case -----

    def create_case(
        self,
        *,
        title: str,
        owner_user_id: UUID,
        goal_summary: str | None = None,
        actor_id: UUID | None = None,
    ) -> Case:
        case = Case(
            title=title,
            owner_user_id=owner_user_id,
            goal_summary=goal_summary,
            status=CaseStatus.OPEN.value,
        )
        self.repo.add(case)
        self.repo.flush()
        self._audit(
            actor_id or owner_user_id,
            "create_case",
            "cases",
            case.id,
            case_id=case.id,
            after={"title": title, "status": case.status},
        )
        return case

    def archive_case(self, case_id: UUID, *, actor_id: UUID) -> Case:
        case = self._require_case(case_id)
        if case.status == CaseStatus.ARCHIVED.value:
            raise ConflictError("case already archived")
        before = {"status": case.status}
        case.status = CaseStatus.ARCHIVED.value
        case.archived_at = _now()
        case.updated_at = _now()
        self._audit(actor_id, "archive_case", "cases", case.id, case_id=case.id, before=before)
        return case

    # ----- Material -----

    def register_material(
        self,
        *,
        case_id: UUID,
        filename: str,
        mime: str,
        byte_size: int,
        content_hash: str,
        storage_key: str,
        created_by: UUID,
    ) -> CaseMaterial:
        self._require_case(case_id)
        material = CaseMaterial(
            case_id=case_id,
            filename=filename,
            mime=mime,
            byte_size=byte_size,
            content_hash=content_hash,
            storage_key=storage_key,
            life_status=MaterialLifeStatus.ACTIVE.value,
            parse_status=ParseStatus.PENDING.value,
            created_by=created_by,
        )
        self.repo.add(material)
        self.repo.flush()
        self._audit(
            created_by,
            "register_material",
            "case_materials",
            material.id,
            case_id=case_id,
            after={"filename": filename, "content_hash": content_hash},
        )
        return material

    def void_material(
        self,
        material_id: UUID,
        *,
        reason: str,
        actor_id: UUID,
        decision: HumanDecision | None = None,
    ) -> CaseMaterial:
        material = self.repo.get_material(material_id)
        if material is None:
            raise NotFoundError("material not found")
        if material.life_status == MaterialLifeStatus.VOID.value:
            raise ConflictError("material already void")
        decision = decision or self._new_decision(
            case_id=material.case_id,
            actor_id=actor_id,
            decision_type="VOID_MATERIAL",
            target_type="CaseMaterial",
            target_id=material.id,
            result=DecisionResult.CONFIRMED.value,
            payload={"reason": reason},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        material.life_status = MaterialLifeStatus.VOID.value
        material.void_reason = reason
        material.updated_at = _now()
        self._audit(
            actor_id,
            "void_material",
            "case_materials",
            material.id,
            case_id=material.case_id,
            after={"life_status": material.life_status, "decision_id": str(decision.id)},
        )
        return material

    def create_extracted_content(
        self,
        *,
        material_id: UUID,
        extraction_method: str,
        extraction_version: str,
        full_text: str | None = None,
        full_text_ref: str | None = None,
        page_count: int | None = None,
        layout_json: dict[str, Any] | None = None,
        error_detail: str | None = None,
        status: str = "SUCCEEDED",
        actor_id: UUID | None = None,
        previous_extracted_content_id: UUID | None = None,
    ) -> ExtractedContent:
        material = self.repo.get_material(material_id)
        if material is None:
            raise NotFoundError("material not found")
        if material.life_status != MaterialLifeStatus.ACTIVE.value:
            raise ConflictError("material is not ACTIVE")
        if status == "SUCCEEDED" and full_text is None:
            raise ValidationError("SUCCEEDED ExtractedContent requires full_text")
        ec = ExtractedContent(
            material_id=material_id,
            extraction_method=extraction_method,
            extraction_version=extraction_version,
            status=status,
            full_text=full_text if status == "SUCCEEDED" else None,
            full_text_ref=full_text_ref,
            page_count=page_count,
            layout_json=layout_json if status == "SUCCEEDED" else None,
            error_detail=(error_detail or "")[:500] if error_detail else None,
        )
        self.repo.add(ec)
        self.repo.flush()
        if status == "SUCCEEDED":
            material.parse_status = ParseStatus.SUCCEEDED.value
        elif status == "FAILED":
            material.parse_status = ParseStatus.FAILED.value
        material.updated_at = _now()
        if previous_extracted_content_id is not None:
            invalidate_dependencies(
                self.session,
                StaleEvent.EXTRACTED_CONTENT_REBUILT,
                case_id=material.case_id,
                payload={"old_extracted_content_id": previous_extracted_content_id},
            )
        self._audit(
            actor_id or material.created_by,
            "create_extracted_content",
            "extracted_contents",
            ec.id,
            case_id=material.case_id,
            after={
                "method": extraction_method,
                "version": extraction_version,
                "status": status,
            },
        )
        return ec

    def create_source_span(
        self,
        *,
        material_id: UUID,
        extracted_content_id: UUID,
        character_start: int,
        character_end: int,
        quote: str,
        extraction_method: str,
        extraction_version: str,
        page: int | None = None,
        paragraph: int | None = None,
        bbox_json: dict[str, Any] | None = None,
        confidence: float | None = None,
        quote_hash: str | None = None,
    ) -> SourceSpan:
        if character_start < 0 or character_end <= character_start:
            raise ValidationError("invalid character offsets")
        ec = self.repo.get_extracted_content(extracted_content_id)
        if ec is None:
            raise NotFoundError("extracted_content not found")
        if ec.material_id != material_id:
            raise ValidationError("material_id does not match extracted_content")
        if ec.status != "SUCCEEDED":
            raise ValidationError(
                "FAILED ExtractedContent cannot produce trusted SourceSpan"
            )
        if ec.full_text is None:
            raise ValidationError("ExtractedContent has no full_text")
        expected = quote_hash or _hash_quote(quote)
        if quote_hash and quote_hash != _hash_quote(quote):
            raise ValidationError("quote_hash mismatch")
        # Coordinates are valid only within this extracted_content_id text space.
        if character_end > len(ec.full_text):
            raise ValidationError("offsets exceed extracted_content text length")
        slice_text = ec.full_text[character_start:character_end]
        if slice_text != quote:
            raise ValidationError("quote does not match extracted_content slice")
        span = SourceSpan(
            material_id=material_id,
            extracted_content_id=extracted_content_id,
            page=page,
            paragraph=paragraph,
            character_start=character_start,
            character_end=character_end,
            quote=quote,
            quote_hash=expected,
            bbox_json=bbox_json,
            extraction_method=extraction_method,
            extraction_version=extraction_version,
            confidence=confidence,
        )
        self.repo.add(span)
        self.repo.flush()
        return span

    # ----- Evidence -----

    def create_evidence_item(
        self,
        *,
        case_id: UUID,
        number: str,
        title: str,
        category: str,
        summary: str | None = None,
        source_span_ids: list[UUID] | None = None,
        evidence_id: UUID | None = None,
        actor_id: UUID | None = None,
        organizer_run_id: UUID | None = None,
    ) -> EvidenceItem:
        self._require_case(case_id)
        eid = evidence_id or uuid.uuid4()
        item = EvidenceItem(
            id=eid,
            case_id=case_id,
            version=1,
            is_current=True,
            number=number,
            title=title,
            category=category,
            summary=summary,
            acceptance=EvidenceAcceptance.PENDING.value,
            organizer_run_id=organizer_run_id,
        )
        self.repo.add(item)
        self.repo.flush()
        for span_id in source_span_ids or []:
            span = self.repo.get_source_span(span_id)
            if span is None:
                raise NotFoundError(f"source_span not found: {span_id}")
            self.repo.add(
                EvidenceItemSpan(
                    evidence_item_id=item.id,
                    evidence_item_version=item.version,
                    source_span_id=span_id,
                    role_in_item="PRIMARY",
                )
            )
        self.repo.flush()
        self._audit(
            actor_id or uuid.UUID(int=0),
            "create_evidence_item",
            "evidence_items",
            item.row_id,
            case_id=case_id,
            after={
                "id": str(item.id),
                "version": item.version,
                "number": number,
                "acceptance": item.acceptance,
                "organizer_run_id": str(organizer_run_id) if organizer_run_id else None,
            },
        )
        return item

    def accept_evidence(
        self,
        evidence_id: UUID,
        *,
        actor_id: UUID,
        decision: HumanDecision | None = None,
    ) -> EvidenceItem:
        item = self._require_current_evidence(evidence_id)
        decision = decision or self._new_decision(
            case_id=item.case_id,
            actor_id=actor_id,
            decision_type="ACCEPT_EVIDENCE",
            target_type="EvidenceItem",
            target_id=item.id,
            result=DecisionResult.CONFIRMED.value,
            payload={"evidence_item_id": str(item.id), "version": item.version},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        item.acceptance = EvidenceAcceptance.ACCEPTED.value
        item.updated_at = _now()
        self._audit(
            actor_id,
            "accept_evidence",
            "evidence_items",
            item.row_id,
            case_id=item.case_id,
            after={"acceptance": item.acceptance, "decision_id": str(decision.id)},
        )
        return item

    def exclude_evidence(
        self,
        evidence_id: UUID,
        *,
        actor_id: UUID,
        decision: HumanDecision | None = None,
    ) -> EvidenceItem:
        item = self._require_current_evidence(evidence_id)
        decision = decision or self._new_decision(
            case_id=item.case_id,
            actor_id=actor_id,
            decision_type="EXCLUDE_EVIDENCE",
            target_type="EvidenceItem",
            target_id=item.id,
            result=DecisionResult.REJECTED.value,
            payload={"evidence_item_id": str(item.id), "version": item.version},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        item.acceptance = EvidenceAcceptance.EXCLUDED.value
        item.updated_at = _now()
        invalidate_dependencies(
            self.session,
            StaleEvent.EVIDENCE_EXCLUDED,
            case_id=item.case_id,
            payload={
                "evidence_item_id": item.id,
                "evidence_item_version": item.version,
            },
        )
        self._audit(
            actor_id,
            "exclude_evidence",
            "evidence_items",
            item.row_id,
            case_id=item.case_id,
            after={"acceptance": item.acceptance, "decision_id": str(decision.id)},
        )
        return item

    def amend_evidence_item(
        self,
        evidence_id: UUID,
        *,
        actor_id: UUID,
        number: str | None = None,
        title: str | None = None,
        summary: str | None = None,
        category: str | None = None,
        source_span_ids: list[UUID] | None = None,
        decision: HumanDecision | None = None,
    ) -> EvidenceItem:
        current = self._require_current_evidence(evidence_id)
        content_changed = any(v is not None for v in (title, summary, category, source_span_ids))
        number_only = number is not None and not content_changed

        if number_only:
            current.number = number  # type: ignore[assignment]
            current.updated_at = _now()
            self._audit(
                actor_id,
                "renumber_evidence_item",
                "evidence_items",
                current.row_id,
                case_id=current.case_id,
                after={"number": current.number, "version": current.version},
            )
            return current

        if not content_changed and number is None:
            raise ValidationError("no amendments provided")

        decision = decision or self._new_decision(
            case_id=current.case_id,
            actor_id=actor_id,
            decision_type="AMEND_EVIDENCE",
            target_type="EvidenceItem",
            target_id=current.id,
            result=DecisionResult.AMENDED.value,
            payload={"from_version": current.version},
        )
        self.repo.add_decision(decision)
        self.repo.flush()

        current.is_current = False
        current.updated_at = _now()
        new_version = current.version + 1
        new_item = EvidenceItem(
            id=current.id,
            case_id=current.case_id,
            version=new_version,
            is_current=True,
            number=number if number is not None else current.number,
            title=title if title is not None else current.title,
            category=category if category is not None else current.category,
            summary=summary if summary is not None else current.summary,
            acceptance=current.acceptance,
        )
        self.repo.add(new_item)
        self.repo.flush()

        spans = (
            source_span_ids
            if source_span_ids is not None
            else [
                s.source_span_id
                for s in self.repo.list_evidence_spans(current.id, current.version)
            ]
        )
        for span_id in spans:
            if self.repo.get_source_span(span_id) is None:
                raise NotFoundError(f"source_span not found: {span_id}")
            self.repo.add(
                EvidenceItemSpan(
                    evidence_item_id=new_item.id,
                    evidence_item_version=new_item.version,
                    source_span_id=span_id,
                    role_in_item="PRIMARY",
                )
            )
        self.repo.flush()
        self._audit(
            actor_id,
            "amend_evidence_item",
            "evidence_items",
            new_item.row_id,
            case_id=current.case_id,
            after={
                "id": str(new_item.id),
                "version": new_item.version,
                "decision_id": str(decision.id),
            },
        )
        return new_item

    # ----- Fact -----

    def propose_fact(
        self,
        *,
        case_id: UUID,
        statement: str,
        evidence_links: list[dict[str, Any]],
        importance: str = "SUPPORTING",
        actor_id: UUID | None = None,
        analyst_run_id: UUID | None = None,
    ) -> Fact:
        self._require_case(case_id)
        if not evidence_links:
            raise ValidationError("propose_fact requires at least one evidence link")
        fact = Fact(
            fact_key=uuid.uuid4(),
            case_id=case_id,
            statement=statement,
            status=FactStatus.CANDIDATE.value,
            importance=importance,
            version=1,
            is_current=True,
            analyst_run_id=analyst_run_id,
        )
        self.repo.add(fact)
        self.repo.flush()
        for link in evidence_links:
            eid = UUID(str(link["evidence_item_id"]))
            ever = int(link["evidence_item_version"])
            if self.repo.get_evidence_version(eid, ever) is None:
                raise NotFoundError("evidence version not found for link")
            self.repo.add(
                FactEvidenceLink(
                    fact_id=fact.id,
                    evidence_item_id=eid,
                    evidence_item_version=ever,
                    source_span_id=(
                        UUID(str(link["source_span_id"])) if link.get("source_span_id") else None
                    ),
                    link_role=link.get("link_role", "PROVES"),
                    explanation=link.get("explanation"),
                    status="ACTIVE",
                )
            )
        self.repo.flush()
        self._audit(
            actor_id or uuid.UUID(int=0),
            "propose_fact",
            "facts",
            fact.id,
            case_id=case_id,
            after={
                "fact_key": str(fact.fact_key),
                "version": fact.version,
                "status": fact.status,
                "analyst_run_id": str(analyst_run_id) if analyst_run_id else None,
            },
        )
        return fact

    def confirm_fact(
        self,
        fact_key: UUID,
        *,
        actor_id: UUID,
        decision: HumanDecision | None = None,
    ) -> Fact:
        fact = self._require_current_fact(fact_key)
        if fact.status != FactStatus.CANDIDATE.value:
            raise ConflictError("only CANDIDATE facts can be confirmed")
        if fact.stale:
            raise ConflictError("cannot confirm stale fact")
        active_links = [lnk for lnk in self.repo.list_fact_links(fact.id) if lnk.status == "ACTIVE"]
        if not active_links:
            raise ValidationError("cannot confirm fact without ACTIVE links")
        decision = decision or self._new_decision(
            case_id=fact.case_id,
            actor_id=actor_id,
            decision_type="CONFIRM_FACT",
            target_type="Fact",
            target_id=fact.fact_key,
            result=DecisionResult.CONFIRMED.value,
            payload={"fact_key": str(fact.fact_key), "version": fact.version},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        fact.status = FactStatus.CONFIRMED.value
        fact.confirm_decision_id = decision.id
        fact.updated_at = _now()
        self._audit(
            actor_id,
            "confirm_fact",
            "facts",
            fact.id,
            case_id=fact.case_id,
            after={
                "status": fact.status,
                "confirm_decision_id": str(decision.id),
                "fact_key": str(fact.fact_key),
                "version": fact.version,
            },
        )
        return fact

    def reject_fact(
        self,
        fact_key: UUID,
        *,
        actor_id: UUID,
        decision: HumanDecision | None = None,
    ) -> Fact:
        fact = self._require_current_fact(fact_key)
        if fact.status not in {FactStatus.CANDIDATE.value, FactStatus.CONFIRMED.value}:
            raise ConflictError("fact cannot be rejected from current status")
        decision = decision or self._new_decision(
            case_id=fact.case_id,
            actor_id=actor_id,
            decision_type="REJECT_FACT",
            target_type="Fact",
            target_id=fact.fact_key,
            result=DecisionResult.REJECTED.value,
            payload={"fact_key": str(fact.fact_key), "version": fact.version},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        fact.status = FactStatus.REJECTED.value
        fact.is_current = True  # rejected remains current identity terminal
        fact.updated_at = _now()
        invalidate_dependencies(
            self.session,
            StaleEvent.FACT_REJECTED,
            case_id=fact.case_id,
            payload={"fact_id": fact.id, "fact_key": fact.fact_key},
        )
        self._audit(
            actor_id,
            "reject_fact",
            "facts",
            fact.id,
            case_id=fact.case_id,
            after={"status": fact.status, "decision_id": str(decision.id)},
        )
        return fact

    def amend_fact(
        self,
        fact_key: UUID,
        *,
        new_statement: str,
        actor_id: UUID,
        decision: HumanDecision | None = None,
    ) -> Fact:
        old = self._require_current_fact(fact_key)
        if old.status != FactStatus.CONFIRMED.value:
            raise ConflictError("only CONFIRMED facts can be amended")
        decision = decision or self._new_decision(
            case_id=old.case_id,
            actor_id=actor_id,
            decision_type="AMEND_FACT",
            target_type="Fact",
            target_id=old.fact_key,
            result=DecisionResult.AMENDED.value,
            payload={"from_version": old.version, "new_statement": new_statement},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        old.status = FactStatus.SUPERSEDED.value
        old.is_current = False
        old.updated_at = _now()
        new_fact = Fact(
            fact_key=old.fact_key,
            case_id=old.case_id,
            statement=new_statement,
            status=FactStatus.CONFIRMED.value,
            importance=old.importance,
            version=old.version + 1,
            is_current=True,
            supersedes_id=old.id,
            confirm_decision_id=decision.id,
        )
        self.repo.add(new_fact)
        self.repo.flush()
        invalidate_dependencies(
            self.session,
            StaleEvent.FACT_AMENDED,
            case_id=old.case_id,
            payload={
                "old_fact_id": old.id,
                "new_fact_id": new_fact.id,
                "fact_key": old.fact_key,
            },
        )
        self._audit(
            actor_id,
            "amend_fact",
            "facts",
            new_fact.id,
            case_id=old.case_id,
            after={
                "fact_key": str(new_fact.fact_key),
                "version": new_fact.version,
                "decision_id": str(decision.id),
            },
        )
        return new_fact

    # ----- Party -----

    def create_party(
        self,
        *,
        case_id: UUID,
        role: str,
        name: str,
        party_type: str,
        actor_id: UUID | None = None,
        identifiers_json: dict[str, Any] | None = None,
    ) -> CaseParty:
        self._require_case(case_id)
        party = CaseParty(
            case_id=case_id,
            party_key=uuid.uuid4(),
            role=role,
            name=name,
            party_type=party_type,
            identifiers_json=identifiers_json,
            layer=LayerStatus.CANDIDATE.value,
            version=1,
            is_current=True,
        )
        self.repo.add(party)
        self.repo.flush()
        self._audit(
            actor_id or uuid.UUID(int=0),
            "create_party",
            "case_parties",
            party.id,
            case_id=case_id,
            after={"party_key": str(party.party_key), "name": name},
        )
        return party

    def confirm_party(
        self,
        party_key: UUID,
        *,
        actor_id: UUID,
        decision: HumanDecision | None = None,
    ) -> CaseParty:
        party = self._require_current_party(party_key)
        if party.layer != LayerStatus.CANDIDATE.value:
            raise ConflictError("only CANDIDATE party can be confirmed")
        decision = decision or self._new_decision(
            case_id=party.case_id,
            actor_id=actor_id,
            decision_type="CONFIRM_PARTY",
            target_type="CaseParty",
            target_id=party.party_key,
            result=DecisionResult.CONFIRMED.value,
            payload={"party_key": str(party.party_key), "version": party.version},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        party.layer = LayerStatus.CONFIRMED.value
        party.confirm_decision_id = decision.id
        party.updated_at = _now()
        self._audit(
            actor_id,
            "confirm_party",
            "case_parties",
            party.id,
            case_id=party.case_id,
            after={"layer": party.layer, "decision_id": str(decision.id)},
        )
        return party

    def amend_party(
        self,
        party_key: UUID,
        *,
        name: str,
        actor_id: UUID,
        role: str | None = None,
        party_type: str | None = None,
        decision: HumanDecision | None = None,
    ) -> CaseParty:
        old = self._require_current_party(party_key)
        if old.layer != LayerStatus.CONFIRMED.value:
            raise ConflictError("only CONFIRMED party can be amended")
        decision = decision or self._new_decision(
            case_id=old.case_id,
            actor_id=actor_id,
            decision_type="AMEND_PARTY",
            target_type="CaseParty",
            target_id=old.party_key,
            result=DecisionResult.AMENDED.value,
            payload={"old_name": old.name, "new_name": name},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        old_name = old.name
        old.layer = LayerStatus.SUPERSEDED.value
        old.is_current = False
        old.updated_at = _now()
        new_party = CaseParty(
            case_id=old.case_id,
            party_key=old.party_key,
            role=role or old.role,
            name=name,
            party_type=party_type or old.party_type,
            identifiers_json=old.identifiers_json,
            layer=LayerStatus.CONFIRMED.value,
            version=old.version + 1,
            is_current=True,
            supersedes_id=old.id,
            confirm_decision_id=decision.id,
        )
        self.repo.add(new_party)
        self.repo.flush()
        invalidate_dependencies(
            self.session,
            StaleEvent.PARTY_CHANGED,
            case_id=old.case_id,
            payload={"old_name": old_name, "old_party_key": old.party_key},
        )
        self._audit(
            actor_id,
            "amend_party",
            "case_parties",
            new_party.id,
            case_id=old.case_id,
            after={"party_key": str(new_party.party_key), "version": new_party.version},
        )
        return new_party

    def reject_party(
        self,
        party_key: UUID,
        *,
        actor_id: UUID,
        decision: HumanDecision | None = None,
    ) -> CaseParty:
        """Reject a CANDIDATE party so N5 can proceed without confirming it."""
        party = self._require_current_party(party_key)
        if party.layer != LayerStatus.CANDIDATE.value:
            raise ConflictError("only CANDIDATE party can be rejected")
        decision = decision or self._new_decision(
            case_id=party.case_id,
            actor_id=actor_id,
            decision_type="REJECT_PARTY",
            target_type="CaseParty",
            target_id=party.party_key,
            result=DecisionResult.REJECTED.value,
            payload={"party_key": str(party.party_key), "version": party.version},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        party.layer = LayerStatus.REJECTED.value
        party.confirm_decision_id = decision.id
        party.updated_at = _now()
        self._audit(
            actor_id,
            "reject_party",
            "case_parties",
            party.id,
            case_id=party.case_id,
            after={"layer": party.layer, "decision_id": str(decision.id)},
        )
        return party

    # ----- ClaimDirection -----

    def create_claim_direction(
        self,
        *,
        case_id: UUID,
        payload: dict[str, Any],
        actor_id: UUID | None = None,
    ) -> ClaimDirection:
        self._require_case(case_id)
        validated = validate_claim_direction_payload(payload)
        claim = ClaimDirection(
            claim_direction_key=uuid.uuid4(),
            case_id=case_id,
            payload=validated,
            status=LayerStatus.CANDIDATE.value,
            version=1,
            is_current=True,
        )
        self.repo.add(claim)
        self.repo.flush()
        self._audit(
            actor_id or uuid.UUID(int=0),
            "create_claim_direction",
            "claim_directions",
            claim.id,
            case_id=case_id,
            after={"claim_direction_key": str(claim.claim_direction_key)},
        )
        return claim

    def confirm_claim_direction(
        self,
        claim_direction_key: UUID,
        *,
        actor_id: UUID,
        decision: HumanDecision | None = None,
    ) -> ClaimDirection:
        claim = self._require_current_claim(claim_direction_key)
        if claim.status != LayerStatus.CANDIDATE.value:
            raise ConflictError("only CANDIDATE claim direction can be confirmed")
        if claim.stale:
            raise ConflictError("cannot confirm stale claim direction")
        self._assert_supporting_facts_confirmed(claim.payload)
        decision = decision or self._new_decision(
            case_id=claim.case_id,
            actor_id=actor_id,
            decision_type="CONFIRM_CLAIM_DIRECTION",
            target_type="ClaimDirection",
            target_id=claim.claim_direction_key,
            result=DecisionResult.CONFIRMED.value,
            payload={"claim_direction_key": str(claim.claim_direction_key)},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        claim.status = LayerStatus.CONFIRMED.value
        claim.confirm_decision_id = decision.id
        claim.updated_at = _now()
        self._audit(
            actor_id,
            "confirm_claim_direction",
            "claim_directions",
            claim.id,
            case_id=claim.case_id,
            after={"status": claim.status, "decision_id": str(decision.id)},
        )
        return claim

    def amend_claim_direction(
        self,
        claim_direction_key: UUID,
        *,
        payload: dict[str, Any],
        actor_id: UUID,
        decision: HumanDecision | None = None,
    ) -> ClaimDirection:
        old = self._require_current_claim(claim_direction_key)
        if old.status != LayerStatus.CONFIRMED.value:
            raise ConflictError("only CONFIRMED claim direction can be amended")
        validated = validate_claim_direction_payload(payload)
        self._assert_supporting_facts_confirmed(validated)
        decision = decision or self._new_decision(
            case_id=old.case_id,
            actor_id=actor_id,
            decision_type="AMEND_CLAIM_DIRECTION",
            target_type="ClaimDirection",
            target_id=old.claim_direction_key,
            result=DecisionResult.AMENDED.value,
            payload={"from_version": old.version},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        old.status = LayerStatus.SUPERSEDED.value
        old.is_current = False
        old.updated_at = _now()
        new_claim = ClaimDirection(
            claim_direction_key=old.claim_direction_key,
            case_id=old.case_id,
            payload=validated,
            status=LayerStatus.CONFIRMED.value,
            version=old.version + 1,
            is_current=True,
            supersedes_id=old.id,
            confirm_decision_id=decision.id,
        )
        self.repo.add(new_claim)
        self.repo.flush()
        invalidate_dependencies(
            self.session,
            StaleEvent.CLAIM_DIRECTION_CHANGED,
            case_id=old.case_id,
            payload={"claim_direction_key": old.claim_direction_key},
        )
        self._audit(
            actor_id,
            "amend_claim_direction",
            "claim_directions",
            new_claim.id,
            case_id=old.case_id,
            after={"version": new_claim.version, "decision_id": str(decision.id)},
        )
        return new_claim

    def reject_claim_direction(
        self,
        claim_direction_key: UUID,
        *,
        actor_id: UUID,
        decision: HumanDecision | None = None,
    ) -> ClaimDirection:
        """CANDIDATE/CONFIRMED → REJECTED (DB already allows REJECTED; Phase 7 additive)."""
        claim = self._require_current_claim(claim_direction_key)
        if claim.status not in {
            LayerStatus.CANDIDATE.value,
            LayerStatus.CONFIRMED.value,
        }:
            raise ConflictError("claim direction cannot be rejected from current status")
        decision = decision or self._new_decision(
            case_id=claim.case_id,
            actor_id=actor_id,
            decision_type="REJECT_CLAIM_DIRECTION",
            target_type="ClaimDirection",
            target_id=claim.claim_direction_key,
            result=DecisionResult.REJECTED.value,
            payload={
                "claim_direction_key": str(claim.claim_direction_key),
                "version": claim.version,
            },
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        claim.status = LayerStatus.REJECTED.value
        claim.is_current = True
        claim.updated_at = _now()
        self._audit(
            actor_id,
            "reject_claim_direction",
            "claim_directions",
            claim.id,
            case_id=claim.case_id,
            after={"status": claim.status, "decision_id": str(decision.id)},
        )
        return claim

    def invalidate_dependencies(
        self,
        event: StaleEvent,
        *,
        case_id: UUID,
        payload: dict[str, Any],
    ) -> list[str]:
        return invalidate_dependencies(self.session, event, case_id=case_id, payload=payload)

    # ----- Draft -----

    def create_document_draft(
        self,
        *,
        case_id: UUID,
        body_structured_json: dict[str, Any],
        citations: list[dict[str, Any]],
        based_on_confirmation_set_hash: str | None = None,
        doc_type: str = "COMPLAINT",
        status: str = DraftStatus.DRAFT.value,
        writer_run_id: UUID | None = None,
        actor_id: UUID | None = None,
    ) -> DocumentDraft:
        self._require_case(case_id)
        from sqlalchemy import func, select

        max_version = self.session.scalar(
            select(func.max(DocumentDraft.version)).where(
                DocumentDraft.case_id == case_id, DocumentDraft.doc_type == doc_type
            )
        )
        version = int(max_version or 0) + 1
        conf_hash = based_on_confirmation_set_hash or _confirmation_set_hash(
            [json.dumps(body_structured_json, sort_keys=True, ensure_ascii=False)]
        )
        draft = DocumentDraft(
            case_id=case_id,
            doc_type=doc_type,
            version=version,
            status=status,
            body_structured_json=body_structured_json,
            based_on_confirmation_set_hash=conf_hash,
            writer_run_id=writer_run_id,
        )
        self.repo.add(draft)
        self.repo.flush()
        for cite in citations:
            has_fact = cite.get("fact_key")
            has_evidence = cite.get("evidence_item_id")
            is_annotation = cite.get("citation_kind") == "ANNOTATION"
            if not has_fact and not has_evidence and not is_annotation:
                raise ValidationError("citation must pin fact or evidence")
            if cite.get("evidence_item_id") and cite.get("evidence_item_version") is None:
                raise ValidationError("DraftCitation must include evidence_item_version")
            if cite.get("fact_key") and cite.get("fact_version") is None:
                raise ValidationError("DraftCitation must include fact_version")
            self.repo.add(
                DraftCitation(
                    draft_id=draft.id,
                    block_id=str(cite["block_id"]),
                    citation_kind=str(cite.get("citation_kind", "FACT")),
                    fact_key=UUID(str(cite["fact_key"])) if cite.get("fact_key") else None,
                    fact_version=cite.get("fact_version"),
                    evidence_item_id=UUID(str(cite["evidence_item_id"]))
                    if cite.get("evidence_item_id")
                    else None,
                    evidence_item_version=cite.get("evidence_item_version"),
                    requires_lawyer_confirm=bool(cite.get("requires_lawyer_confirm", False)),
                )
            )
        self.repo.flush()
        self._audit(
            actor_id or uuid.UUID(int=0),
            "create_document_draft",
            "document_drafts",
            draft.id,
            case_id=case_id,
            after={"version": draft.version, "status": draft.status},
        )
        return draft

    def mark_document_draft_stale(
        self,
        draft_id: UUID,
        *,
        reason: str,
        actor_id: UUID,
    ) -> DocumentDraft:
        draft = self.repo.get_draft(draft_id)
        if draft is None:
            raise NotFoundError("draft not found")
        draft.status = DraftStatus.STALE.value
        draft.stale_reason = reason
        draft.updated_at = _now()
        self._audit(
            actor_id,
            "mark_document_draft_stale",
            "document_drafts",
            draft.id,
            case_id=draft.case_id,
            after={"status": draft.status, "reason": reason},
        )
        return draft

    def approve_document_draft(
        self,
        draft_id: UUID,
        *,
        actor_id: UUID,
        decision: HumanDecision | None = None,
    ) -> DocumentDraft:
        draft = self.repo.get_draft(draft_id)
        if draft is None:
            raise NotFoundError("draft not found")
        if draft.status not in {DraftStatus.DRAFT.value, DraftStatus.IN_REVIEW.value}:
            raise ConflictError("draft cannot be approved from current status")
        decision = decision or self._new_decision(
            case_id=draft.case_id,
            actor_id=actor_id,
            decision_type="APPROVE_DRAFT",
            target_type="DocumentDraft",
            target_id=draft.id,
            result=DecisionResult.CONFIRMED.value,
            payload={"draft_id": str(draft.id), "version": draft.version},
        )
        self.repo.add_decision(decision)
        self.repo.flush()
        draft.status = DraftStatus.APPROVED_BY_LAWYER.value
        draft.updated_at = _now()
        # Intentionally does NOT modify WorkflowInstance status.
        self._audit(
            actor_id,
            "approve_document_draft",
            "document_drafts",
            draft.id,
            case_id=draft.case_id,
            after={"status": draft.status, "decision_id": str(decision.id)},
        )
        return draft

    # ----- helpers -----

    def _require_case(self, case_id: UUID) -> Case:
        case = self.repo.get_case(case_id)
        if case is None:
            raise NotFoundError("case not found")
        if case.status != CaseStatus.OPEN.value:
            raise ConflictError("case is not OPEN")
        return case

    def _require_current_evidence(self, evidence_id: UUID) -> EvidenceItem:
        item = self.repo.get_current_evidence(evidence_id)
        if item is None:
            raise NotFoundError("evidence item not found")
        return item

    def _require_current_fact(self, fact_key: UUID) -> Fact:
        fact = self.repo.get_current_fact(fact_key)
        if fact is None:
            raise NotFoundError("fact not found")
        return fact

    def _require_current_party(self, party_key: UUID) -> CaseParty:
        party = self.repo.get_current_party(party_key)
        if party is None:
            raise NotFoundError("party not found")
        return party

    def _require_current_claim(self, claim_direction_key: UUID) -> ClaimDirection:
        claim = self.repo.get_current_claim(claim_direction_key)
        if claim is None:
            raise NotFoundError("claim direction not found")
        return claim

    def _assert_supporting_facts_confirmed(self, payload: dict[str, Any]) -> None:
        for item in payload.get("claims") or []:
            fact_ids = item.get("supporting_fact_ids") or []
            if not fact_ids:
                raise ValidationError(
                    "each claim requires at least one supporting_fact_id"
                )
            for fact_id in fact_ids:
                fact_key = UUID(str(fact_id))
                fact = self.repo.get_current_fact(fact_key)
                if fact is None:
                    raise ValidationError(f"supporting fact not found: {fact_key}")
                if fact.status != FactStatus.CONFIRMED.value:
                    raise ValidationError(f"supporting fact not CONFIRMED: {fact_key}")
                if fact.stale:
                    raise ValidationError(f"supporting fact is stale: {fact_key}")

    def _new_decision(
        self,
        *,
        case_id: UUID,
        actor_id: UUID,
        decision_type: str,
        target_type: str,
        target_id: UUID | None,
        result: str,
        payload: dict[str, Any],
    ) -> HumanDecision:
        return HumanDecision(
            case_id=case_id,
            decision_type=decision_type,
            target_type=target_type,
            target_id=target_id,
            input_payload_json=payload,
            result=result,
            actor_id=actor_id,
        )

    def _audit(
        self,
        actor_id: UUID,
        action: str,
        entity_type: str,
        entity_id: UUID,
        *,
        case_id: UUID | None = None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> None:
        self.repo.add_audit(
            AuditLog(
                actor_id=actor_id,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                case_id=case_id,
                before_json=before,
                after_json=after,
            )
        )
        self.repo.flush()


# Guard: DomainService must not allow mutating material content hash/storage key via public API.
def ensure_material_bytes_immutable(material: CaseMaterial, updates: dict[str, Any]) -> None:
    if "content_hash" in updates or "storage_key" in updates:
        raise ImmutableError("material content_hash/storage_key are immutable")
