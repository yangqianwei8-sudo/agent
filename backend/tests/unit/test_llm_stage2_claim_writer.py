"""Stage 2 — Real LLM ClaimDirection / Writer (FakeLLM) + N7/N9 gate safety."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.agent.dto import AgentIntent
from backend.agent.intent_router import DeterministicIntentRouter, IntentParseContext
from backend.application.claim_direction import ClaimDirectionService
from backend.application.pleading_writer import PleadingWriterService
from backend.domain.errors import ValidationError
from backend.llm.claim_direction import LLMClaimDirectionEngine
from backend.llm.errors import LLMOutputParseError, LLMSchemaValidationError
from backend.llm.fake import FakeLLMClient
from backend.llm.intent_router import LLMIntentRouter
from backend.llm.pleading_writer import LLMPleadingWriterEngine
from backend.models import ClaimDirection, DraftCitation
from backend.schemas.claim_direction_proposal import ClaimDirectionInput, FactRef
from backend.skills.claim_direction import FactView, PartyView
from backend.skills.pleading_writer import DeterministicPleadingWriterStub
from backend.tests.integration.test_claim_direction_phase7 import (
    _fact_refs,
    _seed_confirmed_world,
)
from backend.tests.integration.test_pleading_writer import _seed_writer_world
from backend.tests.workflow.helpers import ensure_pleading_prep_template


def _valid_claim_payload(facts) -> dict:
    refs = [
        {"fact_key": str(f.fact_key), "fact_version": f.version} for f in facts
    ]
    return {
        "proposals": [
            {
                "proposal_id": str(uuid.uuid4()),
                "overall_strategy": "请求支付剩余服务费",
                "claims": [
                    {
                        "claim_type": "PAYMENT",
                        "description": "请求被告支付剩余服务费",
                        "amount": 700000,
                        "currency": "CNY",
                        "calculation_basis": (
                            "合同总价 1000000 元 - 已付款 300000 元 = 剩余 700000 元"
                        ),
                        "interest_start_date": None,
                        "interest_rate": None,
                        "supporting_fact_refs": refs,
                        "confidence": 0.9,
                        "uncertainties": [],
                    }
                ],
                "risks": [],
                "missing_confirmations": [],
            }
        ]
    }


def _propose(svc, case, parties, facts, actor_id):
    return svc.propose(
        case_id=case.id,
        confirmed_fact_refs=_fact_refs(facts),
        confirmed_party_keys=parties,
        actor_id=actor_id,
    )


def test_a_b_valid_claim_candidate_amount(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    ensure_pleading_prep_template(db_session)
    _, case, parties, facts = _seed_confirmed_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    svc = ClaimDirectionService(
        db_session,
        engine=LLMClaimDirectionEngine(
            FakeLLMClient(responses=[_valid_claim_payload(facts)])
        ),
    )
    result = _propose(svc, case, parties, facts, actor_id)
    assert len(result.created) == 1
    claim = result.created[0]
    assert claim.status == "CANDIDATE"
    assert claim.payload["claims"][0]["amount"] == 700000
    assert claim.payload["claims"][0]["currency"] == "CNY"
    assert claim.payload["claims"][0]["claim_type"] == "PAYMENT"


def test_c_hallucinated_amount_rejected(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    ensure_pleading_prep_template(db_session)
    _, case, parties, facts = _seed_confirmed_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    payload = _valid_claim_payload(facts)
    payload["proposals"][0]["claims"][0]["amount"] = 800000
    payload["proposals"][0]["claims"][0]["calculation_basis"] = "模型估算损失 800000"
    svc = ClaimDirectionService(
        db_session,
        engine=LLMClaimDirectionEngine(FakeLLMClient(responses=[payload])),
    )
    result = _propose(svc, case, parties, facts, actor_id)
    assert result.created == []
    assert result.rejected_proposals


def test_d_wrong_fact_version_rejected_by_engine(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    ensure_pleading_prep_template(db_session)
    _, case, parties, facts = _seed_confirmed_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    payload = _valid_claim_payload(facts)
    payload["proposals"][0]["claims"][0]["supporting_fact_refs"][0]["fact_version"] = 99
    engine = LLMClaimDirectionEngine(FakeLLMClient(responses=[payload]))
    with pytest.raises(LLMSchemaValidationError):
        engine.propose(
            ClaimDirectionInput(
                case_id=case.id,
                confirmed_fact_refs=[
                    FactRef(fact_key=f.fact_key, fact_version=f.version) for f in facts
                ],
                confirmed_party_keys=parties,
            ),
            [
                FactView(
                    fact_key=f.fact_key,
                    fact_version=f.version,
                    statement=f.statement,
                    status="CONFIRMED",
                    stale=False,
                    amounts_mentioned=[],
                )
                for f in facts
            ],
            [
                PartyView(
                    party_key=pk,
                    role="PLAINTIFF",
                    name="x",
                    party_type="ORG",
                    version=1,
                )
                for pk in parties
            ],
        )


def test_e_candidate_fact_input_rejected(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    ensure_pleading_prep_template(db_session)
    domain, case, parties, facts = _seed_confirmed_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    evidence = domain.repo.get_current_evidence(
        domain.repo.list_fact_links(facts[0].id)[0].evidence_item_id
    )
    assert evidence is not None
    cand = domain.propose_fact(
        case_id=case.id,
        statement="候选事实不应进入 Claim，正文足够长。",
        evidence_links=[
            {
                "evidence_item_id": evidence.id,
                "evidence_item_version": evidence.version,
            }
        ],
        actor_id=actor_id,
    )
    svc = ClaimDirectionService(
        db_session,
        engine=LLMClaimDirectionEngine(
            FakeLLMClient(responses=[_valid_claim_payload(facts)])
        ),
    )
    with pytest.raises(ValidationError):
        svc.propose(
            case_id=case.id,
            confirmed_fact_refs=[
                {"fact_key": cand.fact_key, "fact_version": cand.version}
            ],
            confirmed_party_keys=parties,
            actor_id=actor_id,
        )


def test_f_stale_confirmed_fact_rejected(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    ensure_pleading_prep_template(db_session)
    _, case, parties, facts = _seed_confirmed_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    facts[0].stale = True
    db_session.flush()
    svc = ClaimDirectionService(
        db_session,
        engine=LLMClaimDirectionEngine(
            FakeLLMClient(responses=[_valid_claim_payload(facts)])
        ),
    )
    with pytest.raises(ValidationError):
        _propose(svc, case, parties, facts, actor_id)


def test_g_llm_claim_creates_candidate_only(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    ensure_pleading_prep_template(db_session)
    _, case, parties, facts = _seed_confirmed_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    svc = ClaimDirectionService(
        db_session,
        engine=LLMClaimDirectionEngine(
            FakeLLMClient(responses=[_valid_claim_payload(facts)])
        ),
    )
    result = _propose(svc, case, parties, facts, actor_id)
    assert result.created[0].status == "CANDIDATE"
    row = db_session.get(ClaimDirection, result.created[0].id)
    assert row is not None and row.status == "CANDIDATE"
    assert not hasattr(LLMClaimDirectionEngine, "confirm_claim_direction")


def test_h_interest_hallucination_stripped(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    ensure_pleading_prep_template(db_session)
    _, case, parties, facts = _seed_confirmed_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    payload = _valid_claim_payload(facts)
    payload["proposals"][0]["claims"][0]["interest_rate"] = "LPR四倍"
    payload["proposals"][0]["claims"][0]["interest_start_date"] = "2025-01-01"
    svc = ClaimDirectionService(
        db_session,
        engine=LLMClaimDirectionEngine(FakeLLMClient(responses=[payload])),
    )
    result = _propose(svc, case, parties, facts, actor_id)
    assert result.created
    claim0 = result.created[0].payload["claims"][0]
    assert claim0.get("interest_rate") in (None, "")
    assert claim0.get("interest_start_date") in (None, "")
    assert any("利息" in m for m in result.missing_confirmations)


def test_i_duplicate_semantics(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    ensure_pleading_prep_template(db_session)
    _, case, parties, facts = _seed_confirmed_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    payload = _valid_claim_payload(facts)
    payload["proposals"][0]["proposal_id"] = str(uuid.uuid4())
    # identical claim content → duplicate fingerprint on second run
    payload2 = __import__("copy").deepcopy(payload)
    client = FakeLLMClient(responses=[payload, payload2])
    svc = ClaimDirectionService(db_session, engine=LLMClaimDirectionEngine(client))
    r1 = _propose(svc, case, parties, facts, actor_id)
    r2 = _propose(svc, case, parties, facts, actor_id)
    assert r1.created
    assert r2.skipped or (r2.created == [] and not r2.rejected_proposals)


def test_j_d022_warning_still_present(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    ensure_pleading_prep_template(db_session)
    _, case, parties, facts, evidences, claim = _seed_writer_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    result = PleadingWriterService(db_session).write(
        case_id=case.id,
        claim_direction_ref={
            "claim_direction_key": claim.claim_direction_key,
            "claim_direction_version": claim.version,
        },
        confirmed_fact_refs=[
            {"fact_key": f.fact_key, "fact_version": f.version} for f in facts
        ],
        accepted_evidence_refs=[
            {"evidence_item_id": e.id, "evidence_item_version": e.version}
            for e in evidences
        ],
        confirmed_party_keys=parties,
        actor_id=actor_id,
    )
    assert any(w["code"] == "CLAIM_FACT_VERSION_PROVENANCE_GAP" for w in result.warnings)


def _writer_payload(facts, evidences, claim, *, amount=700000, extra_claim=False) -> dict:
    claims = [
        {
            "claim_type": "PAYMENT",
            "text": f"请求支付人民币{amount}元",
            "amount": amount,
            "currency": "CNY",
        }
    ]
    if extra_claim:
        claims.append(
            {
                "claim_type": "INTEREST",
                "text": "额外利息",
                "amount": 10000,
                "currency": "CNY",
            }
        )
    return {
        "title": "民事起诉状",
        "parties_section": "原告：原告设计公司\n被告：被告建设公司",
        "claims": claims,
        "claims_section": "诉讼请求：\n" + "\n".join(c["text"] for c in claims),
        "fact_blocks": [
            {
                "block_id": "fact-001",
                "text": facts[0].statement,
                "fact_refs": [
                    {
                        "fact_key": str(facts[0].fact_key),
                        "fact_version": facts[0].version,
                    }
                ],
                "evidence_refs": [
                    {
                        "evidence_item_id": str(evidences[0].id),
                        "evidence_item_version": evidences[0].version,
                    }
                ],
            }
        ],
        "facts_and_reasons_section": (
            "事实与理由：\n"
            + facts[0].statement
            + "\n依据相关法律规定，依法请求人民法院判如所请。"
        ),
        "evidence_directory": [
            {
                "display_number": evidences[0].number,
                "title": evidences[0].title,
                "proof_purpose": "证明合同金额",
                "evidence_item_id": str(evidences[0].id),
                "evidence_item_version": evidences[0].version,
            }
        ],
        "evidence_section": "证据目录：",
        "court_section": "【待律师确认：管辖法院】",
        "signature_section": "此致\n【待律师确认：管辖法院】",
        "warnings": [],
        "used_fact_refs": [
            {"fact_key": str(f.fact_key), "fact_version": f.version} for f in facts
        ],
        "used_evidence_refs": [
            {"evidence_item_id": str(e.id), "evidence_item_version": e.version}
            for e in evidences
        ],
        "used_claim_direction_ref": {
            "claim_direction_key": str(claim.claim_direction_key),
            "claim_direction_version": claim.version,
        },
    }


def _write_args(case, parties, facts, evidences, claim, actor_id):
    return {
        "case_id": case.id,
        "claim_direction_ref": {
            "claim_direction_key": claim.claim_direction_key,
            "claim_direction_version": claim.version,
        },
        "confirmed_fact_refs": [
            {"fact_key": f.fact_key, "fact_version": f.version} for f in facts
        ],
        "accepted_evidence_refs": [
            {"evidence_item_id": e.id, "evidence_item_version": e.version}
            for e in evidences
        ],
        "confirmed_party_keys": parties,
        "actor_id": actor_id,
    }


def test_k_l_v_w_x_writer_draft_mirror_citations_d022(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    ensure_pleading_prep_template(db_session)
    _, case, parties, facts, evidences, claim = _seed_writer_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    svc = PleadingWriterService(
        db_session,
        engine=LLMPleadingWriterEngine(
            FakeLLMClient(responses=[_writer_payload(facts, evidences, claim)])
        ),
    )
    result = svc.write(**_write_args(case, parties, facts, evidences, claim, actor_id))
    assert result.draft is not None
    assert result.draft.status == "DRAFT"
    body_claims = result.draft.body_structured_json["claims"]
    substantive = [c for c in body_claims if c.get("claim_type") != "PROCEDURAL"]
    assert len(substantive) == 1
    assert abs(float(substantive[0]["amount"]) - 700000) < 1e-6
    assert any(w["code"] == "CLAIM_FACT_VERSION_PROVENANCE_GAP" for w in result.warnings)
    cites = list(
        db_session.scalars(
            select(DraftCitation).where(DraftCitation.draft_id == result.draft.id)
        )
    )
    assert len(cites) >= 1
    assert not hasattr(LLMPleadingWriterEngine, "approve_document_draft")


def test_m_writer_changes_amount_rejected(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    ensure_pleading_prep_template(db_session)
    _, case, parties, facts, evidences, claim = _seed_writer_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    svc = PleadingWriterService(
        db_session,
        engine=LLMPleadingWriterEngine(
            FakeLLMClient(
                responses=[_writer_payload(facts, evidences, claim, amount=800000)]
            )
        ),
    )
    with pytest.raises((ValidationError, LLMSchemaValidationError)):
        svc.write(**_write_args(case, parties, facts, evidences, claim, actor_id))


def test_n_writer_adds_claim_rejected(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    ensure_pleading_prep_template(db_session)
    _, case, parties, facts, evidences, claim = _seed_writer_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    svc = PleadingWriterService(
        db_session,
        engine=LLMPleadingWriterEngine(
            FakeLLMClient(
                responses=[_writer_payload(facts, evidences, claim, extra_claim=True)]
            )
        ),
    )
    with pytest.raises((ValidationError, LLMSchemaValidationError)):
        svc.write(**_write_args(case, parties, facts, evidences, claim, actor_id))


def test_o_writer_deletes_claim_rejected(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    ensure_pleading_prep_template(db_session)
    _, case, parties, facts, evidences, claim = _seed_writer_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )

    class _BadEngine:
        def write(self, *a, **k):
            good = DeterministicPleadingWriterStub().write(*a, **k)
            # Drop one claim after schema build via object mutation
            good.claims = good.claims[:0]
            return good

    with pytest.raises(ValidationError):
        PleadingWriterService(db_session, engine=_BadEngine()).write(
            **_write_args(case, parties, facts, evidences, claim, actor_id)
        )


def test_p_q_r_s_bad_fact_evidence_refs(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    ensure_pleading_prep_template(db_session)
    domain, case, parties, facts, evidences, claim = _seed_writer_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    svc = PleadingWriterService(db_session)
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
    args = _write_args(case, parties, facts, evidences, claim, actor_id)
    with pytest.raises(ValidationError):
        svc.write(
            **{
                **args,
                "confirmed_fact_refs": [
                    {"fact_key": cand.fact_key, "fact_version": cand.version}
                ],
            }
        )
    facts[0].stale = True
    db_session.flush()
    with pytest.raises(ValidationError):
        svc.write(**args)
    facts[0].stale = False
    db_session.flush()
    with pytest.raises(ValidationError):
        svc.write(
            **{
                **args,
                "accepted_evidence_refs": [
                    {
                        "evidence_item_id": evidences[0].id,
                        "evidence_item_version": evidences[0].version + 9,
                    }
                ],
            }
        )


def test_t_party_missing_fields_placeholder(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    ensure_pleading_prep_template(db_session)
    _, case, parties, facts, evidences, claim = _seed_writer_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    result = PleadingWriterService(db_session).write(
        **_write_args(case, parties, facts, evidences, claim, actor_id)
    )
    parties_text = result.draft.body_structured_json["sections"]["parties"]
    assert "【待补充】" in parties_text


def test_u_fabricated_law_article_rejected(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    """LLM cannot inject fabricated statute into final draft (deterministic facts win)."""
    from backend.skills.pleading_writer import looks_like_fabricated_law_citation

    ensure_pleading_prep_template(db_session)
    _, case, parties, facts, evidences, claim = _seed_writer_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    payload = _writer_payload(facts, evidences, claim)
    payload["facts_and_reasons_section"] = "依据《民法典》第509条请求判如所请。"
    svc = PleadingWriterService(
        db_session,
        engine=LLMPleadingWriterEngine(FakeLLMClient(responses=[payload])),
    )
    result = svc.write(**_write_args(case, parties, facts, evidences, claim, actor_id))
    assert result.draft is not None
    full_text = result.draft.body_structured_json["full_text"]
    assert "第509条" not in full_text
    assert not looks_like_fabricated_law_citation(full_text)


def test_n7_n9_ambiguous_affirmations_not_confirm() -> None:
    client = FakeLLMClient(
        responses=[
            {
                "intent": "CONFIRM_CLAIM_DIRECTION",
                "target_text": "诉讼请求1",
                "arguments": {},
                "confidence": 0.99,
                "reason": "用户说好",
            },
            {
                "intent": "APPROVE_DRAFT",
                "target_text": "",
                "arguments": {},
                "confidence": 0.99,
                "reason": "用户说可以",
            },
        ]
    )
    router = LLMIntentRouter(client)
    n7 = IntentParseContext(
        workflow_status="WAITING_USER",
        current_node="N7_CONFIRM_CLAIMS",
        waiting_reason="CLAIM",
        pending_human_gate=True,
    )
    n9 = IntentParseContext(
        workflow_status="WAITING_USER",
        current_node="N9_REVIEW",
        waiting_reason="DRAFT",
        pending_human_gate=True,
    )
    assert router.parse("好", context=n7).intent == AgentIntent.UNKNOWN
    assert router.parse("可以", context=n9).intent == AgentIntent.UNKNOWN
    d = DeterministicIntentRouter()
    assert d.parse("好").intent == AgentIntent.UNKNOWN
    assert d.parse("可以").intent == AgentIntent.UNKNOWN
    assert d.parse("继续").intent == AgentIntent.CONTINUE
    assert d.parse("确认诉讼请求1").intent == AgentIntent.CONFIRM_CLAIM_DIRECTION
    assert d.parse("批准这份起诉状").intent == AgentIntent.APPROVE_DRAFT


def test_invalid_json_fails_engine() -> None:
    class _BadClient:
        def complete_json(self, **kwargs):
            raise LLMOutputParseError("bad json")

    fk = uuid.uuid4()
    pk = uuid.uuid4()
    engine = LLMClaimDirectionEngine(_BadClient())  # type: ignore[arg-type]
    with pytest.raises(LLMOutputParseError):
        engine.propose(
            ClaimDirectionInput(
                case_id=uuid.uuid4(),
                confirmed_fact_refs=[FactRef(fact_key=fk, fact_version=1)],
                confirmed_party_keys=[pk],
            ),
            [
                FactView(
                    fact_key=fk,
                    fact_version=1,
                    statement="合同约定服务费总价为1000000元。",
                    status="CONFIRMED",
                    stale=False,
                    amounts_mentioned=[1000000.0],
                )
            ],
            [
                PartyView(
                    party_key=pk,
                    role="PLAINTIFF",
                    name="原告",
                    party_type="ORG",
                    version=1,
                )
            ],
        )
