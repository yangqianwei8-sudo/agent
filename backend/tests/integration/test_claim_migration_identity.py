"""Integration — ClaimDirection legacy migration identity remediation."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from backend.domain.services import DomainService
from backend.models import Claim


def _claim_item(description: str, *, claim_type: str = "PAYMENT", amount: float = 100000):
    return {
        "claim_type": claim_type,
        "description": description,
        "amount": amount,
        "currency": "CNY",
        "supporting_fact_ids": [],
    }


def _insert_direction_version(
    db_session: Session,
    *,
    case_id: uuid.UUID,
    direction_key: uuid.UUID,
    version: int,
    is_current: bool,
    status: str,
    claims: list[dict],
) -> None:
    row_id = uuid.uuid4()
    db_session.execute(
        text(
            """
            INSERT INTO claim_directions (
                id, claim_direction_key, case_id, version, is_current, status,
                payload, stale, created_at
            ) VALUES (
                :id, :key, :case_id, :version, :is_current, :status,
                CAST(:payload AS jsonb), false, now()
            )
            """
        ),
        {
            "id": row_id,
            "key": direction_key,
            "case_id": case_id,
            "version": version,
            "is_current": is_current,
            "status": status,
            "payload": __import__("json").dumps(
                {"overall_strategy": "测试策略", "claims": claims}
            ),
        },
    )
    db_session.flush()


def _run_remediation(db_session: Session) -> None:
    """Apply corrective migration steps (mirrors f7a8b9c0d1e2)."""
    from backend.migration.claim_identity import insert_claims_from_claim_directions

    db_session.execute(
        text(
            """
            DELETE FROM claim_issue_links
            WHERE claim_key IN (
                SELECT claim_key FROM claims
                WHERE legacy_claim_direction_key IS NOT NULL
            )
            """
        )
    )
    db_session.execute(
        text(
            """
            DELETE FROM claim_fact_links
            WHERE claim_key IN (
                SELECT claim_key FROM claims
                WHERE legacy_claim_direction_key IS NOT NULL
            )
            """
        )
    )
    db_session.execute(
        text("DELETE FROM claims WHERE legacy_claim_direction_key IS NOT NULL")
    )
    rows = db_session.execute(
        text(
            """
            SELECT id, claim_direction_key, case_id, version, is_current, status,
                   payload, confirm_decision_id, stale, stale_reason, stale_at,
                   created_at, updated_at, supersedes_id
            FROM claim_directions
            ORDER BY claim_direction_key, version
            """
        )
    ).fetchall()
    insert_claims_from_claim_directions(
        db_session.connection(), list(rows), include_provenance_columns=True
    )
    db_session.flush()


def test_legacy_insert_acb_identity(
    db_session: Session, owner_id: uuid.UUID, actor_id: uuid.UUID
) -> None:
    """V1 A,B → V2 A,C,B: A/B preserved, C new."""
    svc = DomainService(db_session)
    case = svc.create_case(title="Legacy identity", owner_user_id=owner_id)
    direction_key = uuid.uuid4()

    _insert_direction_version(
        db_session,
        case_id=case.id,
        direction_key=direction_key,
        version=1,
        is_current=False,
        status="SUPERSEDED",
        claims=[
            _claim_item("诉请A支付服务费"),
            _claim_item("诉请B违约金", amount=50000),
        ],
    )
    _insert_direction_version(
        db_session,
        case_id=case.id,
        direction_key=direction_key,
        version=2,
        is_current=True,
        status="CONFIRMED",
        claims=[
            _claim_item("诉请A支付服务费"),
            _claim_item("诉请C诉讼费", amount=8000),
            _claim_item("诉请B违约金", amount=50000),
        ],
    )
    _run_remediation(db_session)

    rows = list(
        db_session.scalars(
            select(Claim)
            .where(Claim.legacy_claim_direction_key == direction_key)
            .order_by(Claim.version, Claim.statement)
        )
    )
    assert len(rows) == 5  # 2 + 3

    a_keys = {
        r.claim_key
        for r in rows
        if "诉请A" in r.statement and r.version in {1, 2}
    }
    b_keys = {
        r.claim_key
        for r in rows
        if "诉请B" in r.statement and r.version in {1, 2}
    }
    c_keys = {r.claim_key for r in rows if "诉请C" in r.statement}
    assert len(a_keys) == 1
    assert len(b_keys) == 1
    assert len(c_keys) == 1
    assert c_keys.isdisjoint(a_keys | b_keys)

    distinct_keys = db_session.scalar(
        select(func.count(func.distinct(Claim.claim_key))).where(
            Claim.legacy_claim_direction_key == direction_key
        )
    )
    assert distinct_keys == 3


def test_index_only_merge_would_be_wrong(
    db_session: Session, owner_id: uuid.UUID
) -> None:
    """V1 idx=1 违约金 → V2 idx=1 诉讼费 must NOT share claim_key."""
    svc = DomainService(db_session)
    case = svc.create_case(title="No false merge", owner_user_id=owner_id)
    direction_key = uuid.uuid4()

    _insert_direction_version(
        db_session,
        case_id=case.id,
        direction_key=direction_key,
        version=1,
        is_current=False,
        status="SUPERSEDED",
        claims=[
            _claim_item("支付服务费"),
            _claim_item("违约金", amount=50000),
        ],
    )
    _insert_direction_version(
        db_session,
        case_id=case.id,
        direction_key=direction_key,
        version=2,
        is_current=True,
        status="CONFIRMED",
        claims=[
            _claim_item("支付服务费"),
            _claim_item("诉讼费", amount=8000),
        ],
    )
    _run_remediation(db_session)

    v1_penalty = db_session.scalars(
        select(Claim).where(
            Claim.legacy_claim_direction_key == direction_key,
            Claim.version == 1,
            Claim.statement.contains("违约金"),
        )
    ).one()
    v2_costs = db_session.scalars(
        select(Claim).where(
            Claim.legacy_claim_direction_key == direction_key,
            Claim.version == 2,
            Claim.statement.contains("诉讼费"),
        )
    ).one()
    assert v1_penalty.claim_key != v2_costs.claim_key
