"""Opt-in real LLM smoke — skipped unless RUN_REAL_LLM_TESTS=1 and config present."""

from __future__ import annotations

import os
import uuid

import pytest

from backend.agent.intent_router import IntentParseContext
from backend.infrastructure.config import clear_settings_cache, get_settings
from backend.llm.claim_direction import LLMClaimDirectionEngine
from backend.llm.client import OpenAICompatibleClient
from backend.llm.errors import LLMConfigurationError
from backend.llm.intent_router import LLMIntentRouter
from backend.llm.organizer import LLMEvidenceOrganizerEngine
from backend.llm.pleading_writer import LLMPleadingWriterEngine
from backend.schemas.case_analyst import EvidenceRef
from backend.schemas.claim_direction_proposal import ClaimDirectionInput, FactRef
from backend.schemas.evidence_proposal import OrganizerInput
from backend.schemas.pleading_writer import ClaimDirectionRef, PleadingWriterInput
from backend.skills.claim_direction import FactView, PartyView
from backend.skills.evidence_organizer import SpanView
from backend.skills.pleading_writer import (
    AcceptedEvidenceView,
    ClaimDirectionView,
    ConfirmedFactView,
)
from backend.skills.pleading_writer import (
    PartyView as WriterPartyView,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_REAL_LLM_TESTS", "").strip() != "1",
    reason="Set RUN_REAL_LLM_TESTS=1 to run real LLM smoke",
)


@pytest.fixture
def real_client():
    clear_settings_cache()
    settings = get_settings()
    if not (settings.llm_api_key or "").strip() or not (settings.llm_model or "").strip():
        pytest.skip("LLM_API_KEY / LLM_MODEL not configured")
    try:
        return OpenAICompatibleClient(settings)
    except LLMConfigurationError as exc:
        pytest.skip(str(exc))


def test_real_llm_intent_smoke(real_client) -> None:
    router = LLMIntentRouter(real_client)
    out = router.parse(
        "接受证据2",
        context=IntentParseContext(workflow_status="WAITING_USER", pending_human_gate=True),
    )
    assert out.intent.value == "ACCEPT_EVIDENCE"
    assert "2" in out.targets


def test_real_llm_organizer_smoke(real_client) -> None:
    span_id = uuid.uuid4()
    engine = LLMEvidenceOrganizerEngine(real_client)
    result = engine.organize(
        OrganizerInput(case_id=uuid.uuid4(), extracted_content_ids=[uuid.uuid4()]),
        [
            SpanView(
                span_id=span_id,
                quote="甲方智图公司与乙方星海公司签订设计优化咨询合同，约定服务费70万元。",
                page=1,
                paragraph=1,
                material_id=uuid.uuid4(),
                ec_id=uuid.uuid4(),
            )
        ],
    )
    assert isinstance(result.proposals, list)
    # Soft assert: model should usually produce >=1; allow empty if conservative
    for p in result.proposals:
        assert span_id in p.source_span_ids


def test_real_llm_claim_direction_smoke(real_client) -> None:
    fk1, fk2 = uuid.uuid4(), uuid.uuid4()
    engine = LLMClaimDirectionEngine(real_client)
    result = engine.propose(
        ClaimDirectionInput(
            case_id=uuid.uuid4(),
            confirmed_fact_refs=[
                FactRef(fact_key=fk1, fact_version=1),
                FactRef(fact_key=fk2, fact_version=1),
            ],
            confirmed_party_keys=[uuid.uuid4(), uuid.uuid4()],
        ),
        [
            FactView(
                fact_key=fk1,
                fact_version=1,
                statement="合同约定服务费总价为1000000元。",
                status="CONFIRMED",
                stale=False,
                amounts_mentioned=[1000000.0],
            ),
            FactView(
                fact_key=fk2,
                fact_version=1,
                statement="被告已支付300000元。",
                status="CONFIRMED",
                stale=False,
                amounts_mentioned=[300000.0],
            ),
        ],
        [
            PartyView(
                party_key=uuid.uuid4(),
                role="PLAINTIFF",
                name="智图设计有限公司",
                party_type="ORG",
                version=1,
            ),
            PartyView(
                party_key=uuid.uuid4(),
                role="DEFENDANT",
                name="星海建设有限公司",
                party_type="ORG",
                version=1,
            ),
        ],
    )
    assert isinstance(result.proposals, list)
    assert engine.last_meta.get("engine_mode") == "real"
    # Soft: if model produces payment claim, amount must be grounded
    for prop in result.proposals:
        for c in prop.claims:
            if c.amount is not None:
                assert float(c.amount) in {1000000.0, 300000.0, 700000.0}


def test_real_llm_pleading_writer_smoke(real_client) -> None:
    fk = uuid.uuid4()
    eid = uuid.uuid4()
    cdk = uuid.uuid4()
    pk_p, pk_d = uuid.uuid4(), uuid.uuid4()
    claim_payload = {
        "overall_strategy": "请求支付剩余服务费",
        "claims": [
            {
                "claim_type": "PAYMENT",
                "description": "请求支付剩余服务费700000元",
                "amount": 700000,
                "currency": "CNY",
                "calculation_basis": "1000000-300000",
                "interest_start_date": None,
                "interest_rate": None,
                "supporting_fact_ids": [str(fk)],
            }
        ],
        "risks": [],
        "missing_confirmations": [],
    }
    engine = LLMPleadingWriterEngine(real_client)
    result = engine.write(
        PleadingWriterInput(
            case_id=uuid.uuid4(),
            claim_direction_ref=ClaimDirectionRef(
                claim_direction_key=cdk, claim_direction_version=1
            ),
            confirmed_fact_refs=[FactRef(fact_key=fk, fact_version=1)],
            accepted_evidence_refs=[
                EvidenceRef(evidence_item_id=eid, evidence_item_version=1)
            ],
            confirmed_party_keys=[pk_p, pk_d],
        ),
        parties=[
            WriterPartyView(
                party_key=pk_p,
                role="PLAINTIFF",
                name="智图设计有限公司",
                party_type="ORG",
                version=1,
                identifiers_json={},
            ),
            WriterPartyView(
                party_key=pk_d,
                role="DEFENDANT",
                name="星海建设有限公司",
                party_type="ORG",
                version=1,
                identifiers_json={},
            ),
        ],
        facts=[
            ConfirmedFactView(
                fact_key=fk,
                fact_version=1,
                statement="合同约定服务费总价为1000000元，被告已支付300000元。",
                evidence_refs=[],
            )
        ],
        claim=ClaimDirectionView(
            claim_direction_key=cdk,
            claim_direction_version=1,
            payload=claim_payload,
            status="CONFIRMED",
            stale=False,
        ),
        evidence=[
            AcceptedEvidenceView(
                evidence_item_id=eid,
                evidence_item_version=1,
                number="1",
                title="设计优化咨询合同",
                summary="合同总价1000000元",
                category="CONTRACT",
            )
        ],
    )
    assert result.claims
    assert abs(float(result.claims[0].amount) - 700000) < 1e-6
    assert any(w.code == "CLAIM_FACT_VERSION_PROVENANCE_GAP" for w in result.warnings)
    assert engine.last_meta.get("engine_mode") == "real"
