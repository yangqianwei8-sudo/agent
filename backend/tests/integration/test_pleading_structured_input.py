"""V2-P4 — PleadingStructuredInput production integration tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.application.pleading_input_production import (
    PleadingStructuredInputProductionBuilder,
)
from backend.application.pleading_writer import PleadingWriterService
from backend.main import app
from backend.models import DocumentDraft, DraftCitation
from backend.schemas.case_analyst import EvidenceRef
from backend.schemas.claim_direction_proposal import FactRef
from backend.tests.integration.test_pleading_writer import _seed_writer_world


@pytest.fixture
def api_client(db_session: Session):
    from backend.infrastructure.db import get_db_session

    def _override():
        yield db_session

    app.dependency_overrides[get_db_session] = _override
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def _seed_production_world(db_session, owner_id, actor_id):
    svc, case, party_keys, facts, evidences, _claim_dir = _seed_writer_world(
        db_session, owner_id=owner_id, actor_id=actor_id
    )
    issue = svc.propose_issue(case_id=case.id, statement="被告是否应支付服务费")
    svc.confirm_issue(issue.issue_key, actor_id=actor_id)
    issue_cur = svc.repo.get_current_issue(issue.issue_key)
    assert issue_cur

    claim = svc.propose_claim(
        case_id=case.id,
        claim_type="PAYMENT",
        title="支付服务费",
        statement="请求被告支付剩余服务费700000元",
        amount=700000.0,
        currency="CNY",
    )
    confirmed = svc.confirm_claim(claim.claim_key, actor_id=actor_id)
    svc.link_issue_to_claim(
        case_id=case.id,
        claim_key=confirmed.claim_key,
        claim_version=confirmed.version,
        issue_key=issue_cur.issue_key,
        issue_version=issue_cur.version,
        role="BASIS",
        actor_id=actor_id,
    )
    svc.link_fact_to_claim(
        case_id=case.id,
        claim_key=confirmed.claim_key,
        claim_version=confirmed.version,
        fact_key=facts[-1].fact_key,
        fact_version=facts[-1].version,
        role="AMOUNT_BASIS",
        actor_id=actor_id,
    )
    svc.link_fact_to_issue(
        case_id=case.id,
        issue_key=issue_cur.issue_key,
        issue_version=issue_cur.version,
        fact_key=facts[0].fact_key,
        fact_version=facts[0].version,
        role="SUPPORT",
        actor_id=actor_id,
    )
    return svc, case, party_keys, facts, evidences, confirmed, issue_cur


def test_confirmed_claim_in_structured_input(db_session, owner_id, actor_id):
    svc, case, party_keys, facts, evidences, confirmed, _ = _seed_production_world(
        db_session, owner_id, actor_id
    )
    builder = PleadingStructuredInputProductionBuilder(db_session)
    inp = builder.build(
        case_id=case.id,
        parties=builder._load_confirmed_parties(case.id),
        facts=builder._load_confirmed_facts_for_case(case.id),
        evidence=builder._load_accepted_evidence_for_case(case.id),
    )
    assert len(inp.claims) == 1
    assert inp.claims[0].claim_key == str(confirmed.claim_key)
    assert inp.snapshot_meta.claim_source == "CLAIM_DOMAIN"
    assert inp.claim_issue_relations
    assert inp.claim_fact_relations
    assert any(r.role == "AMOUNT_BASIS" for r in inp.claim_fact_relations)


def test_candidate_claim_excluded(db_session, owner_id, actor_id):
    svc, case, _, _, _, _, _ = _seed_production_world(
        db_session, owner_id, actor_id
    )
    svc.propose_claim(
        case_id=case.id,
        claim_type="PAYMENT",
        title="候选",
        statement="候选诉请",
    )
    builder = PleadingStructuredInputProductionBuilder(db_session)
    inp = builder.build(
        case_id=case.id,
        parties=builder._load_confirmed_parties(case.id),
        facts=builder._load_confirmed_facts_for_case(case.id),
        evidence=builder._load_accepted_evidence_for_case(case.id),
    )
    assert len(inp.claims) == 1
    assert all("候选" not in c.title for c in inp.claims)


def test_confirmation_hash_deterministic(db_session, owner_id, actor_id):
    _, case, _, _, _, _, _ = _seed_production_world(
        db_session, owner_id, actor_id
    )
    builder = PleadingStructuredInputProductionBuilder(db_session)
    a = builder.build_preview(case.id)
    b = builder.build_preview(case.id)
    assert a.snapshot_meta.confirmation_set_hash == b.snapshot_meta.confirmation_set_hash


def test_hash_changes_on_claim_amend(db_session, owner_id, actor_id):
    svc, case, _, _, _, confirmed, _ = _seed_production_world(
        db_session, owner_id, actor_id
    )
    builder = PleadingStructuredInputProductionBuilder(db_session)
    h1 = builder.build_preview(case.id).snapshot_meta.confirmation_set_hash
    svc.amend_claim(
        confirmed.claim_key,
        statement="修订诉请金额表述",
        actor_id=actor_id,
    )
    h2 = builder.build_preview(case.id).snapshot_meta.confirmation_set_hash
    assert h1 != h2


def test_writer_uses_structured_input_and_snapshot(db_session, owner_id, actor_id):
    _, case, party_keys, facts, evidences, confirmed, _ = _seed_production_world(
        db_session, owner_id, actor_id
    )
    writer = PleadingWriterService(db_session)
    result = writer.write(
        case_id=case.id,
        claim_direction_ref=None,
        confirmed_fact_refs=[
            FactRef(fact_key=f.fact_key, fact_version=f.version) for f in facts
        ],
        accepted_evidence_refs=[
            EvidenceRef(
                evidence_item_id=e.id, evidence_item_version=e.version
            )
            for e in evidences
        ],
        confirmed_party_keys=party_keys,
        actor_id=actor_id,
    )
    assert result.draft is not None
    snap = result.draft.body_structured_json.get("structured_input_snapshot")
    assert snap is not None
    assert snap["snapshot_meta"]["claim_source"] == "CLAIM_DOMAIN"
    assert any(c["claim_key"] == str(confirmed.claim_key) for c in snap["claims"])
    cites = db_session.scalars(
        select(DraftCitation).where(DraftCitation.draft_id == result.draft.id)
    ).all()
    assert cites
    assert all(c.fact_version for c in cites)


def test_amend_stales_old_draft_not_overwrite(db_session, owner_id, actor_id):
    svc, case, party_keys, facts, evidences, confirmed, _ = _seed_production_world(
        db_session, owner_id, actor_id
    )
    writer = PleadingWriterService(db_session)
    r1 = writer.write(
        case_id=case.id,
        claim_direction_ref=None,
        confirmed_fact_refs=[
            FactRef(fact_key=f.fact_key, fact_version=f.version) for f in facts
        ],
        accepted_evidence_refs=[
            EvidenceRef(
                evidence_item_id=e.id, evidence_item_version=e.version
            )
            for e in evidences
        ],
        confirmed_party_keys=party_keys,
        actor_id=actor_id,
    )
    old_hash = r1.draft.based_on_confirmation_set_hash
    svc.amend_claim(
        confirmed.claim_key,
        statement="修订后诉请",
        actor_id=actor_id,
    )
    old = db_session.get(DocumentDraft, r1.draft.id)
    assert old.status == "STALE"
    r2 = writer.write(
        case_id=case.id,
        claim_direction_ref=None,
        confirmed_fact_refs=[
            FactRef(fact_key=f.fact_key, fact_version=f.version) for f in facts
        ],
        accepted_evidence_refs=[
            EvidenceRef(
                evidence_item_id=e.id, evidence_item_version=e.version
            )
            for e in evidences
        ],
        confirmed_party_keys=party_keys,
        actor_id=actor_id,
    )
    assert r2.draft.version == 2
    assert r2.draft.based_on_confirmation_set_hash != old_hash
    assert old.based_on_confirmation_set_hash == old_hash


def test_api_pleading_input(api_client, db_session, owner_id, actor_id):
    _, case, _, _, _, _, _ = _seed_production_world(
        db_session, owner_id, actor_id
    )
    res = api_client.get(f"/api/cases/{case.id}/pleading-input")
    assert res.status_code == 200
    body = res.json()
    assert body["claims"]
    assert body["snapshot_meta"]["confirmation_set_hash"]


def test_workspace_pleading_input_summary(api_client, db_session, owner_id, actor_id):
    _, case, _, _, _, _, _ = _seed_production_world(
        db_session, owner_id, actor_id
    )
    ws = api_client.get(f"/api/cases/{case.id}/workspace").json()
    summary = ws.get("pleading_input_summary")
    assert summary["confirmed_claim_count"] == 1
    assert summary["confirmation_set_hash"]
