"""Build semantic StructuredPleadingInput for Pleading Quality V1."""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import CaseMaterial, EvidenceItemSpan, SourceSpan
from backend.schemas.case_analyst import EvidenceRef
from backend.schemas.pleading_quality import (
    MaterialEvidenceGroup,
    StructuredFactItem,
    StructuredPleadingInput,
)
from backend.skills.pleading_writer import (
    AcceptedEvidenceView,
    ClaimDirectionView,
    ConfirmedFactView,
    PartyView,
)

_CONTRACT_PARTY_PATTERNS = (
    re.compile(r"甲方[：:\s]*([^\s，,；;。]{2,40})"),
    re.compile(r"合同签约[方甲乙]*[：:\s]*([^\s，,；;。]{2,40})"),
)

_CATEGORY_RULES: list[tuple[str, tuple[str, ...]]] = [
    (
        "LIABILITY",
        ("债务加入", "债务承担", "承继", "责任桥梁", "加入债务", "确认承担"),
    ),
    ("JURISDICTION", ("管辖", "人民法院", "住所地", "履行地")),
    ("DEMAND", ("催告", "催款", "律师函")),
    ("DUE", ("已到期", "到期", "逾期", "迟延")),
    ("OUTSTANDING", ("未付", "尚欠", "余额", "欠付", "剩余应支付")),
    ("PAYMENT_HISTORY", ("已付", "已付款", "支付了", "已支付")),
    ("CONTRACT", ("合同", "签订", "签约", "甲方", "乙方", "协议")),
    ("SERVICE_TERMS", ("服务范围", "工作内容", "收费标准", "费率", "封顶", "8%")),
    ("PERFORMANCE", ("已交付", "已提交", "已完成", "交付", "提交成果", "履约")),
    ("ACCEPTANCE", ("签收", "验收", "确认完成", "接收")),
    ("PAYMENT_TERM", ("付款条件", "支付条件", "结清", "成果提交后", "验收后")),
    ("AMOUNT", ("优化金额", "计算", "费率", "封顶", "百分比", "总价", "结算", "应付")),
]

_MONEY_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)")


class PleadingStructuredInputBuilder:
    def __init__(self, session: Session) -> None:
        self.session = session

    def build(
        self,
        *,
        parties: list[PartyView],
        facts: list[ConfirmedFactView],
        claim: ClaimDirectionView,
        evidence: list[AcceptedEvidenceView],
    ) -> StructuredPleadingInput:
        categorized = [self._categorize_fact(f) for f in facts]
        buckets: dict[str, list[StructuredFactItem]] = {
            "CONTRACT": [],
            "SERVICE_TERMS": [],
            "PERFORMANCE": [],
            "ACCEPTANCE": [],
            "LIABILITY": [],
            "AMOUNT": [],
            "PAYMENT_HISTORY": [],
            "OUTSTANDING": [],
            "PAYMENT_TERM": [],
            "DUE": [],
            "DEMAND": [],
            "JURISDICTION": [],
            "BACKGROUND": [],
        }
        for item in categorized:
            buckets[item.category].append(item)

        contract_parties = self._extract_contract_parties(facts)
        defendant_names = [p.name for p in parties if p.role == "DEFENDANT"]
        bridge_required = bool(contract_parties) and not any(
            self._name_overlap(d, c) for d in defendant_names for c in contract_parties
        )

        allowed_amounts: list[float] = []
        for c in claim.payload.get("claims") or []:
            if c.get("amount") is not None:
                allowed_amounts.append(float(c["amount"]))
        for f in facts:
            stmt = f.statement or ""
            for m in _MONEY_RE.finditer(stmt):
                try:
                    val = float(m.group(1))
                except ValueError:
                    continue
                tail = stmt[m.end() : m.end() + 4]
                if "万" in tail or "万元" in stmt[m.start() : m.end() + 3]:
                    val *= 10000
                allowed_amounts.append(val)

        ev_dir = self._build_evidence_directory(evidence, categorized)
        finalized_court = self._extract_finalized_court(facts)

        return StructuredPleadingInput(
            parties=[
                {
                    "party_key": str(p.party_key),
                    "role": p.role,
                    "name": p.name,
                    "party_type": p.party_type,
                    "identifiers_json": p.identifiers_json or {},
                }
                for p in parties
            ],
            claims_payload=dict(claim.payload or {}),
            contract_facts=buckets["CONTRACT"],
            service_term_facts=buckets["SERVICE_TERMS"],
            performance_facts=buckets["PERFORMANCE"],
            acceptance_facts=buckets["ACCEPTANCE"],
            liability_facts=buckets["LIABILITY"],
            amount_facts=buckets["AMOUNT"],
            payment_history_facts=buckets["PAYMENT_HISTORY"],
            outstanding_facts=buckets["OUTSTANDING"],
            payment_term_facts=buckets["PAYMENT_TERM"],
            due_facts=buckets["DUE"],
            demand_facts=buckets["DEMAND"],
            jurisdiction_facts=buckets["JURISDICTION"],
            background_facts=buckets["BACKGROUND"],
            evidence_directory=ev_dir,
            contract_party_names=contract_parties,
            defendant_names=defendant_names,
            liability_bridge_required=bridge_required,
            allowed_amounts=sorted(set(round(a, 2) for a in allowed_amounts)),
            allowed_party_names=sorted(
                set([p.name for p in parties] + contract_parties)
            ),
            allowed_numbers=[g.display_number for g in ev_dir],
            finalized_court_name=finalized_court,
        )

    def _categorize_fact(self, fact: ConfirmedFactView) -> StructuredFactItem:
        stmt = fact.statement or ""
        category = "BACKGROUND"
        for cat, kws in _CATEGORY_RULES:
            if any(k in stmt for k in kws):
                category = cat
                break
        return StructuredFactItem(
            fact_key=fact.fact_key,
            fact_version=fact.fact_version,
            statement=stmt.strip(),
            category=category,
            evidence_refs=list(fact.evidence_refs),
        )

    def _build_evidence_directory(
        self,
        evidence: list[AcceptedEvidenceView],
        facts: list[StructuredFactItem],
    ) -> list[MaterialEvidenceGroup]:
        purpose_by_ev: dict[tuple[UUID, int], set[str]] = {}
        for item in facts:
            for ref in item.evidence_refs:
                key = (ref.evidence_item_id, ref.evidence_item_version)
                purpose_by_ev.setdefault(key, set()).add(item.statement[:120])

        # material_id -> group data
        groups: dict[UUID, dict[str, Any]] = {}
        for ev in evidence:
            mat_id, mat_name = self._resolve_material(ev)
            key = mat_id or ev.evidence_item_id
            if key not in groups:
                groups[key] = {
                    "material_id": mat_id or ev.evidence_item_id,
                    "material_filename": mat_name or ev.title,
                    "title": ev.title,
                    "purposes": set(),
                    "evidence_refs": [],
                }
            g = groups[key]
            g["title"] = max(g["title"], ev.title, key=len)
            g["evidence_refs"].append(
                EvidenceRef(
                    evidence_item_id=ev.evidence_item_id,
                    evidence_item_version=ev.evidence_item_version,
                )
            )
            purposes = purpose_by_ev.get(
                (ev.evidence_item_id, ev.evidence_item_version), set()
            )
            g["purposes"].update(purposes)
            if ev.summary:
                g["purposes"].add(ev.summary[:120])

        out: list[MaterialEvidenceGroup] = []
        for i, g in enumerate(groups.values(), start=1):
            purposes = _dedupe_purposes(sorted(p for p in g["purposes"] if p))
            merged = _merge_proof_purpose_text(purposes) if purposes else "证明与本案相关事实"
            title = _material_display_title(g["material_filename"], g["title"])
            out.append(
                MaterialEvidenceGroup(
                    material_id=g["material_id"],
                    material_filename=g["material_filename"],
                    display_number=str(i),
                    title=title,
                    proof_purposes=purposes or [merged],
                    evidence_refs=g["evidence_refs"],
                )
            )
        return out

    def _resolve_material(
        self, ev: AcceptedEvidenceView
    ) -> tuple[UUID | None, str | None]:
        span_link = self.session.scalars(
            select(EvidenceItemSpan).where(
                EvidenceItemSpan.evidence_item_id == ev.evidence_item_id,
                EvidenceItemSpan.evidence_item_version == ev.evidence_item_version,
            )
        ).first()
        if span_link is None:
            return None, None
        span = self.session.get(SourceSpan, span_link.source_span_id)
        if span is None:
            return None, None
        material = self.session.get(CaseMaterial, span.material_id)
        if material is None:
            return None, None
        return material.id, material.filename

    def _extract_contract_parties(self, facts: list[ConfirmedFactView]) -> list[str]:
        names: list[str] = []
        for f in facts:
            for pat in _CONTRACT_PARTY_PATTERNS:
                for m in pat.finditer(f.statement or ""):
                    name = m.group(1).strip().rstrip("。；;")
                    for sep in ("与乙方", "与甲方", "与丙方", "签订", "签署"):
                        if sep in name:
                            name = name.split(sep, 1)[0].strip()
                    if len(name) >= 2 and len(name) <= 30 and name not in names:
                        names.append(name)
        return names

    def _extract_finalized_court(self, facts: list[ConfirmedFactView]) -> str | None:
        """Only concrete court names — not jurisdiction agreement clauses."""
        concrete = re.compile(
            r"([\u4e00-\u9fff]{2,12}[市区县][^，。；\n]{0,10}人民法院|"
            r"最高人民法院|[\u4e00-\u9fff]{2,12}高级人民法院|"
            r"[\u4e00-\u9fff]{2,12}中级人民法院)"
        )
        generic = ("住所地", "履行地", "标的物", "协议选择", "约定由")
        for fact in facts:
            stmt = fact.statement or ""
            for match in concrete.finditer(stmt):
                name = match.group(1)
                if any(token in name for token in generic):
                    continue
                if "约定" in stmt and any(
                    token in stmt for token in ("住所地", "履行地", "标的物")
                ):
                    continue
                return name
        return None

    @staticmethod
    def _name_overlap(a: str, b: str) -> bool:
        a, b = (a or "").strip(), (b or "").strip()
        return bool(a and b and (a in b or b in a))

    def ordered_fact_items(self, structured: StructuredPleadingInput) -> list[StructuredFactItem]:
        """Lawyer narrative order — only categories with facts."""
        order = [
            structured.contract_facts,
            structured.service_term_facts,
            structured.performance_facts,
            structured.acceptance_facts,
            structured.amount_facts,
            structured.payment_history_facts,
            structured.outstanding_facts,
            structured.payment_term_facts,
            structured.due_facts,
            structured.demand_facts,
            structured.liability_facts,
            structured.background_facts,
        ]
        seen: set[tuple[UUID, int]] = set()
        out: list[StructuredFactItem] = []
        for bucket in order:
            for item in bucket:
                key = (item.fact_key, item.fact_version)
                if key in seen:
                    continue
                seen.add(key)
                out.append(item)
        return out


def _normalize_purpose(text: str) -> str:
    t = (text or "").strip()
    for prefix in ("证明", "用于证明", "以证明"):
        if t.startswith(prefix):
            t = t[len(prefix) :].strip()
    return t.rstrip("。；;")


def _dedupe_purposes(purposes: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for p in purposes:
        norm = _normalize_purpose(p)
        if not norm or norm in seen:
            continue
        if any(norm in s or s in norm for s in seen):
            continue
        seen.add(norm)
        out.append(norm)
    return out


def _merge_proof_purpose_text(purposes: list[str]) -> str:
    if not purposes:
        return "证明与本案相关事实"
    if len(purposes) == 1:
        return f"证明{purposes[0]}"
    return "证明" + "、".join(purposes[:5])


def _material_display_title(filename: str, fallback: str) -> str:
    name = (filename or fallback or "").strip()
    for suffix in (".pdf", ".PDF", ".docx", ".DOCX"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name or fallback or "案件材料"
