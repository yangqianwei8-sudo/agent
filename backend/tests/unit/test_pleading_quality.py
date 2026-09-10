"""Pleading Quality V1 — unit tests for structured input, validator, evidence grouping."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from backend.application.pleading_draft_validator import PleadingDraftValidator
from backend.application.pleading_structured_input import (
    PleadingStructuredInputBuilder,
    _dedupe_purposes,
)
from backend.domain.services import DomainService
from backend.schemas.case_analyst import EvidenceRef
from backend.schemas.claim_direction_proposal import FactRef
from backend.schemas.pleading_quality import StructuredFactItem, StructuredPleadingInput
from backend.schemas.pleading_writer import (
    ClaimLine,
    EvidenceDirectoryItem,
    FactBlock,
    PleadingWriterEngineResult,
)
from backend.skills.civil_complaint_renderer import render_civil_complaint
from backend.skills.pleading_writer import (
    AcceptedEvidenceView,
    ClaimDirectionView,
    DeterministicPleadingWriterStub,
)


def _fact(stmt: str, *, category: str = "BACKGROUND") -> StructuredFactItem:
    fk = uuid.uuid4()
    return StructuredFactItem(
        fact_key=fk,
        fact_version=1,
        statement=stmt,
        category=category,
        evidence_refs=[],
    )


def test_dedupe_purposes_merges_semantic_duplicates() -> None:
    out = _dedupe_purposes(
        [
            "证明合同关系",
            "证明合同关系",
            "证明收费标准",
            "证明收费条款",
        ]
    )
    assert "合同关系" in out[0]
    assert len(out) <= 3


def test_validator_rejects_speculative_performance() -> None:
    structured = StructuredPleadingInput(
        performance_facts=[_fact("2024年6月原告向被告提交优化成果。")],
        claims_payload={"claims": [{"claim_type": "PAYMENT", "amount": 150000}]},
        allowed_amounts=[150000],
    )
    result = PleadingWriterEngineResult(
        title="民事起诉状",
        parties_section="原告：A\n被告：B",
        claims=[
            ClaimLine(
                claim_type="PAYMENT",
                text="一、支付150000元。",
                amount=150000,
                currency="CNY",
            )
        ],
        claims_section="诉讼请求：\n一、支付150000元。",
        fact_blocks=[
            FactBlock(
                block_id="fact-001",
                text="如原告已提交成果",
                fact_refs=[
                    FactRef(
                        fact_key=structured.performance_facts[0].fact_key,
                        fact_version=1,
                    )
                ],
                evidence_refs=[
                    EvidenceRef(
                        evidence_item_id=uuid.uuid4(),
                        evidence_item_version=1,
                    )
                ],
            )
        ],
        facts_and_reasons_section="事实与理由：\n如原告已提交成果",
        evidence_directory=[],
        court_section="此致\n【待确认有管辖权的人民法院】",
        signature_section="具状人：A\n日期：【待律师确认】",
    )
    full = render_civil_complaint(result)
    v = PleadingDraftValidator().validate(result, structured, full_text=full)
    assert not v.passed
    assert any(i.code == "PERFORMANCE_NARRATIVE_SUPPORTED" for i in v.errors)


def test_validator_requires_liability_bridge() -> None:
    structured = StructuredPleadingInput(
        liability_bridge_required=True,
        contract_party_names=["四川富茂置业有限公司"],
        defendant_names=["中梁地产"],
        liability_facts=[_fact("中梁地产债务加入并确认承担付款义务。")],
        claims_payload={"claims": [{"claim_type": "PAYMENT", "amount": 150000}]},
        allowed_amounts=[150000],
        allowed_party_names=["四川维海科技有限公司", "中梁地产", "四川富茂置业有限公司"],
    )
    bad = PleadingWriterEngineResult(
        title="民事起诉状",
        parties_section="原告：四川维海科技有限公司\n被告：中梁地产",
        claims=[
            ClaimLine(
                claim_type="PAYMENT",
                text="一、支付150000元。",
                amount=150000,
                currency="CNY",
            )
        ],
        claims_section="诉讼请求：\n一、支付150000元。",
        fact_blocks=[
            FactBlock(
                block_id="fact-001",
                text="被告依法应承担付款责任。",
                fact_refs=[
                    FactRef(
                        fact_key=structured.liability_facts[0].fact_key,
                        fact_version=1,
                    )
                ],
                evidence_refs=[
                    EvidenceRef(
                        evidence_item_id=uuid.uuid4(),
                        evidence_item_version=1,
                    )
                ],
            )
        ],
        facts_and_reasons_section="事实与理由：\n被告依法应承担付款责任。",
        evidence_directory=[],
        court_section="此致\n【待确认有管辖权的人民法院】",
        signature_section="具状人：四川维海科技有限公司\n日期：【待律师确认】",
    )
    v = PleadingDraftValidator().validate(bad, structured, full_text=render_civil_complaint(bad))
    assert not v.passed
    assert any(i.code == "LIABILITY_NARRATIVE_SUPPORTED" for i in v.errors)


def test_evidence_directory_one_material_four_items(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    """Permanent regression: one contract PDF → one evidence directory row."""
    svc = DomainService(db_session)
    case = svc.create_case(title="Evidence Grouping", owner_user_id=owner_id)
    contract_text = (
        "设计优化咨询服务合同\n甲方：四川富茂置业有限公司\n"
        "服务范围：设计优化\n收费标准：8%封顶30万\n付款：成果提交后支付"
    )
    material = svc.register_material(
        case_id=case.id,
        filename="设计优化咨询服务合同.pdf",
        mime="application/pdf",
        byte_size=len(contract_text),
        content_hash=f"h-{uuid.uuid4().hex[:10]}",
        storage_key=f"k-{uuid.uuid4().hex[:10]}",
        created_by=actor_id,
    )
    ec = svc.create_extracted_content(
        material_id=material.id,
        extraction_method="pdfplumber",
        extraction_version="v1",
        full_text=contract_text,
        actor_id=actor_id,
    )
    parts = [
        "设计优化咨询服务合同\n甲方：四川富茂置业有限公司\n",
        "服务范围：设计优化\n",
        "收费标准：8%封顶30万\n",
        "付款：成果提交后支付",
    ]
    spans = []
    offset = 0
    for part in parts:
        start = offset
        end = offset + len(part)
        spans.append(
            svc.create_source_span(
                material_id=material.id,
                extracted_content_id=ec.id,
                character_start=start,
                character_end=end,
                quote=part,
                extraction_method="pdfplumber",
                extraction_version="v1",
            )
        )
        offset = end
    evidence_views: list[AcceptedEvidenceView] = []
    for i, span in enumerate(spans, start=1):
        ev = svc.create_evidence_item(
            case_id=case.id,
            number=str(i),
            title=f"合同摘录{i}",
            category="CONTRACT",
            summary=span.quote,
            source_span_ids=[span.id],
            actor_id=actor_id,
        )
        svc.accept_evidence(ev.id, actor_id=actor_id)
        cur = svc.repo.get_current_evidence(ev.id)
        assert cur is not None
        evidence_views.append(
            AcceptedEvidenceView(
                evidence_item_id=cur.id,
                evidence_item_version=cur.version,
                number=cur.number,
                title=cur.title,
                summary=cur.summary,
                category=cur.category,
            )
        )

    builder = PleadingStructuredInputBuilder(db_session)
    structured = builder.build(
        parties=[],
        facts=[],
        claim=ClaimDirectionView(
            claim_direction_key=uuid.uuid4(),
            claim_direction_version=1,
            payload={},
            status="CONFIRMED",
            stale=False,
        ),
        evidence=evidence_views,
    )
    assert len(structured.evidence_directory) == 1
    grp = structured.evidence_directory[0]
    assert len(grp.evidence_refs) == 4
    assert "设计优化咨询服务合同" in grp.title


def _seed_fumao_ready(
    session: Session, *, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> tuple:
    from backend.models import Fact

    svc = DomainService(session)
    case = svc.create_case(title="富茂中梁READY回归", owner_user_id=owner_id)
    plaintiff = svc.create_party(
        case_id=case.id,
        role="PLAINTIFF",
        name="四川维海科技有限公司",
        party_type="ORG",
        actor_id=actor_id,
    )
    defendant = svc.create_party(
        case_id=case.id,
        role="DEFENDANT",
        name="中梁地产",
        party_type="ORG",
        actor_id=actor_id,
    )
    svc.confirm_party(plaintiff.party_key, actor_id=actor_id)
    svc.confirm_party(defendant.party_key, actor_id=actor_id)

    statements = [
        "合同签约甲方：四川富茂置业有限公司，乙方四川维海科技有限公司。双方签订《设计优化咨询服务合同》。",
        "合同约定服务范围包括设计优化咨询及成果提交。",
        "合同约定服务费按经确认的优化金额的8%计取，并以30万元为上限。",
        "中梁地产向原告出具债务加入确认函，确认加入债务并承担全部服务费付款义务。",
        "2024年6月15日，原告向四川富茂置业有限公司提交优化成果，四川富茂置业有限公司签收确认。",
        "经确认的优化金额为250万元。",
        "按8%计算应付服务费200000元。",
        "被告已支付50000元。",
        "尚欠服务费150000元未付。",
        "合同约定成果提交并确认后付款条件成就。",
        "付款义务已到期，应于2024年7月1日前结清。",
        "2024年8月1日原告向中梁地产发送催款函。",
    ]
    facts: list[Fact] = []
    evidences = []
    for i, text in enumerate(statements):
        material = svc.register_material(
            case_id=case.id,
            filename=f"fm-{i}.pdf",
            mime="application/pdf",
            byte_size=len(text),
            content_hash=f"h-{uuid.uuid4().hex[:10]}",
            storage_key=f"k-{uuid.uuid4().hex[:10]}",
            created_by=actor_id,
        )
        ec = svc.create_extracted_content(
            material_id=material.id,
            extraction_method="pdfplumber",
            extraction_version="v1",
            full_text=text,
            actor_id=actor_id,
        )
        span = svc.create_source_span(
            material_id=material.id,
            extracted_content_id=ec.id,
            character_start=0,
            character_end=len(text),
            quote=text,
            extraction_method="pdfplumber",
            extraction_version="v1",
        )
        evidence = svc.create_evidence_item(
            case_id=case.id,
            number=str(i + 1),
            title=f"证据{i + 1}",
            category="CONTRACT",
            summary=text,
            source_span_ids=[span.id],
            actor_id=actor_id,
        )
        svc.accept_evidence(evidence.id, actor_id=actor_id)
        evidence = svc.repo.get_current_evidence(evidence.id)
        assert evidence is not None
        fact = svc.propose_fact(
            case_id=case.id,
            statement=text,
            evidence_links=[
                {
                    "evidence_item_id": evidence.id,
                    "evidence_item_version": evidence.version,
                }
            ],
            actor_id=actor_id,
        )
        svc.confirm_fact(fact.fact_key, actor_id=actor_id)
        fact = svc.repo.get_current_fact(fact.fact_key)
        assert fact is not None
        facts.append(fact)
        evidences.append(evidence)

    claim = svc.create_claim_direction(
        case_id=case.id,
        payload={
            "overall_strategy": "请求中梁支付剩余服务费",
            "claims": [
                {
                    "claim_type": "PAYMENT",
                    "description": "剩余服务费",
                    "amount": 150000,
                    "currency": "CNY",
                    "calculation_basis": "250万×8%-5万",
                    "supporting_fact_ids": [str(f.fact_key) for f in facts],
                }
            ],
        },
        actor_id=actor_id,
    )
    claim = svc.confirm_claim_direction(claim.claim_direction_key, actor_id=actor_id)
    return case, [plaintiff.party_key, defendant.party_key], facts, evidences, claim


def test_fumao_zhongliang_ready_draft_quality(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    from backend.application.pleading_readiness import PleadingReadinessService
    from backend.application.pleading_writer import PleadingWriterService
    from backend.schemas.pleading_readiness import ReadinessStatus

    case, parties, facts, evidences, claim = _seed_fumao_ready(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    readiness = PleadingReadinessService(db_session).evaluate(case.id)
    assert readiness.status == ReadinessStatus.READY, [
        i.code for i in readiness.blocking_issues
    ]

    result = PleadingWriterService(db_session).write(
        case_id=case.id,
        claim_direction_ref={
            "claim_direction_key": str(claim.claim_direction_key),
            "claim_direction_version": claim.version,
        },
        confirmed_fact_refs=[
            {"fact_key": str(f.fact_key), "fact_version": f.version} for f in facts
        ],
        accepted_evidence_refs=[
            {"evidence_item_id": str(e.id), "evidence_item_version": e.version}
            for e in evidences
        ],
        confirmed_party_keys=parties,
        actor_id=actor_id,
    )
    assert result.draft is not None
    body = result.draft.body_structured_json
    text = body["full_text"]
    assert "富茂" in text or "四川富茂置业有限公司" in text
    assert "中梁" in text
    assert "债务加入" in text or "承担" in text
    assert "150000" in text.replace(",", "") or "150,000" in text
    assert "200000" in text.replace(",", "") or "200,000" in text or "20万元" in text
    assert "150000" in text.replace(",", "") or "150,000" in text or "15万元" in text
    assert "2024年6月15日" in text or "提交优化成果" in text
    v = body.get("validation") or {}
    assert v.get("passed") is True
    assert v.get("repair_count", 0) == 0


class _BadFirstEngine:
    """Returns bad draft on first write; repair delegates to deterministic stub."""

    def __init__(self, bad: PleadingWriterEngineResult) -> None:
        self._bad = bad
        self._stub = DeterministicPleadingWriterStub()
        self._first = True

    def write(self, inp, *, parties, facts, claim, evidence, structured=None):
        if self._first:
            self._first = False
            return self._bad.model_copy(deep=True)
        return self._stub.write(
            inp,
            parties=parties,
            facts=facts,
            claim=claim,
            evidence=evidence,
            structured=structured,
        )

    def repair(self, inp, *, parties, facts, claim, evidence, structured, errors, prior):
        return self._stub.repair(
            inp,
            parties=parties,
            facts=facts,
            claim=claim,
            evidence=evidence,
            structured=structured,
            errors=errors,
            prior=prior,
        )


def test_deterministic_repair_once(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    from backend.application.pleading_writer import PleadingWriterService
    from backend.tests.integration.test_pleading_writer import _seed_writer_world, _writer_args

    _, case, parties, facts, evidences, claim = _seed_writer_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    bad = PleadingWriterEngineResult(
        title="民事起诉状",
        parties_section="原告：x\n被告：y",
        claims=[
            ClaimLine(
                claim_type="PAYMENT",
                text="一、判令被告向原告支付剩余服务费人民币700,000元。",
                amount=700000,
                currency="CNY",
            ),
            ClaimLine(
                claim_type="PROCEDURAL",
                text="二、本案诉讼费用由被告承担。",
                amount=None,
                currency=None,
            ),
        ],
        claims_section=(
            "诉讼请求：\n"
            "一、判令被告向原告支付剩余服务费人民币700,000元。\n"
            "二、本案诉讼费用由被告承担。"
        ),
        fact_blocks=[
            FactBlock(
                block_id="fact-001",
                text="如原告已提交成果",
                fact_refs=[FactRef(fact_key=facts[0].fact_key, fact_version=facts[0].version)],
                evidence_refs=[
                    EvidenceRef(
                        evidence_item_id=evidences[0].id,
                        evidence_item_version=evidences[0].version,
                    )
                ],
            )
        ],
        facts_and_reasons_section="事实与理由：\n如原告已提交成果",
        evidence_directory=[
            EvidenceDirectoryItem(
                display_number="1",
                title="e1",
                proof_purpose="p",
                evidence_item_id=evidences[0].id,
                evidence_item_version=evidences[0].version,
            )
            for _ in range(len(evidences))
        ],
        court_section="此致\n【待确认有管辖权的人民法院】",
        signature_section="具状人：x\n日期：【待律师确认】",
    )
    svc = PleadingWriterService(db_session, engine=_BadFirstEngine(bad))
    result = svc.write(
        **_writer_args(case, parties, facts, evidences, claim),
        actor_id=actor_id,
    )
    assert result.draft is not None
    body = result.draft.body_structured_json
    assert "如原告已" not in body["full_text"]
    assert body.get("validation", {}).get("repair_count") == 1
