"""Pleading Quality V1 — validator matrix, evidence merge, incident regression."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.application.pleading_draft_validator import PleadingDraftValidator
from backend.application.pleading_writer import PleadingWriterService
from backend.domain.services import DomainService
from backend.models import EvidenceItem
from backend.schemas.case_analyst import EvidenceRef
from backend.schemas.claim_direction_proposal import FactRef
from backend.schemas.pleading_quality import (
    MaterialEvidenceGroup,
    PleadingDraftValidationError,
    StructuredFactItem,
    StructuredPleadingInput,
)
from backend.schemas.pleading_writer import (
    ClaimDirectionRef,
    ClaimLine,
    EvidenceDirectoryItem,
    FactBlock,
    PleadingWriterEngineResult,
)
from backend.skills.civil_complaint_renderer import render_civil_complaint
from backend.skills.pleading_writer import (
    DeterministicPleadingWriterStub,
)
from backend.tests.integration.test_pleading_readiness import _add_confirmed_fact
from backend.tests.integration.test_pleading_writer import _seed_writer_world, _writer_args


def _structured_payment(*, amount: float = 700000) -> StructuredPleadingInput:
    fk = uuid.uuid4()
    return StructuredPleadingInput(
        claims_payload={
            "claims": [
                {
                    "claim_type": "PAYMENT",
                    "description": "剩余服务费",
                    "amount": amount,
                    "currency": "CNY",
                }
            ]
        },
        contract_facts=[
            StructuredFactItem(
                fact_key=fk,
                fact_version=1,
                statement="双方签订设计合同。",
                category="CONTRACT",
            )
        ],
        performance_facts=[
            StructuredFactItem(
                fact_key=uuid.uuid4(),
                fact_version=1,
                statement="原告已向被告交付设计成果并经签收。",
                category="PERFORMANCE",
            )
        ],
        amount_facts=[
            StructuredFactItem(
                fact_key=uuid.uuid4(),
                fact_version=1,
                statement=f"尚欠服务费{int(amount)}元。",
                category="AMOUNT",
            )
        ],
        allowed_amounts=[amount, 1000000, 300000],
        allowed_party_names=["原告设计公司", "被告建设公司"],
        evidence_directory=[
            MaterialEvidenceGroup(
                material_id=uuid.uuid4(),
                material_filename="contract.pdf",
                display_number="1",
                title="设计合同",
                proof_purposes=["证明合同关系"],
            )
        ],
    )


def _base_result(
    *,
    amount: float = 700000,
    facts_text: str = "原告已向被告交付设计成果并经签收。",
    claims_text: str | None = None,
    structured: StructuredPleadingInput | None = None,
) -> PleadingWriterEngineResult:
    fact_key = uuid.uuid4()
    if structured and structured.performance_facts:
        pf = structured.performance_facts[0]
        fact_key = pf.fact_key
    if claims_text is None:
        claims_text = f"一、判令被告向原告支付剩余服务费人民币{int(amount):,}元。"
    return PleadingWriterEngineResult(
        title="民事起诉状",
        parties_section="原告：原告设计公司\n被告：被告建设公司",
        claims=[
            ClaimLine(
                claim_type="PAYMENT",
                text=claims_text,
                amount=amount,
                currency="CNY",
            ),
            ClaimLine(
                claim_type="PROCEDURAL",
                text="二、本案诉讼费用由被告承担。",
            ),
        ],
        claims_section=f"诉讼请求：\n{claims_text}\n二、本案诉讼费用由被告承担。",
        fact_blocks=[
            FactBlock(
                block_id="fact-001",
                text=facts_text,
                fact_refs=[FactRef(fact_key=fact_key, fact_version=1)],
                evidence_refs=[
                    EvidenceRef(evidence_item_id=uuid.uuid4(), evidence_item_version=1)
                ],
            )
        ],
        facts_and_reasons_section=f"事实与理由：\n{facts_text}",
        evidence_directory=[
            EvidenceDirectoryItem(
                display_number="1",
                title="设计合同",
                proof_purpose="证明合同关系",
                evidence_item_id=uuid.uuid4(),
                evidence_item_version=1,
            )
        ],
        evidence_section="证据目录\n\n1. 《设计合同》",
        court_section="此致\n【待确认有管辖权的人民法院】",
        signature_section="具状人：【待律师确认】\n日期：【待律师确认】",
        used_fact_refs=[FactRef(fact_key=fact_key, fact_version=1)],
        used_evidence_refs=[
            EvidenceRef(evidence_item_id=uuid.uuid4(), evidence_item_version=1)
        ],
        used_claim_direction_ref=ClaimDirectionRef(
            claim_direction_key=uuid.uuid4(), claim_direction_version=1
        ),
        warnings=[],
    )


def _validate(result: PleadingWriterEngineResult, structured: StructuredPleadingInput):
    full = render_civil_complaint(result)
    return PleadingDraftValidator().validate(result, structured, full_text=full)


# ----- Validator matrix A–H -----


def test_a_claim_amount_mismatch_fails() -> None:
    structured = _structured_payment(amount=700000)
    result = _base_result(
        amount=800000,
        claims_text="一、判令被告支付人民币800000元。",
        structured=structured,
    )
    out = _validate(result, structured)
    assert not out.passed
    assert any(i.code == "CLAIM_AMOUNT_MATCH" for i in out.errors)


def test_b_extra_amount_in_body_fails() -> None:
    structured = _structured_payment(amount=700000)
    result = _base_result(
        facts_text="除700000元外，被告还应支付999999元。",
        structured=structured,
    )
    out = _validate(result, structured)
    assert not out.passed
    assert any(i.code == "NO_EXTRA_AMOUNT" for i in out.errors)


def test_c_guessed_court_in_court_section_fails() -> None:
    structured = _structured_payment()
    result = _base_result()
    result = result.model_copy(
        update={"court_section": "此致\n成都市高新区人民法院"}
    )
    out = _validate(result, structured)
    assert not out.passed
    assert any(i.code == "NO_UNKNOWN_COURT" for i in out.errors)


def test_d_fabricated_law_article_fails() -> None:
    structured = _structured_payment()
    result = _base_result(
        facts_text="依据《民法典》第577条，被告应承担违约责任。",
    )
    out = _validate(result, structured)
    assert not out.passed
    assert any(i.code == "NO_UNKNOWN_LAW_ARTICLE" for i in out.errors)


def test_e_speculative_language_fails() -> None:
    structured = _structured_payment()
    result = _base_result(facts_text="如原告已提交成果，被告可能构成违约。")
    out = _validate(result, structured)
    assert not out.passed
    assert any(i.code == "SPECULATIVE_LANGUAGE" for i in out.errors)


def test_f_liability_bridge_missing_fails() -> None:
    structured = _structured_payment()
    structured = structured.model_copy(
        update={
            "liability_bridge_required": True,
            "contract_party_names": ["四川富茂置业有限公司"],
            "defendant_names": ["中梁地产"],
            "liability_facts": [],
        }
    )
    result = _base_result()
    out = _validate(result, structured)
    assert not out.passed
    assert any(i.code == "LIABILITY_NARRATIVE_SUPPORTED" for i in out.errors)


def test_g_unsupported_full_performance_fails() -> None:
    structured = _structured_payment()
    structured = structured.model_copy(update={"performance_facts": []})
    result = _base_result(facts_text="原告已按约履行全部义务。")
    out = _validate(result, structured)
    assert not out.passed
    assert any(i.code == "PERFORMANCE_NARRATIVE_SUPPORTED" for i in out.errors)


def test_h_uuid_in_body_fails() -> None:
    structured = _structured_payment()
    uid = str(uuid.uuid4())
    result = _base_result(facts_text=f"事实关联 {uid}。")
    out = _validate(result, structured)
    assert not out.passed
    assert any(i.code == "NO_UUID_IN_BODY" for i in out.errors)


# ----- Evidence directory I–J -----


def test_i_same_material_multiple_spans_one_directory_item(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    svc = DomainService(db_session)
    case = svc.create_case(title="证据归并", owner_user_id=owner_id)
    plaintiff = svc.create_party(
        case_id=case.id,
        role="PLAINTIFF",
        name="原告A",
        party_type="ORG",
        actor_id=actor_id,
    )
    defendant = svc.create_party(
        case_id=case.id,
        role="DEFENDANT",
        name="被告B",
        party_type="ORG",
        actor_id=actor_id,
    )
    svc.confirm_party(plaintiff.party_key, actor_id=actor_id)
    svc.confirm_party(defendant.party_key, actor_id=actor_id)

    material = svc.register_material(
        case_id=case.id,
        filename="设计优化咨询服务合同.pdf",
        mime="application/pdf",
        byte_size=100,
        content_hash=f"h-{uuid.uuid4().hex[:10]}",
        storage_key=f"k-{uuid.uuid4().hex[:10]}",
        created_by=actor_id,
    )
    full_contract = "收费条款内容。付款条款内容。签章页内容。"
    ec = svc.create_extracted_content(
        material_id=material.id,
        extraction_method="pdfplumber",
        extraction_version="v1",
        full_text=full_contract,
        actor_id=actor_id,
    )
    spans = []
    for start, quote in ((0, "收费条款内容"), (7, "付款条款内容"), (14, "签章页内容")):
        spans.append(
            svc.create_source_span(
                material_id=material.id,
                extracted_content_id=ec.id,
                character_start=start,
                character_end=start + len(quote),
                quote=quote,
                extraction_method="pdfplumber",
                extraction_version="v1",
            )
        )

    evidences = []
    for i, span in enumerate(spans, start=1):
        ev = svc.create_evidence_item(
            case_id=case.id,
            number=str(i),
            title=f"合同片段{i}",
            category="CONTRACT",
            summary=span.quote,
            source_span_ids=[span.id],
            actor_id=actor_id,
        )
        svc.accept_evidence(ev.id, actor_id=actor_id)
        evidences.append(svc.repo.get_current_evidence(ev.id))
    assert all(evidences)

    facts = []
    for i, ev in enumerate(evidences):
        assert ev is not None
        stmt = f"合同约定服务费按优化金额8%计取（片段{i + 1}）。"
        fact = svc.propose_fact(
            case_id=case.id,
            statement=stmt,
            evidence_links=[
                {
                    "evidence_item_id": ev.id,
                    "evidence_item_version": ev.version,
                }
            ],
            actor_id=actor_id,
        )
        svc.confirm_fact(fact.fact_key, actor_id=actor_id)
        f = svc.repo.get_current_fact(fact.fact_key)
        assert f is not None
        facts.append(f)

    extra_facts = [
        "原告已交付优化成果并经签收。",
        "优化金额为2500000元，对应服务费200000元。",
        "被告已支付50000元，尚欠150000元。",
        "付款条件已成就，债务已到期。",
    ]
    anchor = evidences[0]
    assert anchor is not None
    for stmt in extra_facts:
        fact = svc.propose_fact(
            case_id=case.id,
            statement=stmt,
            evidence_links=[
                {
                    "evidence_item_id": anchor.id,
                    "evidence_item_version": anchor.version,
                }
            ],
            actor_id=actor_id,
        )
        svc.confirm_fact(fact.fact_key, actor_id=actor_id)
        confirmed = svc.repo.get_current_fact(fact.fact_key)
        assert confirmed is not None
        facts.append(confirmed)

    claim = svc.create_claim_direction(
        case_id=case.id,
        payload={
            "overall_strategy": "请求支付剩余服务费",
            "claims": [
                {
                    "claim_type": "PAYMENT",
                    "description": "剩余服务费",
                    "amount": 150000,
                    "currency": "CNY",
                    "supporting_fact_ids": [str(f.fact_key) for f in facts],
                }
            ],
        },
        actor_id=actor_id,
    )
    claim = svc.confirm_claim_direction(claim.claim_direction_key, actor_id=actor_id)

    all_evidences = list(
        db_session.scalars(
            select(EvidenceItem).where(
                EvidenceItem.case_id == case.id,
                EvidenceItem.acceptance == "ACCEPTED",
                EvidenceItem.is_current.is_(True),
            )
        ).all()
    )

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
            for e in all_evidences
        ],
        confirmed_party_keys=[plaintiff.party_key, defendant.party_key],
        actor_id=actor_id,
    )
    assert result.draft is not None
    body = result.draft.body_structured_json
    ev_dir = body.get("evidence_directory") or []
    assert len(ev_dir) == 1
    assert "设计优化" in ev_dir[0]["title"] or "合同" in ev_dir[0]["title"]
    ev_text = body.get("evidence_directory_text") or ""
    assert ev_text.count("证明目的") == 1


def test_j_merge_proof_purposes_same_material(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    test_i_same_material_multiple_spans_one_directory_item(
        db_session, owner_id=owner_id, actor_id=actor_id
    )


# ----- K placeholder -----


def test_k_missing_legal_rep_placeholder_not_fabricated(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, parties, facts, evidences, claim = _seed_writer_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    result = PleadingWriterService(db_session).write(
        **_writer_args(case, parties, facts, evidences, claim),
        actor_id=actor_id,
    )
    text = result.draft.body_structured_json["full_text"]  # type: ignore[union-attr]
    assert "【待补充】" in text
    assert "法定代表人：张三" not in text


# ----- L full READY -----


def test_l_full_ready_case_valid_draft(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, parties, facts, evidences, claim = _seed_writer_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    result = PleadingWriterService(db_session).write(
        **_writer_args(case, parties, facts, evidences, claim),
        actor_id=actor_id,
    )
    assert result.draft is not None
    body = result.draft.body_structured_json
    assert body["validation"]["passed"] is True
    text = body["full_text"]
    assert "民事起诉状" in text
    assert "诉讼请求" in text
    assert "事实与理由" in text
    assert "一、" in text
    assert "700,000" in text or "700000" in text.replace(",", "")


# ----- M–N repair -----


class _FailOnceEngine:
    def __init__(self) -> None:
        self._fallback = DeterministicPleadingWriterStub()

    def write(self, inp, *, parties, facts, claim, evidence, structured=None):
        good = self._fallback.write(
            inp,
            parties=parties,
            facts=facts,
            claim=claim,
            evidence=evidence,
            structured=structured,
        )
        return good.model_copy(
            update={
                "facts_and_reasons_section": (
                    good.facts_and_reasons_section
                    + "\n如原告已提交成果，被告可能构成违约。"
                )
            }
        )

    def repair(self, inp, *, parties, facts, claim, evidence, structured, errors, prior):
        return self._fallback.write(
            inp,
            parties=parties,
            facts=facts,
            claim=claim,
            evidence=evidence,
            structured=structured,
        )


def test_m_repair_after_bad_draft_saves_valid(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, parties, facts, evidences, claim = _seed_writer_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    svc = PleadingWriterService(db_session, engine=_FailOnceEngine())
    result = svc.write(
        **_writer_args(case, parties, facts, evidences, claim),
        actor_id=actor_id,
    )
    assert result.draft is not None
    assert result.draft.body_structured_json["validation"]["passed"] is True


class _AlwaysBadEngine:
    def __init__(self) -> None:
        self._fallback = DeterministicPleadingWriterStub()

    def write(self, inp, *, parties, facts, claim, evidence, structured=None):
        good = self._fallback.write(
            inp,
            parties=parties,
            facts=facts,
            claim=claim,
            evidence=evidence,
            structured=structured,
        )
        return good.model_copy(
            update={
                "facts_and_reasons_section": (
                    good.facts_and_reasons_section
                    + "\n如原告已提交成果，被告可能构成违约。"
                )
            }
        )

    def repair(self, inp, *, parties, facts, claim, evidence, structured, errors, prior):
        return self.write(
            inp,
            parties=parties,
            facts=facts,
            claim=claim,
            evidence=evidence,
            structured=structured,
        )


def test_n_repair_still_invalid_raises(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, parties, facts, evidences, claim = _seed_writer_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    svc = PleadingWriterService(db_session, engine=_AlwaysBadEngine())
    with pytest.raises(PleadingDraftValidationError):
        svc.write(
            **_writer_args(case, parties, facts, evidences, claim),
            actor_id=actor_id,
        )


# ----- Fumao/Zhongliang READY quality regression -----


def _seed_fumao_zhongliang_ready(
    session: Session, *, owner_id: uuid.UUID, actor_id: uuid.UUID
):
    svc = DomainService(session)
    case = svc.create_case(title="富茂中梁质量回归", owner_user_id=owner_id)
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
        "合同签约甲方：四川富茂置业有限公司，乙方为四川维海科技有限公司。",
        "合同约定服务费按优化金额的8%计取，封顶30万元。",
        "中梁地产债务加入并确认承担涉案合同付款义务。",
        "原告于2024年6月向项目组提交设计优化成果，被告通过邮件确认接收。",
        "经确认的优化金额为2500000元，按8%计算对应服务费200000元。",
        "被告已支付50000元。",
        "尚欠150000元未付。",
        "付款条件已成就，服务费已到期。",
        "原告已发送催款函催告付款。",
    ]
    facts = []
    evidences = []
    for i, stmt in enumerate(statements):
        f = _add_confirmed_fact(
            svc,
            case_id=case.id,
            statement=stmt,
            actor_id=actor_id,
            number=str(i + 1),
        )
        facts.append(f)

    evidences = list(
        session.scalars(
            select(EvidenceItem).where(
                EvidenceItem.case_id == case.id,
                EvidenceItem.acceptance == "ACCEPTED",
                EvidenceItem.is_current.is_(True),
            )
        ).all()
    )

    claim = svc.create_claim_direction(
        case_id=case.id,
        payload={
            "overall_strategy": "请求中梁地产支付剩余服务费",
            "claims": [
                {
                    "claim_type": "PAYMENT",
                    "description": "设计优化咨询服务费",
                    "amount": 150000,
                    "currency": "CNY",
                    "calculation_basis": "200000-50000",
                    "supporting_fact_ids": [str(f.fact_key) for f in facts],
                }
            ],
        },
        actor_id=actor_id,
    )
    claim = svc.confirm_claim_direction(claim.claim_direction_key, actor_id=actor_id)
    return svc, case, [plaintiff.party_key, defendant.party_key], facts, evidences, claim


def test_fumao_zhongliang_ready_quality_regression(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, parties, facts, evidences, claim = _seed_fumao_zhongliang_ready(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    from backend.application.pleading_readiness import PleadingReadinessService

    readiness = PleadingReadinessService(db_session).evaluate(case.id)
    assert readiness.status.value == "READY"

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
    text = result.draft.body_structured_json["full_text"]
    assert "虽然" in text or "债务加入" in text
    assert "2500000" in text.replace(",", "") or "250万" in text or "2500000" in text
    assert "150000" in text.replace(",", "") or "150,000" in text
    assert "如原告" not in text
    assert "可能" not in text
    assert "根据案件情况" not in text
    uid_pattern = str(uuid.uuid4())[:8]
    assert uid_pattern not in text
