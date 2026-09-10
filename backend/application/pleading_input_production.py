"""V2-P4 — build production PleadingStructuredInput for Writer."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.application.claim_direction import ClaimDirectionService
from backend.application.claim_view import ClaimViewService
from backend.application.issue_matrix import IssueMatrixService
from backend.application.pleading_structured_input import PleadingStructuredInputBuilder
from backend.domain.errors import ValidationError
from backend.models import (
    CaseMaterial,
    Claim,
    ClaimFactLink,
    ClaimIssueLink,
    EvidenceItemSpan,
    Fact,
    IssueFactLink,
    SourceSpan,
)
from backend.repositories.base import Repository
from backend.schemas.pleading_structured_input import (
    ClaimCentricView,
    ClaimFactRelation,
    ClaimInput,
    ClaimIssueRelation,
    EvidenceInput,
    FactEvidenceRelation,
    FactInput,
    IssueFactRelation,
    IssueInput,
    PartyInput,
    PleadingStructuredInput,
    SnapshotMeta,
    SourceSpanInput,
)
from backend.skills.pleading_writer import (
    AcceptedEvidenceView,
    ClaimDirectionView,
    ConfirmedFactView,
    PartyView,
)

_REF_SEP = "@v"


def _ref_key(kind: str, identity: str, version: int) -> str:
    return f"{kind}:{identity}{_REF_SEP}{version}"


def _stable_hash(parts: list[str]) -> str:
    return hashlib.sha256("|".join(sorted(parts)).encode("utf-8")).hexdigest()


def _formal_ref_sets(
    claims_in: list[ClaimInput],
    issues_in: list[IssueInput],
    facts_in: list[FactInput],
    evidence_in: list[EvidenceInput],
) -> tuple[
    set[tuple[str, int]],
    set[tuple[str, int]],
    set[tuple[str, int]],
    set[tuple[str, int]],
]:
    """Formal refs derived only from final structured arrays — not loose DB queries."""
    formal_claim_refs = {(c.claim_key, c.claim_version) for c in claims_in}
    formal_issue_refs = {(i.issue_key, i.issue_version) for i in issues_in}
    formal_fact_refs = {(f.fact_key, f.fact_version) for f in facts_in}
    formal_evidence_refs = {
        (e.evidence_item_id, e.evidence_item_version) for e in evidence_in
    }
    return formal_claim_refs, formal_issue_refs, formal_fact_refs, formal_evidence_refs


def _relation_ref_keys(
    claim_issue: list[ClaimIssueRelation],
    claim_fact: list[ClaimFactRelation],
    issue_fact: list[IssueFactRelation],
    fact_evidence: list[FactEvidenceRelation],
) -> list[str]:
    refs: list[str] = []
    for r in claim_issue:
        refs.append(
            f"claim-issue:{r.claim_key}@v{r.claim_version}:"
            f"{r.issue_key}@v{r.issue_version}:{r.role}"
        )
    for r in claim_fact:
        refs.append(
            f"claim-fact:{r.claim_key}@v{r.claim_version}:"
            f"{r.fact_key}@v{r.fact_version}:{r.role}"
        )
    for r in issue_fact:
        refs.append(
            f"issue-fact:{r.issue_key}@v{r.issue_version}:"
            f"{r.fact_key}@v{r.fact_version}:{r.role}"
        )
    for r in fact_evidence:
        refs.append(
            f"fact-evidence:{r.fact_key}@v{r.fact_version}:"
            f"{r.evidence_item_id}@v{r.evidence_item_version}:{r.role}"
        )
    return refs


def assert_closed_relation_graph(inp: PleadingStructuredInput) -> None:
    """Every relation edge must reference objects present in this snapshot."""
    formal_claim_refs, formal_issue_refs, formal_fact_refs, formal_evidence_refs = (
        _formal_ref_sets(inp.claims, inp.issues, inp.facts, inp.evidence)
    )

    def _check_active(status: str, label: str) -> None:
        if status != "ACTIVE":
            raise ValidationError(f"blocking: non-ACTIVE {label} relation in snapshot")

    for r in inp.claim_issue_relations:
        _check_active(r.status, "claim-issue")
        if (r.claim_key, r.claim_version) not in formal_claim_refs:
            raise ValidationError(
                f"blocking: orphan claim-issue relation for claim "
                f"{r.claim_key}@v{r.claim_version}"
            )
        if (r.issue_key, r.issue_version) not in formal_issue_refs:
            raise ValidationError(
                f"blocking: orphan claim-issue relation for issue "
                f"{r.issue_key}@v{r.issue_version}"
            )

    for r in inp.claim_fact_relations:
        _check_active(r.status, "claim-fact")
        if (r.claim_key, r.claim_version) not in formal_claim_refs:
            raise ValidationError(
                f"blocking: orphan claim-fact relation for claim "
                f"{r.claim_key}@v{r.claim_version}"
            )
        if (r.fact_key, r.fact_version) not in formal_fact_refs:
            raise ValidationError(
                f"blocking: orphan claim-fact relation for fact "
                f"{r.fact_key}@v{r.fact_version}"
            )

    for r in inp.issue_fact_relations:
        _check_active(r.status, "issue-fact")
        if (r.issue_key, r.issue_version) not in formal_issue_refs:
            raise ValidationError(
                f"blocking: orphan issue-fact relation for issue "
                f"{r.issue_key}@v{r.issue_version}"
            )
        if (r.fact_key, r.fact_version) not in formal_fact_refs:
            raise ValidationError(
                f"blocking: orphan issue-fact relation for fact "
                f"{r.fact_key}@v{r.fact_version}"
            )

    for r in inp.fact_evidence_relations:
        _check_active(r.status, "fact-evidence")
        if (r.fact_key, r.fact_version) not in formal_fact_refs:
            raise ValidationError(
                f"blocking: orphan fact-evidence relation for fact "
                f"{r.fact_key}@v{r.fact_version}"
            )
        if (r.evidence_item_id, r.evidence_item_version) not in formal_evidence_refs:
            raise ValidationError(
                f"blocking: orphan fact-evidence relation for evidence "
                f"{r.evidence_item_id}@v{r.evidence_item_version}"
            )


class PleadingStructuredInputProductionBuilder:
    """Production SSOT builder — Claim Domain first, ClaimDirection legacy fallback."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = Repository(session)
        self.claim_direction_svc = ClaimDirectionService(session)

    def build(
        self,
        *,
        case_id: UUID,
        parties: list[PartyView],
        facts: list[ConfirmedFactView],
        evidence: list[AcceptedEvidenceView],
    ) -> PleadingStructuredInput:
        self._assert_parties_formal(parties)
        self._assert_facts_formal(case_id, facts)
        self._assert_evidence_formal(case_id, evidence)

        claim_rows, claim_source, legacy_ref, claim_warnings = self._resolve_claims(case_id)
        claim_view_items = (
            ClaimViewService(self.session).build(case_id, include_candidates=False).items
            if claim_source == "CLAIM_DOMAIN"
            else []
        )
        view_by_key = {
            (item.claim_key, item.claim_version): item for item in claim_view_items
        }

        issue_matrix = IssueMatrixService(self.session).build(
            case_id, include_candidates=False
        )
        issues_in: list[IssueInput] = []
        for item in issue_matrix.items:
            if item.status != "CONFIRMED":
                continue
            if item.stale_state.get("stale"):
                continue
            issues_in.append(
                IssueInput(
                    issue_key=item.issue_key,
                    issue_version=item.issue_version,
                    statement=item.statement,
                    source_type=item.source_type,
                    lawyer_confirmation_state=item.lawyer_confirmation_state,
                )
            )

        facts_in = self._build_facts_in(case_id, facts)
        evidence_in = self._build_evidence_in(case_id, evidence)
        claims_in = self._build_claims_in(claim_rows, view_by_key)
        (
            formal_claim_refs,
            formal_issue_refs,
            formal_fact_refs,
            formal_evidence_refs,
        ) = _formal_ref_sets(claims_in, issues_in, facts_in, evidence_in)
        relations = self._build_relations(
            case_id,
            formal_claim_refs=formal_claim_refs,
            formal_issue_refs=formal_issue_refs,
            formal_fact_refs=formal_fact_refs,
            formal_evidence_refs=formal_evidence_refs,
        )

        warnings = list(claim_warnings)
        if claim_source == "LEGACY_CLAIM_DIRECTION":
            warnings.append("LEGACY_CLAIM_SOURCE")

        party_refs = [_ref_key("party", str(p.party_key), p.version) for p in parties]
        claim_refs = [
            _ref_key("claim", c.claim_key, c.claim_version) for c in claims_in
        ]
        issue_refs = [
            _ref_key("issue", i.issue_key, i.issue_version) for i in issues_in
        ]
        fact_refs = [_ref_key("fact", f.fact_key, f.fact_version) for f in facts_in]
        evidence_refs = [
            _ref_key("evidence", e.evidence_item_id, e.evidence_item_version)
            for e in evidence_in
        ]
        relation_refs = _relation_ref_keys(
            relations["claim_issue"],
            relations["claim_fact"],
            relations["issue_fact"],
            relations["fact_evidence"],
        )
        conf_hash = _stable_hash(
            party_refs
            + claim_refs
            + issue_refs
            + fact_refs
            + evidence_refs
            + relation_refs
        )

        snapshot = SnapshotMeta(
            generated_at=datetime.now(UTC),
            party_refs=party_refs,
            claim_refs=claim_refs,
            issue_refs=issue_refs,
            fact_refs=fact_refs,
            evidence_refs=evidence_refs,
            confirmation_set_hash=conf_hash,
            claim_source=claim_source,
            legacy_claim_direction_ref=legacy_ref,
        )

        claim_view = self._claim_direction_view_for_writer(
            case_id, claim_source, claim_rows, legacy_ref
        )
        writer_structured = PleadingStructuredInputBuilder(self.session).build(
            parties=parties,
            facts=facts,
            claim=claim_view,
            evidence=evidence,
        )
        if claim_source == "CLAIM_DOMAIN":
            writer_structured.claims_payload = self._claims_domain_payload(claims_in)
        writer_structured.warnings = list(set(writer_structured.warnings + warnings))

        structured = PleadingStructuredInput(
            case_id=str(case_id),
            parties=[
                PartyInput(
                    party_key=str(p.party_key),
                    version=p.version,
                    role=p.role,
                    name=p.name,
                    party_type=p.party_type,
                )
                for p in parties
            ],
            claims=claims_in,
            issues=issues_in,
            facts=facts_in,
            evidence=evidence_in,
            claim_issue_relations=relations["claim_issue"],
            claim_fact_relations=relations["claim_fact"],
            issue_fact_relations=relations["issue_fact"],
            fact_evidence_relations=relations["fact_evidence"],
            warnings=warnings,
            snapshot_meta=snapshot,
            writer_structured=writer_structured,
        )
        assert_closed_relation_graph(structured)
        return structured

    @staticmethod
    def assert_formal_boundaries(inp: PleadingStructuredInput) -> None:
        for claim in inp.claims:
            if claim.amount and inp.snapshot_meta.claim_source == "CLAIM_DOMAIN":
                has_basis = bool(claim.claim_centric.amount_basis_fact_refs)
                if not has_basis and claim.amount is not None:
                    pass  # warning handled in readiness, not blocking here
        if not inp.claims and inp.snapshot_meta.claim_source == "LEGACY_CLAIM_DIRECTION":
            return
        if not inp.claims:
            raise ValidationError("blocking: no confirmed claim in structured input")
        assert_closed_relation_graph(inp)

    def summary(self, case_id: UUID) -> dict[str, Any]:
        try:
            inp = self.build_preview(case_id)
        except ValidationError as exc:
            return {
                "confirmed_claim_count": 0,
                "confirmed_issue_count": 0,
                "confirmed_fact_count": 0,
                "accepted_evidence_count": 0,
                "blocking_reasons": [str(exc.message if hasattr(exc, "message") else exc)],
                "warnings": [],
                "confirmation_set_hash": None,
                "claim_source": None,
            }
        return {
            "confirmed_claim_count": len(inp.claims),
            "confirmed_issue_count": len(inp.issues),
            "confirmed_fact_count": len(inp.facts),
            "accepted_evidence_count": len(inp.evidence),
            "blocking_reasons": [],
            "warnings": inp.warnings,
            "confirmation_set_hash": inp.snapshot_meta.confirmation_set_hash,
            "claim_source": inp.snapshot_meta.claim_source,
        }

    def build_preview(self, case_id: UUID) -> PleadingStructuredInput:
        """Best-effort preview for API/workspace without full Writer gate inputs."""
        parties = self._load_confirmed_parties(case_id)
        facts = self._load_confirmed_facts_for_case(case_id)
        evidence = self._load_accepted_evidence_for_case(case_id)
        return self.build(
            case_id=case_id, parties=parties, facts=facts, evidence=evidence
        )

    # ----- claim resolution -----

    def _resolve_claims(
        self, case_id: UUID
    ) -> tuple[list[Claim], str, dict[str, Any] | None, list[str]]:
        rows = list(
            self.session.scalars(
                select(Claim).where(
                    Claim.case_id == case_id,
                    Claim.is_current.is_(True),
                    Claim.status == "CONFIRMED",
                    Claim.stale.is_(False),
                )
            )
        )
        warnings: list[str] = []
        if rows:
            for row in rows:
                if row.amount_is_suggested:
                    warnings.append(
                        f"Claim {row.claim_key} has suggested amount — excluded from formal amount"
                    )
            return rows, "CLAIM_DOMAIN", None, warnings

        try:
            cd = self.claim_direction_svc.get_confirmed_claim_direction_for_writer(case_id)
        except ValidationError:
            return [], "CLAIM_DOMAIN", None, ["NO_CONFIRMED_CLAIM"]

        legacy_ref = {
            "claim_direction_key": str(cd.claim_direction_key),
            "claim_direction_version": cd.version,
        }
        warnings.append("LEGACY_CLAIM_SOURCE")
        return [], "LEGACY_CLAIM_DIRECTION", legacy_ref, warnings

    def _claim_direction_view_for_writer(
        self,
        case_id: UUID,
        claim_source: str,
        claim_rows: list[Claim],
        legacy_ref: dict[str, Any] | None,
    ) -> ClaimDirectionView:
        if claim_source == "CLAIM_DOMAIN" and claim_rows:
            payload = {
                "overall_strategy": "基于律师确认的诉讼请求生成。",
                "claims": [
                    {
                        "claim_type": c.claim_type,
                        "description": c.statement,
                        "amount": c.amount if not c.amount_is_suggested else None,
                        "currency": c.currency,
                        "supporting_fact_ids": [],
                    }
                    for c in claim_rows
                ],
            }
            return ClaimDirectionView(
                claim_direction_key=claim_rows[0].claim_key,
                claim_direction_version=max(c.version for c in claim_rows),
                payload=payload,
                status="CONFIRMED",
                stale=False,
            )
        cd = self.claim_direction_svc.get_confirmed_claim_direction_for_writer(case_id)
        return ClaimDirectionView(
            claim_direction_key=cd.claim_direction_key,
            claim_direction_version=cd.version,
            payload=dict(cd.payload or {}),
            status=cd.status,
            stale=cd.stale,
        )

    @staticmethod
    def _claims_domain_payload(claims: list[ClaimInput]) -> dict[str, Any]:
        return {
            "overall_strategy": "基于律师确认的诉讼请求生成。",
            "claims": [
                {
                    "claim_type": c.claim_type,
                    "description": c.statement,
                    "amount": c.amount,
                    "currency": c.currency,
                    "supporting_fact_ids": c.claim_centric.basis_fact_refs,
                }
                for c in claims
            ],
        }

    def _build_claims_in(
        self,
        claim_rows: list[Claim],
        view_by_key: dict[tuple[str, int], Any],
    ) -> list[ClaimInput]:
        out: list[ClaimInput] = []
        for row in claim_rows:
            view = view_by_key.get((str(row.claim_key), row.version))
            centric = ClaimCentricView()
            if view:
                centric.basis_issue_refs = [
                    _ref_key("issue", r.issue_key, r.issue_version)
                    for r in view.basis_issues
                ]
                centric.limitation_issue_refs = [
                    _ref_key("issue", r.issue_key, r.issue_version)
                    for r in view.limitation_issues
                ]
                centric.context_issue_refs = [
                    _ref_key("issue", r.issue_key, r.issue_version)
                    for r in view.context_issues
                ]
                centric.basis_fact_refs = [
                    _ref_key("fact", r.fact_key, r.fact_version) for r in view.basis_facts
                ]
                centric.amount_basis_fact_refs = [
                    _ref_key("fact", r.fact_key, r.fact_version)
                    for r in view.amount_basis_facts
                ]
                centric.limitation_fact_refs = [
                    _ref_key("fact", r.fact_key, r.fact_version) for r in view.limitations
                ]
            out.append(
                ClaimInput(
                    claim_key=str(row.claim_key),
                    claim_version=row.version,
                    claim_type=row.claim_type,
                    title=row.title,
                    statement=row.statement,
                    amount=row.amount if not row.amount_is_suggested else None,
                    currency=row.currency,
                    source_type=row.source_type,
                    confirm_decision_id=str(row.confirm_decision_id)
                    if row.confirm_decision_id
                    else None,
                    claim_centric=centric,
                )
            )
        return out

    def _build_facts_in(
        self, case_id: UUID, facts: list[ConfirmedFactView]
    ) -> list[FactInput]:
        out: list[FactInput] = []
        for f in facts:
            row = self.session.scalars(
                select(Fact).where(
                    Fact.fact_key == f.fact_key, Fact.version == f.fact_version
                )
            ).first()
            if row is None or row.case_id != case_id:
                raise ValidationError(f"cross-case or missing fact: {f.fact_key}")
            out.append(
                FactInput(
                    fact_key=str(f.fact_key),
                    fact_version=f.fact_version,
                    statement=f.statement,
                    importance=row.importance,
                )
            )
        return out

    def _build_evidence_in(
        self, case_id: UUID, evidence: list[AcceptedEvidenceView]
    ) -> list[EvidenceInput]:
        out: list[EvidenceInput] = []
        for ev in evidence:
            spans = self._source_spans_for_evidence(case_id, ev)
            out.append(
                EvidenceInput(
                    evidence_item_id=str(ev.evidence_item_id),
                    evidence_item_version=ev.evidence_item_version,
                    number=ev.number,
                    title=ev.title,
                    category=ev.category,
                    source_spans=spans,
                )
            )
        return out

    def _source_spans_for_evidence(
        self, case_id: UUID, ev: AcceptedEvidenceView
    ) -> list[SourceSpanInput]:
        links = self.session.scalars(
            select(EvidenceItemSpan).where(
                EvidenceItemSpan.evidence_item_id == ev.evidence_item_id,
                EvidenceItemSpan.evidence_item_version == ev.evidence_item_version,
            )
        ).all()
        spans: list[SourceSpanInput] = []
        for link in links:
            span = self.session.get(SourceSpan, link.source_span_id)
            if span is None:
                continue
            material = self.session.get(CaseMaterial, span.material_id)
            if material is None or material.case_id != case_id:
                raise ValidationError("cross-case source span rejected")
            spans.append(
                SourceSpanInput(
                    source_span_id=str(span.id),
                    material_id=str(span.material_id),
                    page=span.page,
                    paragraph=getattr(span, "paragraph", None),
                    character_start=span.character_start,
                    character_end=span.character_end,
                    quote=span.quote or "",
                    quote_hash=getattr(span, "quote_hash", None),
                )
            )
        return spans

    def _build_relations(
        self,
        case_id: UUID,
        *,
        formal_claim_refs: set[tuple[str, int]],
        formal_issue_refs: set[tuple[str, int]],
        formal_fact_refs: set[tuple[str, int]],
        formal_evidence_refs: set[tuple[str, int]],
    ) -> dict[str, list]:
        claim_issue: list[ClaimIssueRelation] = []
        for link in self.session.scalars(
            select(ClaimIssueLink).where(
                ClaimIssueLink.case_id == case_id, ClaimIssueLink.status == "ACTIVE"
            )
        ):
            claim_ref = (str(link.claim_key), link.claim_version)
            issue_ref = (str(link.issue_key), link.issue_version)
            if claim_ref not in formal_claim_refs:
                continue
            if issue_ref not in formal_issue_refs:
                continue
            claim_issue.append(
                ClaimIssueRelation(
                    claim_key=claim_ref[0],
                    claim_version=claim_ref[1],
                    issue_key=issue_ref[0],
                    issue_version=issue_ref[1],
                    role=link.role,
                    status=link.status,
                )
            )

        claim_fact: list[ClaimFactRelation] = []
        for link in self.session.scalars(
            select(ClaimFactLink).where(
                ClaimFactLink.case_id == case_id, ClaimFactLink.status == "ACTIVE"
            )
        ):
            claim_ref = (str(link.claim_key), link.claim_version)
            fact_ref = (str(link.fact_key), link.fact_version)
            if claim_ref not in formal_claim_refs:
                continue
            if fact_ref not in formal_fact_refs:
                continue
            claim_fact.append(
                ClaimFactRelation(
                    claim_key=claim_ref[0],
                    claim_version=claim_ref[1],
                    fact_key=fact_ref[0],
                    fact_version=fact_ref[1],
                    role=link.role,
                    status=link.status,
                )
            )

        issue_fact: list[IssueFactRelation] = []
        for link in self.session.scalars(
            select(IssueFactLink).where(
                IssueFactLink.case_id == case_id, IssueFactLink.status == "ACTIVE"
            )
        ):
            issue_ref = (str(link.issue_key), link.issue_version)
            fact_ref = (str(link.fact_key), link.fact_version)
            if issue_ref not in formal_issue_refs:
                continue
            if fact_ref not in formal_fact_refs:
                continue
            issue_fact.append(
                IssueFactRelation(
                    issue_key=issue_ref[0],
                    issue_version=issue_ref[1],
                    fact_key=fact_ref[0],
                    fact_version=fact_ref[1],
                    role=link.role,
                    status=link.status,
                )
            )

        fact_evidence: list[FactEvidenceRelation] = []
        for fact_key, fact_version in sorted(formal_fact_refs):
            row = self.session.scalars(
                select(Fact).where(
                    Fact.fact_key == fact_key, Fact.version == fact_version
                )
            ).first()
            if row is None or row.case_id != case_id:
                continue
            for link in self.repo.list_fact_links(row.id):
                if link.status != "ACTIVE":
                    continue
                ev_ref = (str(link.evidence_item_id), link.evidence_item_version)
                if ev_ref not in formal_evidence_refs:
                    continue
                fact_evidence.append(
                    FactEvidenceRelation(
                        fact_key=fact_key,
                        fact_version=fact_version,
                        evidence_item_id=ev_ref[0],
                        evidence_item_version=ev_ref[1],
                        role=link.link_role,
                        status=link.status,
                        source_span_id=str(link.source_span_id)
                        if link.source_span_id
                        else None,
                    )
                )

        return {
            "claim_issue": claim_issue,
            "claim_fact": claim_fact,
            "issue_fact": issue_fact,
            "fact_evidence": fact_evidence,
        }

    # ----- formal boundary checks -----

    def _assert_parties_formal(self, parties: list[PartyView]) -> None:
        for p in parties:
            party = self.repo.get_current_party(p.party_key)
            if party is None or party.layer != "CONFIRMED":
                raise ValidationError(f"blocking: party not CONFIRMED: {p.party_key}")
            if party.stale:
                raise ValidationError(f"blocking: stale party: {p.party_key}")
            if party.version != p.version:
                raise ValidationError("blocking: party version mismatch")

    def _assert_facts_formal(
        self, case_id: UUID, facts: list[ConfirmedFactView]
    ) -> None:
        for f in facts:
            row = self.session.scalars(
                select(Fact).where(
                    Fact.fact_key == f.fact_key, Fact.version == f.fact_version
                )
            ).first()
            if row is None:
                raise ValidationError(f"broken fact version: {f.fact_key}")
            if row.case_id != case_id:
                raise ValidationError("cross-case fact rejected")
            if row.status == "CANDIDATE":
                raise ValidationError("blocking: candidate fact in formal input")
            if row.status != "CONFIRMED":
                raise ValidationError(f"blocking: fact not CONFIRMED: {f.fact_key}")
            if row.stale:
                raise ValidationError(f"blocking: stale fact: {f.fact_key}")

    def _assert_evidence_formal(
        self, case_id: UUID, evidence: list[AcceptedEvidenceView]
    ) -> None:
        for ev in evidence:
            item = self.repo.get_evidence_version(
                ev.evidence_item_id, ev.evidence_item_version
            )
            if item is None or item.case_id != case_id:
                raise ValidationError("cross-case evidence rejected")
            if item.acceptance != "ACCEPTED":
                raise ValidationError(
                    f"blocking: evidence not ACCEPTED: {ev.evidence_item_id}"
                )

    def _load_confirmed_parties(self, case_id: UUID) -> list[PartyView]:
        from backend.models import CaseParty

        rows = self.session.scalars(
            select(CaseParty).where(
                CaseParty.case_id == case_id,
                CaseParty.is_current.is_(True),
                CaseParty.layer == "CONFIRMED",
            )
        ).all()
        return [
            PartyView(
                party_key=r.party_key,
                role=r.role,
                name=r.name,
                party_type=r.party_type,
                version=r.version,
                identifiers_json=r.identifiers_json,
            )
            for r in rows
        ]

    def _load_confirmed_facts_for_case(self, case_id: UUID) -> list[ConfirmedFactView]:
        from backend.schemas.case_analyst import EvidenceRef

        rows = self.session.scalars(
            select(Fact).where(
                Fact.case_id == case_id,
                Fact.is_current.is_(True),
                Fact.status == "CONFIRMED",
                Fact.stale.is_(False),
            )
        ).all()
        views: list[ConfirmedFactView] = []
        for row in rows:
            links = [
                lnk
                for lnk in self.repo.list_fact_links(row.id)
                if lnk.status == "ACTIVE"
            ]
            erefs = [
                EvidenceRef(
                    evidence_item_id=lnk.evidence_item_id,
                    evidence_item_version=lnk.evidence_item_version,
                )
                for lnk in links
            ]
            views.append(
                ConfirmedFactView(
                    fact_key=row.fact_key,
                    fact_version=row.version,
                    statement=row.statement,
                    evidence_refs=erefs,
                )
            )
        return views

    def _load_accepted_evidence_for_case(
        self, case_id: UUID
    ) -> list[AcceptedEvidenceView]:
        from backend.models import EvidenceItem

        rows = self.session.scalars(
            select(EvidenceItem).where(
                EvidenceItem.case_id == case_id,
                EvidenceItem.is_current.is_(True),
                EvidenceItem.acceptance == "ACCEPTED",
            )
        ).all()
        return [
            AcceptedEvidenceView(
                evidence_item_id=r.id,
                evidence_item_version=r.version,
                number=r.number,
                title=r.title,
                summary=r.summary,
                category=r.category,
            )
            for r in rows
        ]
