"""Read-only case conversation context — never mutates Domain/Workflow."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.application.material_usability import MaterialUsabilityPolicy
from backend.models import (
    AgentMessage,
    Case,
    CaseParty,
    ClaimDirection,
    DocumentDraft,
    EvidenceItem,
    EvidenceItemSpan,
    ExtractedContent,
    Fact,
    FactEvidenceLink,
    Issue,
    LegalTheory,
    SourceSpan,
    WorkflowInstance,
    WorkflowNode,
)


@dataclass
class ConversationTurn:
    role: str
    content: str


@dataclass
class SourceExcerpt:
    display_evidence: str | None
    quote: str
    page: int | None
    paragraph: int | None
    material_filename: str | None


@dataclass
class CaseConversationContext:
    case_title: str
    case_status: str
    workflow_status: str | None
    current_node: str | None
    current_node_label: str | None
    parties: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    facts: list[dict[str, Any]] = field(default_factory=list)
    issues: list[dict[str, Any]] = field(default_factory=list)
    legal_theories: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    missing_evidence: list[dict[str, Any]] = field(default_factory=list)
    claim: dict[str, Any] | None = None
    draft: dict[str, Any] | None = None
    material_pool: dict[str, Any] = field(default_factory=dict)
    pleading_readiness: dict[str, Any] | None = None
    history: list[ConversationTurn] = field(default_factory=list)
    source_excerpts: list[SourceExcerpt] = field(default_factory=list)

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "case": {
                "title": self.case_title,
                "status": self.case_status,
                "workflow_status": self.workflow_status,
                "current_node": self.current_node,
                "current_node_label": self.current_node_label,
            },
            "layer_legend": {
                "CONFIRMED_ACCEPTED": "律师已确认/已接受，可称「目前已确认」",
                "CANDIDATE": "AI 候选，必须标明尚未由律师确认",
                "REJECTED": "已拒绝",
                "PENDING_EVIDENCE": "证据待律师确认",
            },
            "parties": self.parties,
            "evidence": self.evidence,
            "facts": self.facts,
            "issues_ai_candidate": self.issues,
            "legal_theories_ai_candidate": self.legal_theories,
            "conflicts_ai_analysis": self.conflicts,
            "missing_evidence_ai_analysis": self.missing_evidence,
            "claim_direction": self.claim,
            "draft": self.draft,
            "material_pool": self.material_pool,
            "pleading_readiness": self.pleading_readiness,
            "recent_conversation": [
                {"role": t.role, "content": t.content} for t in self.history
            ],
            "source_excerpts": [
                {
                    "evidence": e.display_evidence,
                    "quote": e.quote,
                    "page": e.page,
                    "paragraph": e.paragraph,
                    "material": e.material_filename,
                }
                for e in self.source_excerpts
            ],
        }


class CaseConversationContextBuilder:
    """Assemble minimal necessary read-only context for CASE_CONVERSATION."""

    MAX_HISTORY = 16
    MAX_EXCERPTS = 8
    MAX_QUOTE_CHARS = 600

    def __init__(self, session: Session) -> None:
        self.session = session
        self.policy = MaterialUsabilityPolicy(session)

    def build(
        self,
        *,
        case_id: UUID,
        conversation_id: UUID | None,
        user_message: str,
    ) -> CaseConversationContext:
        case = self.session.get(Case, case_id)
        if case is None:
            raise LookupError("case not found")

        wf_status, node_code, node_label = self._workflow_meta(case_id)
        parties = self._parties(case_id)
        evidence = self._evidence(case_id)
        facts = self._facts(case_id)
        claim = self._claim(case_id)
        draft = self._draft(case_id)
        issues = self._issues(case_id)
        theories = self._legal_theories(case_id)
        conflicts, missing = self._analyst_side(case_id)
        pool = self.policy.pool_summary(case_id)
        history = self._history(case_id, conversation_id)
        excerpts = self._select_excerpts(case_id, user_message)
        from backend.application.pleading_readiness import PleadingReadinessService

        readiness = PleadingReadinessService(self.session).evaluate(case_id)

        return CaseConversationContext(
            case_title=case.title,
            case_status=case.status,
            workflow_status=wf_status,
            current_node=node_code,
            current_node_label=node_label,
            parties=parties,
            evidence=evidence,
            facts=facts,
            issues=issues,
            legal_theories=theories,
            conflicts=conflicts,
            missing_evidence=missing,
            claim=claim,
            draft=draft,
            material_pool={
                **pool,
                "note": "usable materials only participate in analysis",
            },
            pleading_readiness=readiness.model_dump(mode="json"),
            history=history,
            source_excerpts=excerpts,
        )

    def _workflow_meta(
        self, case_id: UUID
    ) -> tuple[str | None, str | None, str | None]:
        active = list(
            self.session.scalars(
                select(WorkflowInstance).where(
                    WorkflowInstance.case_id == case_id,
                    WorkflowInstance.status.notin_(
                        ["SUCCEEDED", "FAILED", "CANCELLED"]
                    ),
                )
            )
        )
        inst = active[0] if active else self.session.scalars(
            select(WorkflowInstance)
            .where(WorkflowInstance.case_id == case_id)
            .order_by(WorkflowInstance.created_at.desc())
        ).first()
        if inst is None:
            return None, None, None
        node = (
            self.session.get(WorkflowNode, inst.current_node_id)
            if inst.current_node_id
            else None
        )
        code = node.code if node else None
        return inst.status, code, code

    def _parties(self, case_id: UUID) -> list[dict[str, Any]]:
        rows = list(
            self.session.scalars(
                select(CaseParty)
                .where(
                    CaseParty.case_id == case_id,
                    CaseParty.is_current.is_(True),
                )
                .order_by(CaseParty.created_at.asc())
            )
        )
        role_zh = {
            "PLAINTIFF": "原告",
            "DEFENDANT": "被告",
            "THIRD_PARTY": "第三人",
            "OTHER": "其他",
        }
        out = []
        for i, p in enumerate(rows, start=1):
            out.append(
                {
                    "display_number": str(i),
                    "role": p.role,
                    "role_label": role_zh.get(p.role, p.role),
                    "name": p.name,
                    "layer": p.layer,
                    "version": p.version,
                    "layer_note": (
                        "已确认"
                        if p.layer == "CONFIRMED"
                        else (
                            "AI/律师候选，尚未确认"
                            if p.layer == "CANDIDATE"
                            else p.layer
                        )
                    ),
                }
            )
        return out

    def _evidence(self, case_id: UUID) -> list[dict[str, Any]]:
        rows = list(
            self.session.scalars(
                select(EvidenceItem)
                .where(
                    EvidenceItem.case_id == case_id,
                    EvidenceItem.is_current.is_(True),
                )
                .order_by(EvidenceItem.number.asc())
            )
        )
        out = []
        for e in rows:
            links = list(
                self.session.scalars(
                    select(EvidenceItemSpan).where(
                        EvidenceItemSpan.evidence_item_id == e.id,
                        EvidenceItemSpan.evidence_item_version == e.version,
                    )
                )
            )
            span_previews: list[str] = []
            for link in links[:3]:
                span = self.session.get(SourceSpan, link.source_span_id)
                if span and span.quote:
                    q = span.quote.strip()
                    if len(q) > 160:
                        q = q[:160] + "…"
                    span_previews.append(q)
            out.append(
                {
                    "display_number": str(e.number),
                    "title": e.title,
                    "summary": e.summary,
                    "category": e.category,
                    "acceptance": e.acceptance,
                    "version": e.version,
                    "acceptance_note": (
                        "已接受"
                        if e.acceptance == "ACCEPTED"
                        else (
                            "待确认"
                            if e.acceptance == "PENDING"
                            else e.acceptance
                        )
                    ),
                    "source_quote_previews": span_previews,
                }
            )
        return out

    def _facts(self, case_id: UUID) -> list[dict[str, Any]]:
        rows = list(
            self.session.scalars(
                select(Fact)
                .where(Fact.case_id == case_id, Fact.is_current.is_(True))
                .order_by(Fact.created_at.asc(), Fact.fact_key.asc())
            )
        )
        out = []
        for i, f in enumerate(rows, start=1):
            links = list(
                self.session.scalars(
                    select(FactEvidenceLink).where(FactEvidenceLink.fact_id == f.id)
                )
            )
            ev_refs = []
            for link in links:
                item = self.session.get(EvidenceItem, link.evidence_item_id)
                if item is not None:
                    ev_refs.append(
                        {
                            "display_number": str(item.number),
                            "title": item.title,
                            "evidence_version": link.evidence_item_version,
                        }
                    )
            out.append(
                {
                    "display_number": str(i),
                    "statement": f.statement,
                    "status": f.status,
                    "version": f.version,
                    "stale": f.stale,
                    "status_note": (
                        "已确认事实"
                        if f.status == "CONFIRMED"
                        else (
                            "AI 事实候选，尚未由律师确认"
                            if f.status == "CANDIDATE"
                            else f.status
                        )
                    ),
                    "supporting_evidence": ev_refs,
                }
            )
        return out

    def _issues(self, case_id: UUID) -> list[dict[str, Any]]:
        rows = list(
            self.session.scalars(
                select(Issue)
                .where(Issue.case_id == case_id, Issue.is_current.is_(True))
                .order_by(Issue.order_index.asc())
                .limit(20)
            )
        )
        return [
            {
                "statement": r.statement,
                "layer": r.layer,
                "status": r.status,
                "source_type": r.source_type,
                "note": (
                    "AI candidate analysis — NOT lawyer-confirmed fact"
                    if r.status == "CANDIDATE"
                    else "Lawyer-confirmed issue framing — NOT a proven fact"
                ),
            }
            for r in rows
        ]

    def _legal_theories(self, case_id: UUID) -> list[dict[str, Any]]:
        rows = list(
            self.session.scalars(
                select(LegalTheory)
                .where(LegalTheory.case_id == case_id)
                .order_by(LegalTheory.created_at.asc())
                .limit(20)
            )
        )
        return [
            {
                "summary": r.theory_summary,
                "layer": r.layer,
                "note": "AI candidate analysis — NOT lawyer-confirmed fact",
            }
            for r in rows
        ]

    def _analyst_side(
        self, case_id: UUID
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        inst = self.session.scalars(
            select(WorkflowInstance)
            .where(WorkflowInstance.case_id == case_id)
            .order_by(WorkflowInstance.created_at.desc())
        ).first()
        if inst is None:
            return [], []
        analyst = (inst.context_json or {}).get("analyst") or {}
        conflicts = list(analyst.get("conflicts") or [])
        missing = list(analyst.get("missing_evidence") or [])
        return conflicts[:20], missing[:20]

    def _claim(self, case_id: UUID) -> dict[str, Any] | None:
        row = self.session.scalars(
            select(ClaimDirection)
            .where(
                ClaimDirection.case_id == case_id,
                ClaimDirection.is_current.is_(True),
            )
            .order_by(ClaimDirection.created_at.desc())
        ).first()
        if row is None:
            return None
        payload = row.payload or {}
        return {
            "status": row.status,
            "version": row.version,
            "stale": row.stale,
            "status_note": (
                "已确认诉讼请求方向"
                if row.status == "CONFIRMED"
                else "AI/候选诉讼请求，尚未确认"
            ),
            "overall_strategy": payload.get("overall_strategy"),
            "claims": payload.get("claims") or [],
            "risks": payload.get("risks") or [],
            "missing_confirmations": payload.get("missing_confirmations") or [],
        }

    def _draft(self, case_id: UUID) -> dict[str, Any] | None:
        row = self.session.scalars(
            select(DocumentDraft)
            .where(DocumentDraft.case_id == case_id)
            .order_by(DocumentDraft.version.desc())
        ).first()
        if row is None:
            return None
        body = row.body_structured_json or {}
        # Key sections only — avoid dumping full draft
        keys = [
            "parties",
            "claims",
            "facts",
            "evidence_directory",
            "claim_basis",
            "prayer",
        ]
        sections = {k: body.get(k) for k in keys if k in body}
        return {
            "version": row.version,
            "status": row.status,
            "doc_type": row.doc_type,
            "key_sections": sections,
        }

    def _history(
        self, case_id: UUID, conversation_id: UUID | None
    ) -> list[ConversationTurn]:
        rows = list(
            self.session.scalars(
                select(AgentMessage)
                .where(
                    AgentMessage.case_id == case_id,
                    AgentMessage.role.in_(["USER", "AGENT"]),
                )
                .order_by(AgentMessage.created_at.desc())
                .limit(80)
            )
        )
        rows.reverse()
        filtered: list[AgentMessage] = []
        for m in rows:
            meta = m.parsed_intent_json or {}
            if conversation_id is not None:
                cid = meta.get("conversation_id")
                if cid and str(cid) != str(conversation_id):
                    continue
            filtered.append(m)
        # Drop the trailing current USER message (just persisted, unanswered)
        if filtered and filtered[-1].role == "USER":
            filtered = filtered[:-1]
        recent = filtered[-self.MAX_HISTORY :]
        return [
            ConversationTurn(role=m.role, content=(m.content or "")[:2000])
            for m in recent
        ]

    def _select_excerpts(
        self,
        case_id: UUID,
        user_message: str,
    ) -> list[SourceExcerpt]:
        """Pull necessary SourceSpan quotes when the question needs source grounding."""
        text = user_message or ""
        need_source = bool(
            re.search(
                r"(第\s*[一二三四五六七八九十百0-9]+条|原文|怎么写|条款|"
                r"付款条件|违约|服务费|金额|证明什么|重新看|理解错)",
                text,
            )
        )
        want_numbers = [int(n) for n in re.findall(r"证据\s*([0-9]+)", text)]
        want_accepted = not need_source and not want_numbers

        items = list(
            self.session.scalars(
                select(EvidenceItem)
                .where(
                    EvidenceItem.case_id == case_id,
                    EvidenceItem.is_current.is_(True),
                )
                .order_by(EvidenceItem.number.asc())
            )
        )
        selected: list[EvidenceItem] = []
        for item in items:
            num = None
            try:
                num = int(str(item.number))
            except ValueError:
                pass
            if want_numbers and num in want_numbers:
                selected.append(item)
            elif need_source and item.acceptance == "ACCEPTED":
                selected.append(item)
            elif want_accepted and item.acceptance == "ACCEPTED":
                selected.append(item)
        if not selected and need_source:
            selected = [i for i in items if i.acceptance in {"ACCEPTED", "PENDING"}][:4]

        excerpts: list[SourceExcerpt] = []
        for item in selected:
            if len(excerpts) >= self.MAX_EXCERPTS:
                break
            links = list(
                self.session.scalars(
                    select(EvidenceItemSpan).where(
                        EvidenceItemSpan.evidence_item_id == item.id,
                        EvidenceItemSpan.evidence_item_version == item.version,
                    )
                )
            )
            for link in links:
                if len(excerpts) >= self.MAX_EXCERPTS:
                    break
                span = self.session.get(SourceSpan, link.source_span_id)
                if span is None or not span.quote:
                    continue
                ec = self.session.get(ExtractedContent, span.extracted_content_id)
                if ec is None or ec.status != "SUCCEEDED":
                    continue
                from backend.models import CaseMaterial

                material = self.session.get(CaseMaterial, span.material_id)
                if material is None or material.life_status == "VOID":
                    continue
                quote = span.quote.strip()
                if len(quote) > self.MAX_QUOTE_CHARS:
                    quote = quote[: self.MAX_QUOTE_CHARS] + "…"
                # Keyword filter when asking about specific article
                if "第六条" in text or "第6条" in text:
                    if not re.search(r"第六条|第\s*6\s*条|6\s*、", quote):
                        # keep anyway if title hints fee/payment
                        title = item.title or ""
                        if not re.search(r"第六|服务费|支付|条款", title):
                            continue
                excerpts.append(
                    SourceExcerpt(
                        display_evidence=f"证据{item.number}",
                        quote=quote,
                        page=span.page,
                        paragraph=span.paragraph,
                        material_filename=material.filename,
                    )
                )
        return excerpts
