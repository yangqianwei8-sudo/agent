"""Phase 8 — Pleading Writer / N8_WRITE anti-examples and gates."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.application.pleading_writer import PleadingWriterService
from backend.domain.errors import ValidationError
from backend.domain.services import DomainService
from backend.models import (
    DocumentDraft,
    DraftCitation,
    EvidenceItem,
    Fact,
    SkillExecution,
)
from backend.schemas.case_analyst import EvidenceRef
from backend.schemas.claim_direction_proposal import FactRef
from backend.schemas.pleading_readiness import PleadingNotReadyError
from backend.schemas.pleading_writer import (
    ClaimDirectionRef,
    ClaimLine,
    FactBlock,
    PleadingWriterEngineResult,
    WriterWarning,
)
from backend.skills.pleading_writer import ScriptedPleadingWriterEngine
from backend.tests.workflow.helpers import ensure_pleading_prep_template, node_by_code
from backend.workflow.runtime import WorkflowRuntime


def _seed_writer_world(
    session: Session,
    *,
    owner_id: uuid.UUID,
    actor_id: uuid.UUID,
    amount_facts: bool = True,
) -> tuple:
    svc = DomainService(session)
    case = svc.create_case(title="P8 Case", owner_user_id=owner_id)
    plaintiff = svc.create_party(
        case_id=case.id,
        role="PLAINTIFF",
        name="原告设计公司",
        party_type="ORG",
        actor_id=actor_id,
        identifiers_json={"credit_code": "91110000MA00000000"},
    )
    defendant = svc.create_party(
        case_id=case.id,
        role="DEFENDANT",
        name="被告建设公司",
        party_type="ORG",
        actor_id=actor_id,
    )
    svc.confirm_party(plaintiff.party_key, actor_id=actor_id)
    svc.confirm_party(defendant.party_key, actor_id=actor_id)

    statements = (
        [
            "合同约定服务费总价为1000000元。",
            "被告已支付300000元。",
            "原告已向被告交付设计成果并经签收。",
            "合同约定成果提交后付款，付款条件已成就。",
            "尚欠服务费700000元已到期。",
            "合同约定由被告住所地人民法院管辖。",
        ]
        if amount_facts
        else [
            "双方于2025年3月1日签订设计合同。",
            "原告已向被告交付设计成果。",
        ]
    )
    facts: list[Fact] = []
    evidences: list[EvidenceItem] = []
    for i, text in enumerate(statements):
        material = svc.register_material(
            case_id=case.id,
            filename=f"m{i}.pdf",
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

    claim_payload = {
        "overall_strategy": "请求支付剩余服务费",
        "claims": [
            {
                "claim_type": "PAYMENT",
                "description": "剩余服务费",
                "amount": 700000,
                "currency": "CNY",
                "calculation_basis": "1000000-300000",
                "supporting_fact_ids": [str(f.fact_key) for f in facts],
            }
        ],
    }
    if not amount_facts:
        claim_payload = {
            "overall_strategy": "请求确认合同关系",
            "claims": [
                {
                    "claim_type": "DECLARATORY",
                    "description": "请求确认双方合同关系及交付事实",
                    "supporting_fact_ids": [str(f.fact_key) for f in facts],
                }
            ],
        }
    claim = svc.create_claim_direction(_legacy_compat=True,
        case_id=case.id, payload=claim_payload, actor_id=actor_id
    )
    claim = svc.confirm_claim_direction(claim.claim_direction_key, actor_id=actor_id)

    return (
        svc,
        case,
        [plaintiff.party_key, defendant.party_key],
        facts,
        evidences,
        claim,
    )


def _writer_args(case, parties, facts, evidences, claim):
    return {
        "case_id": case.id,
        "claim_direction_ref": {
            "claim_direction_key": str(claim.claim_direction_key),
            "claim_direction_version": claim.version,
        },
        "confirmed_fact_refs": [
            {"fact_key": str(f.fact_key), "fact_version": f.version} for f in facts
        ],
        "accepted_evidence_refs": [
            {
                "evidence_item_id": str(e.id),
                "evidence_item_version": e.version,
            }
            for e in evidences
        ],
        "confirmed_party_keys": parties,
    }


# ----- A–I input gates -----


def test_a_no_confirmed_claim_direction(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    domain, case, parties, facts, evidences, claim = _seed_writer_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    domain.reject_claim_direction(claim.claim_direction_key, actor_id=actor_id)
    svc = PleadingWriterService(db_session)
    with pytest.raises(
        (ValidationError, PleadingNotReadyError),
        match="尚无律师确认|NO_CONFIRMED|ClaimDirection",
    ):
        svc.write(
            **_writer_args(case, parties, facts, evidences, claim),
            actor_id=actor_id,
        )


def test_b_stale_claim_direction(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    domain, case, parties, facts, evidences, claim = _seed_writer_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    domain.reject_fact(facts[0].fact_key, actor_id=actor_id)
    claim = domain.repo.get_current_claim(claim.claim_direction_key)
    assert claim is not None and claim.stale is True
    svc = PleadingWriterService(db_session)
    with pytest.raises(
        (ValidationError, PleadingNotReadyError),
        match="尚无律师确认|stale|NO_CONFIRMED|ClaimDirection",
    ):
        svc.write(
            **_writer_args(case, parties, facts, evidences, claim),
            actor_id=actor_id,
        )


def test_c_d_e_fact_gates(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    domain, case, parties, facts, evidences, claim = _seed_writer_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    svc = PleadingWriterService(db_session)
    args = _writer_args(case, parties, facts, evidences, claim)

    # missing version
    with pytest.raises(ValidationError, match="fact_version required"):
        bad = dict(args)
        bad["confirmed_fact_refs"] = [{"fact_key": str(facts[0].fact_key)}]
        svc.write(**bad, actor_id=actor_id)

    # CANDIDATE
    cand = domain.propose_fact(
        case_id=case.id,
        statement="候选事实正文足够长。",
        evidence_links=[
            {
                "evidence_item_id": evidences[0].id,
                "evidence_item_version": evidences[0].version,
            }
        ],
        actor_id=actor_id,
    )
    with pytest.raises(ValidationError, match="CANDIDATE"):
        bad = dict(args)
        bad["confirmed_fact_refs"] = [
            {"fact_key": str(cand.fact_key), "fact_version": 1}
        ]
        svc.write(**bad, actor_id=actor_id)


def test_f_g_h_evidence_gates(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    domain, case, parties, facts, evidences, claim = _seed_writer_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    svc = PleadingWriterService(db_session)
    args = _writer_args(case, parties, facts, evidences, claim)

    pending = domain.create_evidence_item(
        case_id=case.id,
        number="9",
        title="未确认",
        category="OTHER",
        summary="x",
        source_span_ids=[
            domain.repo.list_evidence_spans(evidences[0].id, evidences[0].version)[
                0
            ].source_span_id
        ],
        actor_id=actor_id,
    )
    with pytest.raises(ValidationError, match="PENDING"):
        bad = dict(args)
        bad["accepted_evidence_refs"] = [
            {
                "evidence_item_id": str(pending.id),
                "evidence_item_version": 1,
            }
        ]
        svc.write(**bad, actor_id=actor_id)

    with pytest.raises(ValidationError, match="filename-only"):
        svc._parse_evidence_refs(  # noqa: SLF001
            [{"filename": "合同.pdf", "evidence_item_version": 1}]
        )


def test_i_cross_case_rejected(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case_a, parties_a, _, _, claim_a = _seed_writer_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    _, _, _, facts_b, evidences_b, _ = _seed_writer_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    svc = PleadingWriterService(db_session)
    with pytest.raises(ValidationError, match="cross-case"):
        svc.write(
            case_id=case_a.id,
            claim_direction_ref={
                "claim_direction_key": str(claim_a.claim_direction_key),
                "claim_direction_version": claim_a.version,
            },
            confirmed_fact_refs=[
                {"fact_key": str(facts_b[0].fact_key), "fact_version": 1}
            ],
            accepted_evidence_refs=[
                {
                    "evidence_item_id": str(evidences_b[0].id),
                    "evidence_item_version": 1,
                }
            ],
            confirmed_party_keys=parties_a,
            actor_id=actor_id,
        )


# ----- J–M claim consistency -----


def test_j_amount_consistent(
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
    text = result.draft.body_structured_json["full_text"]
    assert "700000" in text.replace(",", "")
    assert result.draft.status == "DRAFT"


def test_k_amount_mismatch_rejected(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, parties, facts, evidences, claim = _seed_writer_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    bad = PleadingWriterEngineResult(
        title="民事起诉状",
        parties_section="原告：x\n被告：y",
        claims=[
            ClaimLine(
                claim_type="PAYMENT",
                text="请求支付人民币800000元。",
                amount=800000,
                currency="CNY",
            )
        ],
        claims_section="诉讼请求：\n1. 请求支付人民币800000元。",
        fact_blocks=[
            FactBlock(
                block_id="fact-001",
                text=facts[0].statement,
                fact_refs=[
                    FactRef(fact_key=facts[0].fact_key, fact_version=facts[0].version)
                ],
                evidence_refs=[
                    EvidenceRef(
                        evidence_item_id=evidences[0].id,
                        evidence_item_version=evidences[0].version,
                    )
                ],
            )
        ],
        facts_and_reasons_section="事实与理由：\n" + facts[0].statement,
        evidence_section="证据目录：",
        signature_section="此致",
        used_fact_refs=[
            FactRef(fact_key=facts[0].fact_key, fact_version=facts[0].version)
        ],
        used_evidence_refs=[
            EvidenceRef(
                evidence_item_id=evidences[0].id,
                evidence_item_version=evidences[0].version,
            )
        ],
        used_claim_direction_ref=ClaimDirectionRef(
            claim_direction_key=claim.claim_direction_key,
            claim_direction_version=claim.version,
        ),
        warnings=[
            WriterWarning(
                code="CLAIM_FACT_VERSION_PROVENANCE_GAP",
                message="gap",
            )
        ],
    )
    svc = PleadingWriterService(
        db_session, engine=ScriptedPleadingWriterEngine(bad)
    )
    with pytest.raises(ValidationError, match="amount"):
        svc.write(
            **_writer_args(case, parties, facts, evidences, claim),
            actor_id=actor_id,
        )


def test_l_invented_lpr_rejected(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, parties, facts, evidences, claim = _seed_writer_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    bad = PleadingWriterEngineResult(
        title="民事起诉状",
        parties_section="原告：x\n被告：y",
        claims=[
            ClaimLine(
                claim_type="PAYMENT",
                text="请求支付人民币700000元，并按LPR计息。",
                amount=700000,
                currency="CNY",
            )
        ],
        claims_section="诉讼请求：\n1. 请求支付人民币700000元，并按LPR计息。",
        fact_blocks=[
            FactBlock(
                block_id="fact-001",
                text=facts[0].statement,
                fact_refs=[
                    FactRef(fact_key=facts[0].fact_key, fact_version=facts[0].version)
                ],
                evidence_refs=[
                    EvidenceRef(
                        evidence_item_id=evidences[0].id,
                        evidence_item_version=evidences[0].version,
                    )
                ],
            )
        ],
        facts_and_reasons_section="事实与理由：\n" + facts[0].statement,
        evidence_section="证据目录：",
        signature_section="此致",
        used_fact_refs=[
            FactRef(fact_key=facts[0].fact_key, fact_version=facts[0].version)
        ],
        used_evidence_refs=[
            EvidenceRef(
                evidence_item_id=evidences[0].id,
                evidence_item_version=evidences[0].version,
            )
        ],
        used_claim_direction_ref=ClaimDirectionRef(
            claim_direction_key=claim.claim_direction_key,
            claim_direction_version=claim.version,
        ),
        warnings=[],
    )
    with pytest.raises(ValidationError, match="interest"):
        PleadingWriterService(
            db_session, engine=ScriptedPleadingWriterEngine(bad)
        ).write(
            **_writer_args(case, parties, facts, evidences, claim),
            actor_id=actor_id,
        )


def test_m_extra_claim_rejected(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, parties, facts, evidences, claim = _seed_writer_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    bad = PleadingWriterEngineResult(
        title="民事起诉状",
        parties_section="原告：x\n被告：y",
        claims=[
            ClaimLine(
                claim_type="PAYMENT",
                text="请求支付人民币700000元。",
                amount=700000,
                currency="CNY",
            ),
            ClaimLine(
                claim_type="TERMINATION",
                text="请求解除合同。",
            ),
        ],
        claims_section="诉讼请求：\n1.\n2.",
        fact_blocks=[
            FactBlock(
                block_id="fact-001",
                text=facts[0].statement,
                fact_refs=[
                    FactRef(fact_key=facts[0].fact_key, fact_version=facts[0].version)
                ],
                evidence_refs=[
                    EvidenceRef(
                        evidence_item_id=evidences[0].id,
                        evidence_item_version=evidences[0].version,
                    )
                ],
            )
        ],
        facts_and_reasons_section="事实与理由：\n" + facts[0].statement,
        evidence_section="证据目录：",
        signature_section="此致",
        used_fact_refs=[
            FactRef(fact_key=facts[0].fact_key, fact_version=facts[0].version)
        ],
        used_evidence_refs=[
            EvidenceRef(
                evidence_item_id=evidences[0].id,
                evidence_item_version=evidences[0].version,
            )
        ],
        used_claim_direction_ref=ClaimDirectionRef(
            claim_direction_key=claim.claim_direction_key,
            claim_direction_version=claim.version,
        ),
        warnings=[],
    )
    with pytest.raises(ValidationError, match="claims count"):
        PleadingWriterService(
            db_session, engine=ScriptedPleadingWriterEngine(bad)
        ).write(
            **_writer_args(case, parties, facts, evidences, claim),
            actor_id=actor_id,
        )


# ----- N–W facts / citations -----


def test_n_p_r_t_u_v_happy_path_citations(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    domain, case, parties, facts, evidences, claim = _seed_writer_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    result = PleadingWriterService(db_session).write(
        **_writer_args(case, parties, facts, evidences, claim),
        actor_id=actor_id,
    )
    assert result.draft is not None
    cites = db_session.scalars(
        select(DraftCitation).where(DraftCitation.draft_id == result.draft.id)
    ).all()
    assert cites
    for cite in cites:
        assert cite.fact_key is not None
        assert cite.fact_version is not None
        assert cite.evidence_item_id is not None
        assert cite.evidence_item_version is not None
        # provenance chain
        item = domain.repo.get_evidence_version(
            cite.evidence_item_id, cite.evidence_item_version
        )
        assert item is not None
        spans = domain.repo.list_evidence_spans(item.id, item.version)
        assert spans
    assert all("fact_version" in r for r in result.used_fact_refs)


def test_o_unknown_fact_rejected(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, parties, facts, evidences, claim = _seed_writer_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    fake_key = uuid.uuid4()
    bad = PleadingWriterEngineResult(
        title="民事起诉状",
        parties_section="原告：x\n被告：y",
        claims=[
            ClaimLine(
                claim_type="PAYMENT",
                text="请求支付人民币700000元。",
                amount=700000,
                currency="CNY",
            )
        ],
        claims_section="诉讼请求：\n1. 请求支付人民币700000元。",
        fact_blocks=[
            FactBlock(
                block_id="fact-001",
                text="模型新编的关键事实：被告构成根本违约。",
                fact_refs=[FactRef(fact_key=fake_key, fact_version=1)],
                evidence_refs=[
                    EvidenceRef(
                        evidence_item_id=evidences[0].id,
                        evidence_item_version=evidences[0].version,
                    )
                ],
            )
        ],
        facts_and_reasons_section="事实与理由：\n模型新编的关键事实",
        evidence_section="证据目录：",
        signature_section="此致",
        used_fact_refs=[FactRef(fact_key=fake_key, fact_version=1)],
        used_evidence_refs=[
            EvidenceRef(
                evidence_item_id=evidences[0].id,
                evidence_item_version=evidences[0].version,
            )
        ],
        used_claim_direction_ref=ClaimDirectionRef(
            claim_direction_key=claim.claim_direction_key,
            claim_direction_version=claim.version,
        ),
        warnings=[],
    )
    with pytest.raises(ValidationError, match="outside input"):
        PleadingWriterService(
            db_session, engine=ScriptedPleadingWriterEngine(bad)
        ).write(
            **_writer_args(case, parties, facts, evidences, claim),
            actor_id=actor_id,
        )


# ----- X–AB draft / stale -----


def test_x_y_draft_versioning_and_not_approved(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, parties, facts, evidences, claim = _seed_writer_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    svc = PleadingWriterService(db_session)
    first = svc.write(
        **_writer_args(case, parties, facts, evidences, claim),
        actor_id=actor_id,
    )
    second = svc.write(
        **_writer_args(case, parties, facts, evidences, claim),
        actor_id=actor_id,
    )
    assert first.draft is not None and second.draft is not None
    assert first.draft.status == "DRAFT"
    assert second.draft.version == first.draft.version + 1
    old = db_session.get(DocumentDraft, first.draft.id)
    assert old is not None
    assert old.status == "DRAFT"


def test_z_aa_ab_stale_on_fact_evidence_claim_change(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    domain, case, parties, facts, evidences, claim = _seed_writer_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    svc = PleadingWriterService(db_session)
    result = svc.write(
        **_writer_args(case, parties, facts, evidences, claim),
        actor_id=actor_id,
    )
    assert result.draft is not None
    draft_fact = result.draft.id

    domain.amend_fact(
        facts[0].fact_key,
        new_statement=facts[0].statement + "（修订）",
        actor_id=actor_id,
    )
    stale_fact = db_session.get(DocumentDraft, draft_fact)
    assert stale_fact is not None and stale_fact.status == "STALE"

    # Fresh world for evidence-exclude → draft stale
    domain2, case2, parties2, facts2, evidences2, claim2 = _seed_writer_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    result2 = svc.write(
        **_writer_args(case2, parties2, facts2, evidences2, claim2),
        actor_id=actor_id,
    )
    assert result2.draft is not None
    domain2.exclude_evidence(evidences2[0].id, actor_id=actor_id)
    stale_ev = db_session.get(DocumentDraft, result2.draft.id)
    assert stale_ev is not None and stale_ev.status == "STALE"

    # Fresh world for claim amend → draft stale
    domain3, case3, parties3, facts3, evidences3, claim3 = _seed_writer_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    result3 = svc.write(
        **_writer_args(case3, parties3, facts3, evidences3, claim3),
        actor_id=actor_id,
    )
    assert result3.draft is not None
    domain3.amend_claim_direction(
        claim3.claim_direction_key,
        payload={
            "overall_strategy": "修订策略",
            "claims": [
                {
                    "claim_type": "PAYMENT",
                    "description": "剩余服务费",
                    "amount": 700000,
                    "currency": "CNY",
                    "calculation_basis": "1000000-300000",
                    "supporting_fact_ids": [str(f.fact_key) for f in facts3],
                }
            ],
        },
        actor_id=actor_id,
        _legacy_compat=True,
    )
    stale_claim = db_session.get(DocumentDraft, result3.draft.id)
    assert stale_claim is not None and stale_claim.status == "STALE"


# ----- AC–AE party / AF–AH legal -----


def test_ac_ad_ae_party_placeholders_and_blocking(
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
    assert "91110000MA00000000" in text

    with pytest.raises(ValidationError, match="PLAINTIFF and DEFENDANT"):
        PleadingWriterService(db_session).write(
            case_id=case.id,
            claim_direction_ref={
                "claim_direction_key": str(claim.claim_direction_key),
                "claim_direction_version": claim.version,
            },
            confirmed_fact_refs=[
                {"fact_key": str(facts[0].fact_key), "fact_version": 1}
            ],
            accepted_evidence_refs=[
                {
                    "evidence_item_id": str(evidences[0].id),
                    "evidence_item_version": 1,
                }
            ],
            confirmed_party_keys=[parties[0]],
            actor_id=actor_id,
        )


def test_af_ag_ah_legal_boundary(
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
    assert "依据相关法律规定" not in text
    assert "《民法典》第" not in text

    bad = PleadingWriterEngineResult(
        title="民事起诉状",
        parties_section="原告：x\n被告：y",
        claims=[
            ClaimLine(
                claim_type="PAYMENT",
                text="请求支付人民币700000元。",
                amount=700000,
                currency="CNY",
            )
        ],
        claims_section="诉讼请求：\n1. 请求支付人民币700000元。",
        fact_blocks=[
            FactBlock(
                block_id="fact-001",
                text=facts[0].statement,
                fact_refs=[
                    FactRef(fact_key=facts[0].fact_key, fact_version=facts[0].version)
                ],
                evidence_refs=[
                    EvidenceRef(
                        evidence_item_id=evidences[0].id,
                        evidence_item_version=evidences[0].version,
                    )
                ],
            )
        ],
        facts_and_reasons_section="事实与理由：\n依据《民法典》第577条，被告应承担违约责任。",
        evidence_section="证据目录：",
        signature_section="此致",
        used_fact_refs=[
            FactRef(fact_key=facts[0].fact_key, fact_version=facts[0].version)
        ],
        used_evidence_refs=[
            EvidenceRef(
                evidence_item_id=evidences[0].id,
                evidence_item_version=evidences[0].version,
            )
        ],
        used_claim_direction_ref=ClaimDirectionRef(
            claim_direction_key=claim.claim_direction_key,
            claim_direction_version=claim.version,
        ),
        warnings=[],
    )
    with pytest.raises(ValidationError, match="fabricated statute"):
        PleadingWriterService(
            db_session, engine=ScriptedPleadingWriterEngine(bad)
        ).write(
            **_writer_args(case, parties, facts, evidences, claim),
            actor_id=actor_id,
        )


# ----- AI–AJ phase7 gap -----


def test_ai_aj_phase7_gap_warning_and_fact_versions_audited(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, parties, facts, evidences, claim = _seed_writer_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    result = PleadingWriterService(db_session).write(
        **_writer_args(case, parties, facts, evidences, claim),
        actor_id=actor_id,
    )
    assert any(
        w["code"] == "CLAIM_FACT_VERSION_PROVENANCE_GAP" for w in result.warnings
    )
    assert all("fact_version" in r and "fact_key" in r for r in result.used_fact_refs)
    body = result.draft.body_structured_json  # type: ignore[union-attr]
    assert body["used_fact_refs"]
    assert all("fact_version" in r for r in body["used_fact_refs"])


# ----- AK–AN workflow -----


def _advance_to_n8(runtime: WorkflowRuntime, case_id: uuid.UUID):
    ensure_pleading_prep_template(runtime.session)
    inst = runtime.create_instance(case_id=case_id)
    r = runtime.start_instance(inst.id)
    n8 = node_by_code(runtime.session, inst.template_id, "N8_WRITE")
    safety = 0
    while True:
        safety += 1
        if safety > 50:
            raise AssertionError("did not reach N8")
        if r.node_run is not None and r.node_run.node_id == n8.id:
            return inst, r.node_run
        if r.instance.status == "WAITING_USER":
            r = runtime.resume_instance(r.instance.id, command_id=uuid.uuid4())
            continue
        if r.node_run is None:
            raise AssertionError(f"stuck: {r.instance.status}")
        r = runtime.complete_node(r.node_run.id)


def test_workflow_n8_creates_draft_and_enters_n9(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    domain, case, parties, facts, evidences, claim = _seed_writer_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    # Seed Phase 7 context marker so N8 prereq soft-check has claim keys
    runtime = WorkflowRuntime(db_session)
    inst, n8_run = _advance_to_n8(runtime, case.id)
    inst = runtime.get_instance(inst.id)
    ctx = dict(inst.context_json or {})
    ctx["claim_direction"] = {
        "claim_direction_keys": [str(claim.claim_direction_key)]
    }
    inst.context_json = ctx
    db_session.flush()

    result = PleadingWriterService(db_session).run_n8_write(
        instance_id=inst.id,
        node_run_id=n8_run.id,
        claim_direction_ref={
            "claim_direction_key": str(claim.claim_direction_key),
            "claim_direction_version": claim.version,
        },
        confirmed_fact_refs=[
            {"fact_key": str(f.fact_key), "fact_version": f.version} for f in facts
        ],
        accepted_evidence_refs=[
            {
                "evidence_item_id": str(e.id),
                "evidence_item_version": e.version,
            }
            for e in evidences
        ],
        confirmed_party_keys=parties,
        actor_id=actor_id,
        auto_complete=True,
    )
    assert result.draft is not None
    assert result.draft.status == "DRAFT"
    assert result.citation_count >= 1

    skill = db_session.scalars(
        select(SkillExecution).where(SkillExecution.node_run_id == n8_run.id)
    ).one()
    assert skill.skill_code == "PleadingWriterSkill"
    assert skill.metrics_json["output"]["draft_status"] == "DRAFT"
    assert all(
        "fact_version" in r
        for r in skill.metrics_json["output"]["used_fact_refs"]
    )

    inst = runtime.get_instance(inst.id)
    assert inst.status == "WAITING_USER"
    assert inst.waiting_reason == "DRAFT_REVIEW"
    n9 = node_by_code(db_session, inst.template_id, "N9_REVIEW")
    assert inst.current_node_id == n9.id

    # Must not auto-approve
    draft = db_session.get(DocumentDraft, result.draft.id)
    assert draft is not None
    assert draft.status == "DRAFT"
    _ = domain
