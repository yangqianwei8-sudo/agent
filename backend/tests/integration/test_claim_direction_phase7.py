"""Phase 7 — Claim Direction / N7_CONFIRM_CLAIMS anti-examples and gates."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.application.claim_direction import ClaimDirectionService
from backend.domain.errors import ConflictError, ValidationError
from backend.domain.services import DomainService
from backend.models import (
    AuditLog,
    ClaimDirection,
    Fact,
    HumanDecision,
    SkillExecution,
)
from backend.schemas.claim_direction_proposal import (
    ClaimDirectionEngineResult,
    ClaimDirectionProposal,
    ClaimProposalItem,
    FactRef,
)
from backend.skills.claim_direction import ScriptedClaimDirectionEngine
from backend.tests.workflow.helpers import ensure_pleading_prep_template, node_by_code
from backend.workflow.runtime import WorkflowRuntime


def _seed_confirmed_world(
    session: Session,
    *,
    owner_id: uuid.UUID,
    actor_id: uuid.UUID,
    statements: list[str] | None = None,
) -> tuple:
    svc = DomainService(session)
    case = svc.create_case(title="P7 Case", owner_user_id=owner_id)
    plaintiff = svc.create_party(
        case_id=case.id,
        role="PLAINTIFF",
        name="原告设计公司",
        party_type="ORG",
        actor_id=actor_id,
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

    texts = statements or [
        "合同约定服务费总价为1000000元。",
        "被告已支付300000元。",
    ]
    facts: list[Fact] = []
    for i, text in enumerate(texts):
        material = svc.register_material(
            case_id=case.id,
            filename=f"f{i}.pdf",
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
        fact = svc.propose_fact(
            case_id=case.id,
            statement=text,
            evidence_links=[
                {
                    "evidence_item_id": evidence.id,
                    "evidence_item_version": 1,
                }
            ],
            actor_id=actor_id,
        )
        svc.confirm_fact(fact.fact_key, actor_id=actor_id)
        current = svc.repo.get_current_fact(fact.fact_key)
        assert current is not None
        facts.append(current)

    return svc, case, [plaintiff.party_key, defendant.party_key], facts


def _fact_refs(facts: list[Fact]) -> list[dict]:
    return [
        {"fact_key": str(f.fact_key), "fact_version": f.version} for f in facts
    ]


def _payment_proposal(
    facts: list[Fact],
    *,
    amount: float = 700000,
    strategy: str = "请求被告支付拖欠服务费。",
) -> ClaimDirectionProposal:
    refs = [
        FactRef(fact_key=f.fact_key, fact_version=f.version) for f in facts
    ]
    return ClaimDirectionProposal(
        proposal_id=uuid.uuid4(),
        overall_strategy=strategy,
        claims=[
            ClaimProposalItem(
                claim_type="PAYMENT",
                description="请求被告支付剩余服务费",
                amount=amount,
                currency="CNY",
                calculation_basis="合同总价 1000000 - 已支付 300000 = 剩余 700000",
                supporting_fact_refs=refs,
                confidence=0.9,
            )
        ],
    )


def test_a_fact_missing_version(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, parties, facts = _seed_confirmed_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    svc = ClaimDirectionService(db_session)
    with pytest.raises(ValidationError, match="fact_version required"):
        svc.propose(
            case_id=case.id,
            confirmed_fact_refs=[{"fact_key": str(facts[0].fact_key)}],
            confirmed_party_keys=parties,
            actor_id=actor_id,
        )


def test_b_fact_not_found(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, parties, _ = _seed_confirmed_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    svc = ClaimDirectionService(db_session)
    with pytest.raises(ValidationError, match="not found"):
        svc.propose(
            case_id=case.id,
            confirmed_fact_refs=[
                {"fact_key": str(uuid.uuid4()), "fact_version": 1}
            ],
            confirmed_party_keys=parties,
            actor_id=actor_id,
        )


def test_c_cross_case_fact(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case_a, parties_a, _ = _seed_confirmed_world(
        db_session,
        owner_id=owner_id,
        actor_id=actor_id,
        statements=["案件A事实正文足够长。"],
    )
    _, _, _, facts_b = _seed_confirmed_world(
        db_session,
        owner_id=owner_id,
        actor_id=actor_id,
        statements=["案件B事实正文足够长。"],
    )
    svc = ClaimDirectionService(db_session)
    with pytest.raises(ValidationError, match="cross-case"):
        svc.propose(
            case_id=case_a.id,
            confirmed_fact_refs=_fact_refs(facts_b),
            confirmed_party_keys=parties_a,
            actor_id=actor_id,
        )


def test_d_candidate_fact_rejected(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    domain, case, parties, facts = _seed_confirmed_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    evidence = domain.repo.get_current_evidence(
        domain.repo.list_fact_links(facts[0].id)[0].evidence_item_id
    )
    assert evidence is not None
    cand = domain.propose_fact(
        case_id=case.id,
        statement="未确认候选事实正文足够长。",
        evidence_links=[
            {
                "evidence_item_id": evidence.id,
                "evidence_item_version": evidence.version,
            }
        ],
        actor_id=actor_id,
    )
    svc = ClaimDirectionService(db_session)
    with pytest.raises(ValidationError, match="CANDIDATE"):
        svc.propose(
            case_id=case.id,
            confirmed_fact_refs=[
                {"fact_key": str(cand.fact_key), "fact_version": 1}
            ],
            confirmed_party_keys=parties,
            actor_id=actor_id,
        )


def test_e_rejected_fact(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    domain, case, parties, facts = _seed_confirmed_world(
        db_session,
        owner_id=owner_id,
        actor_id=actor_id,
        statements=["将被拒绝的事实正文足够长。"],
    )
    domain.reject_fact(facts[0].fact_key, actor_id=actor_id)
    svc = ClaimDirectionService(db_session)
    with pytest.raises(ValidationError, match="REJECTED"):
        svc.propose(
            case_id=case.id,
            confirmed_fact_refs=_fact_refs(facts),
            confirmed_party_keys=parties,
            actor_id=actor_id,
        )


def test_f_stale_fact(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    domain, case, parties, facts = _seed_confirmed_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    link = domain.repo.list_fact_links(facts[0].id)[0]
    domain.exclude_evidence(link.evidence_item_id, actor_id=actor_id)
    stale_fact = domain.repo.get_current_fact(facts[0].fact_key)
    assert stale_fact is not None and stale_fact.stale is True
    svc = ClaimDirectionService(db_session)
    with pytest.raises(ValidationError, match="stale"):
        svc.propose(
            case_id=case.id,
            confirmed_fact_refs=[
                {
                    "fact_key": str(stale_fact.fact_key),
                    "fact_version": stale_fact.version,
                }
            ],
            confirmed_party_keys=parties,
            actor_id=actor_id,
        )


def test_g_unconfirmed_party(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    domain, case, _, facts = _seed_confirmed_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    pending = domain.create_party(
        case_id=case.id,
        role="OTHER",
        name="未确认第三人",
        party_type="ORG",
        actor_id=actor_id,
    )
    svc = ClaimDirectionService(db_session)
    with pytest.raises(ValidationError, match="not CONFIRMED"):
        svc.propose(
            case_id=case.id,
            confirmed_fact_refs=_fact_refs(facts),
            confirmed_party_keys=[pending.party_key],
            actor_id=actor_id,
        )


def test_h_no_implicit_latest_fact(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, parties, facts = _seed_confirmed_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    svc = ClaimDirectionService(db_session)
    with pytest.raises(ValidationError, match="fact_version required"):
        svc.propose(
            case_id=case.id,
            confirmed_fact_refs=[{"fact_key": str(facts[0].fact_key)}],
            confirmed_party_keys=parties,
            actor_id=actor_id,
        )


def test_i_empty_claims_rejected(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, parties, facts = _seed_confirmed_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    engine = ScriptedClaimDirectionEngine(
        ClaimDirectionEngineResult(
            proposals=[
                ClaimDirectionProposal.model_construct(
                    proposal_id=uuid.uuid4(),
                    overall_strategy="策略",
                    claims=[],
                    risks=[],
                    missing_confirmations=[],
                )
            ]
        )
    )
    svc = ClaimDirectionService(db_session, engine=engine)
    result = svc.propose(
        case_id=case.id,
        confirmed_fact_refs=_fact_refs(facts),
        confirmed_party_keys=parties,
        actor_id=actor_id,
    )
    assert result.created == []
    assert result.rejected_proposals


def test_k_empty_strategy_rejected(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    from pydantic import ValidationError as PydanticValidationError

    _, _, _, facts = _seed_confirmed_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    with pytest.raises(PydanticValidationError):
        ClaimDirectionProposal(
            proposal_id=uuid.uuid4(),
            overall_strategy="",
            claims=[
                ClaimProposalItem(
                    claim_type="TERMINATION",
                    description="解除合同",
                    supporting_fact_refs=[
                        FactRef(
                            fact_key=facts[0].fact_key,
                            fact_version=facts[0].version,
                        )
                    ],
                )
            ],
        )


def test_l_illegal_claim_type(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    from pydantic import ValidationError as PydanticValidationError

    _, _, _, facts = _seed_confirmed_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    with pytest.raises(PydanticValidationError):
        ClaimProposalItem(
            claim_type="NOT_A_TYPE",  # type: ignore[arg-type]
            description="x",
            supporting_fact_refs=[
                FactRef(fact_key=facts[0].fact_key, fact_version=1)
            ],
        )


def test_m_n_o_p_payment_amount_currency_rules() -> None:
    from backend.schemas.claim_direction import validate_claim_direction_payload

    with pytest.raises(ValidationError):
        validate_claim_direction_payload(
            {
                "overall_strategy": "x",
                "claims": [
                    {
                        "claim_type": "PAYMENT",
                        "description": "付款",
                        "currency": "CNY",
                        "supporting_fact_ids": [str(uuid.uuid4())],
                    }
                ],
            }
        )
    with pytest.raises(ValidationError):
        validate_claim_direction_payload(
            {
                "overall_strategy": "x",
                "claims": [
                    {
                        "claim_type": "PAYMENT",
                        "description": "付款",
                        "amount": -1,
                        "currency": "CNY",
                        "supporting_fact_ids": [str(uuid.uuid4())],
                    }
                ],
            }
        )
    with pytest.raises(ValidationError):
        validate_claim_direction_payload(
            {
                "overall_strategy": "x",
                "claims": [
                    {
                        "claim_type": "PAYMENT",
                        "description": "付款",
                        "amount": 100,
                        "supporting_fact_ids": [str(uuid.uuid4())],
                    }
                ],
            }
        )
    with pytest.raises(ValidationError):
        validate_claim_direction_payload(
            {
                "overall_strategy": "x",
                "claims": [
                    {
                        "claim_type": "PAYMENT",
                        "description": "付款",
                        "amount": 100,
                        "currency": "CN",
                        "supporting_fact_ids": [str(uuid.uuid4())],
                    }
                ],
            }
        )


def test_q_empty_supporting_facts_cannot_confirm(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    domain, case, _, _ = _seed_confirmed_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    claim = domain.create_claim_direction(_legacy_compat=True,
        case_id=case.id,
        payload={
            "overall_strategy": "解除",
            "claims": [
                {
                    "claim_type": "TERMINATION",
                    "description": "解除合同",
                    "supporting_fact_ids": [],
                }
            ],
        },
        actor_id=actor_id,
    )
    with pytest.raises(ValidationError, match="supporting_fact"):
        domain.confirm_claim_direction(claim.claim_direction_key, actor_id=actor_id)


def test_r_grounded_amount_allowed(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, parties, facts = _seed_confirmed_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    engine = ScriptedClaimDirectionEngine(
        ClaimDirectionEngineResult(proposals=[_payment_proposal(facts, amount=700000)])
    )
    svc = ClaimDirectionService(db_session, engine=engine)
    result = svc.propose(
        case_id=case.id,
        confirmed_fact_refs=_fact_refs(facts),
        confirmed_party_keys=parties,
        actor_id=actor_id,
    )
    assert len(result.created) == 1
    assert result.created[0].status == "CANDIDATE"
    assert result.created[0].payload["claims"][0]["amount"] == 700000


def test_s_invented_amount_rejected(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, parties, facts = _seed_confirmed_world(
        db_session,
        owner_id=owner_id,
        actor_id=actor_id,
        statements=["双方签订设计合同，未载明金额。", "原告已交付成果。"],
    )
    engine = ScriptedClaimDirectionEngine(
        ClaimDirectionEngineResult(proposals=[_payment_proposal(facts, amount=700000)])
    )
    svc = ClaimDirectionService(db_session, engine=engine)
    result = svc.propose(
        case_id=case.id,
        confirmed_fact_refs=_fact_refs(facts),
        confirmed_party_keys=parties,
        actor_id=actor_id,
    )
    assert result.created == []
    assert any("not grounded" in r["error"] for r in result.rejected_proposals)


def test_t_ai_creates_candidate_only(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, parties, facts = _seed_confirmed_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    svc = ClaimDirectionService(db_session)
    result = svc.propose(
        case_id=case.id,
        confirmed_fact_refs=_fact_refs(facts),
        confirmed_party_keys=parties,
        actor_id=actor_id,
    )
    assert result.created
    assert all(c.status == "CANDIDATE" for c in result.created)


def test_u_v_ai_cannot_overwrite_confirmed(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    domain, case, parties, facts = _seed_confirmed_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    engine = ScriptedClaimDirectionEngine(
        ClaimDirectionEngineResult(proposals=[_payment_proposal(facts)])
    )
    svc = ClaimDirectionService(db_session, engine=engine)
    first = svc.propose(
        case_id=case.id,
        confirmed_fact_refs=_fact_refs(facts),
        confirmed_party_keys=parties,
        actor_id=actor_id,
    ).created[0]
    domain.confirm_claim_direction(first.claim_direction_key, actor_id=actor_id)

    engine2 = ScriptedClaimDirectionEngine(
        ClaimDirectionEngineResult(
            proposals=[
                _payment_proposal(
                    facts, strategy="不同策略：仅主张违约金。", amount=700000
                )
            ]
        )
    )
    second = ClaimDirectionService(db_session, engine=engine2).propose(
        case_id=case.id,
        confirmed_fact_refs=_fact_refs(facts),
        confirmed_party_keys=parties,
        actor_id=actor_id,
    )
    confirmed = domain.repo.get_current_claim(first.claim_direction_key)
    assert confirmed is not None
    assert confirmed.status == "CONFIRMED"
    assert "拖欠服务费" in confirmed.payload["overall_strategy"]
    assert second.created
    assert second.created[0].status == "CANDIDATE"
    assert second.amendment_proposals


def test_w_confirm_human_decision(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    domain, case, parties, facts = _seed_confirmed_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    svc = ClaimDirectionService(
        db_session,
        engine=ScriptedClaimDirectionEngine(
            ClaimDirectionEngineResult(proposals=[_payment_proposal(facts)])
        ),
    )
    created = svc.propose(
        case_id=case.id,
        confirmed_fact_refs=_fact_refs(facts),
        confirmed_party_keys=parties,
        actor_id=actor_id,
    ).created[0]
    confirmed = domain.confirm_claim_direction(
        created.claim_direction_key, actor_id=actor_id
    )
    assert confirmed.status == "CONFIRMED"
    assert confirmed.confirm_decision_id is not None
    assert any(
        d.decision_type == "CONFIRM_CLAIM_DIRECTION"
        for d in db_session.scalars(
            select(HumanDecision).where(HumanDecision.case_id == case.id)
        )
    )
    assert db_session.scalars(
        select(AuditLog).where(AuditLog.action == "confirm_claim_direction")
    ).first()


def test_x_amend_keeps_old_version(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    domain, case, parties, facts = _seed_confirmed_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    svc = ClaimDirectionService(
        db_session,
        engine=ScriptedClaimDirectionEngine(
            ClaimDirectionEngineResult(proposals=[_payment_proposal(facts)])
        ),
    )
    created = svc.propose(
        case_id=case.id,
        confirmed_fact_refs=_fact_refs(facts),
        confirmed_party_keys=parties,
        actor_id=actor_id,
    ).created[0]
    domain.confirm_claim_direction(created.claim_direction_key, actor_id=actor_id)
    new_payload = {
        "overall_strategy": "修订后策略：主张剩余服务费。",
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
    amended = domain.amend_claim_direction(
        created.claim_direction_key, payload=new_payload, actor_id=actor_id
    )
    assert amended.version == 2
    assert amended.status == "CONFIRMED"
    old = db_session.scalars(
        select(ClaimDirection).where(
            ClaimDirection.claim_direction_key == created.claim_direction_key,
            ClaimDirection.version == 1,
        )
    ).one()
    assert old.status == "SUPERSEDED"


def test_y_stale_supporting_blocks_confirm(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    domain, case, parties, facts = _seed_confirmed_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    svc = ClaimDirectionService(
        db_session,
        engine=ScriptedClaimDirectionEngine(
            ClaimDirectionEngineResult(proposals=[_payment_proposal(facts)])
        ),
    )
    created = svc.propose(
        case_id=case.id,
        confirmed_fact_refs=_fact_refs(facts),
        confirmed_party_keys=parties,
        actor_id=actor_id,
    ).created[0]
    link = domain.repo.list_fact_links(facts[0].id)[0]
    domain.exclude_evidence(link.evidence_item_id, actor_id=actor_id)
    with pytest.raises((ValidationError, ConflictError)):
        domain.confirm_claim_direction(created.claim_direction_key, actor_id=actor_id)


def test_z_fact_reject_stales_confirmed_claim(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    domain, case, parties, facts = _seed_confirmed_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    svc = ClaimDirectionService(
        db_session,
        engine=ScriptedClaimDirectionEngine(
            ClaimDirectionEngineResult(proposals=[_payment_proposal(facts)])
        ),
    )
    created = svc.propose(
        case_id=case.id,
        confirmed_fact_refs=_fact_refs(facts),
        confirmed_party_keys=parties,
        actor_id=actor_id,
    ).created[0]
    domain.confirm_claim_direction(created.claim_direction_key, actor_id=actor_id)
    domain.reject_fact(facts[0].fact_key, actor_id=actor_id)
    claim = domain.repo.get_current_claim(created.claim_direction_key)
    assert claim is not None
    assert claim.stale is True
    assert claim.status == "CONFIRMED"


def test_aa_ab_ac_ad_writer_query(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    domain, case, parties, facts = _seed_confirmed_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    svc = ClaimDirectionService(
        db_session,
        engine=ScriptedClaimDirectionEngine(
            ClaimDirectionEngineResult(proposals=[_payment_proposal(facts)])
        ),
    )
    with pytest.raises(ValidationError, match="no CONFIRMED"):
        svc.get_confirmed_claim_direction_for_writer(case.id)

    created = svc.propose(
        case_id=case.id,
        confirmed_fact_refs=_fact_refs(facts),
        confirmed_party_keys=parties,
        actor_id=actor_id,
    ).created[0]
    with pytest.raises(ValidationError, match="no CONFIRMED"):
        svc.get_confirmed_claim_direction_for_writer(case.id)

    confirmed = domain.confirm_claim_direction(
        created.claim_direction_key, actor_id=actor_id
    )
    got = svc.get_confirmed_claim_direction_for_writer(case.id)
    assert got.id == confirmed.id
    assert got.version == confirmed.version

    domain.reject_fact(facts[0].fact_key, actor_id=actor_id)
    with pytest.raises(ValidationError, match="no CONFIRMED"):
        svc.get_confirmed_claim_direction_for_writer(case.id)


def test_ae_duplicate_skipped(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    _, case, parties, facts = _seed_confirmed_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    svc = ClaimDirectionService(
        db_session,
        engine=ScriptedClaimDirectionEngine(
            ClaimDirectionEngineResult(proposals=[_payment_proposal(facts)])
        ),
    )
    first = svc.propose(
        case_id=case.id,
        confirmed_fact_refs=_fact_refs(facts),
        confirmed_party_keys=parties,
        actor_id=actor_id,
    )
    second = ClaimDirectionService(
        db_session,
        engine=ScriptedClaimDirectionEngine(
            ClaimDirectionEngineResult(proposals=[_payment_proposal(facts)])
        ),
    ).propose(
        case_id=case.id,
        confirmed_fact_refs=_fact_refs(facts),
        confirmed_party_keys=parties,
        actor_id=actor_id,
    )
    assert second.created == []
    assert second.skipped
    assert second.claim_direction_keys == [first.created[0].claim_direction_key]


def _advance_to_n7(runtime: WorkflowRuntime, case_id: uuid.UUID):
    ensure_pleading_prep_template(runtime.session)
    inst = runtime.create_instance(case_id=case_id)
    r = runtime.start_instance(inst.id)
    n7 = node_by_code(runtime.session, inst.template_id, "N7_CONFIRM_CLAIMS")
    safety = 0
    while True:
        safety += 1
        if safety > 40:
            raise AssertionError("did not reach N7")
        if r.instance.status == "WAITING_USER":
            inst = runtime.get_instance(inst.id)
            if inst.current_node_id == n7.id:
                r = runtime.resume_instance(inst.id, command_id=uuid.uuid4())
                assert r.node_run is not None
                assert r.node_run.node_id == n7.id
                return inst, r.node_run
            r = runtime.resume_instance(inst.id, command_id=uuid.uuid4())
            continue
        if r.node_run is None:
            raise AssertionError(f"stuck: {r.instance.status}")
        r = runtime.complete_node(r.node_run.id)


def test_workflow_n7_stops_before_n8(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    domain, case, parties, facts = _seed_confirmed_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    runtime = WorkflowRuntime(db_session)
    inst, n7_run = _advance_to_n7(runtime, case.id)

    claim_svc = ClaimDirectionService(
        db_session,
        engine=ScriptedClaimDirectionEngine(
            ClaimDirectionEngineResult(proposals=[_payment_proposal(facts)])
        ),
    )
    result = claim_svc.run_n7_propose(
        instance_id=inst.id,
        node_run_id=n7_run.id,
        confirmed_fact_refs=_fact_refs(facts),
        confirmed_party_keys=parties,
        actor_id=actor_id,
    )
    assert result.created
    assert result.created[0].status == "CANDIDATE"

    skill = db_session.scalars(
        select(SkillExecution).where(SkillExecution.node_run_id == n7_run.id)
    ).one()
    assert skill.skill_code == "ClaimDirectionSkill"
    assert skill.status == "SUCCEEDED"

    inst = runtime.get_instance(inst.id)
    assert inst.status == "WAITING_USER"
    assert inst.waiting_reason == "CLAIM"

    with pytest.raises(ValidationError, match="N7 claim gate"):
        claim_svc.assert_n7_ready_to_complete(inst.id)

    domain.confirm_claim_direction(
        result.created[0].claim_direction_key, actor_id=actor_id
    )
    claim_svc.assert_n7_ready_to_complete(inst.id)

    n7_run = runtime.get_node_run(n7_run.id)
    if n7_run.status == "RUNNING":
        n7_run.status = "WAITING_USER"
        db_session.flush()

    done = claim_svc.complete_n7_confirm_claims(
        instance_id=inst.id,
        node_run_id=n7_run.id,
        auto_advance=False,
    )
    assert done.node_run is not None
    assert done.node_run.status == "SUCCEEDED"
    n8 = node_by_code(db_session, inst.template_id, "N8_WRITE")
    assert runtime.list_node_runs(inst.id, node_id=n8.id) == []

    writer_cd = claim_svc.get_confirmed_claim_direction_for_writer(case.id)
    assert writer_cd.status == "CONFIRMED"
    assert writer_cd.stale is False


def test_reject_claim_direction_domain(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    domain, case, parties, facts = _seed_confirmed_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    svc = ClaimDirectionService(
        db_session,
        engine=ScriptedClaimDirectionEngine(
            ClaimDirectionEngineResult(proposals=[_payment_proposal(facts)])
        ),
    )
    created = svc.propose(
        case_id=case.id,
        confirmed_fact_refs=_fact_refs(facts),
        confirmed_party_keys=parties,
        actor_id=actor_id,
    ).created[0]
    rejected = domain.reject_claim_direction(
        created.claim_direction_key, actor_id=actor_id
    )
    assert rejected.status == "REJECTED"
    assert any(
        d.decision_type == "REJECT_CLAIM_DIRECTION"
        for d in db_session.scalars(
            select(HumanDecision).where(HumanDecision.case_id == case.id)
        )
    )
