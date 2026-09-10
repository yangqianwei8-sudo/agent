"""Live acceptance — legacy ClaimDirection migration identity remediation."""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

os.environ.setdefault("LLM_MODE", "deterministic")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import func, select, text  # noqa: E402

from backend.domain.services import DomainService  # noqa: E402
from backend.infrastructure.db import get_session_factory  # noqa: E402
from backend.models import Claim  # noqa: E402
from backend.migration.claim_identity import (  # noqa: E402
    assign_claim_keys_for_direction,
    remediate_legacy_claim_identity,
)


def _claim(
    description: str,
    *,
    amount: float = 100000,
    claim_type: str = "PAYMENT",
) -> dict:
    return {
        "claim_type": claim_type,
        "description": description,
        "amount": amount,
        "currency": "CNY",
        "supporting_fact_ids": [],
    }


def _insert_direction(
    session,
    *,
    case_id: uuid.UUID,
    direction_key: uuid.UUID,
    version: int,
    is_current: bool,
    status: str,
    claims: list[dict],
) -> None:
    session.execute(
        text(
            """
            INSERT INTO claim_directions (
                id, claim_direction_key, case_id, version, is_current,
                status, payload, stale, created_at
            ) VALUES (
                :id, :key, :case_id, :version, :is_current,
                :status, CAST(:payload AS jsonb), false, now()
            )
            """
        ),
        {
            "id": uuid.uuid4(),
            "key": direction_key,
            "case_id": case_id,
            "version": version,
            "is_current": is_current,
            "status": status,
            "payload": json.dumps({"overall_strategy": "live", "claims": claims}),
        },
    )


def _assert_scenario(name: str, direction_key: uuid.UUID, claims: list[Claim]) -> None:
    print(f"  OK {name} keys={len({c.claim_key for c in claims})} rows={len(claims)}")


def main() -> int:
    factory = get_session_factory()
    with factory() as session:
        svc = DomainService(session)
        actor = uuid.uuid4()
        case = svc.create_case(title="Live legacy identity", owner_user_id=actor)

        # Scenario 1: insert A,C,B
        insert_key = uuid.uuid4()
        _insert_direction(
            session,
            case_id=case.id,
            direction_key=insert_key,
            version=1,
            is_current=False,
            status="SUPERSEDED",
            claims=[_claim("诉请A"), _claim("诉请B", amount=50000)],
        )
        _insert_direction(
            session,
            case_id=case.id,
            direction_key=insert_key,
            version=2,
            is_current=True,
            status="CONFIRMED",
            claims=[
                _claim("诉请A"),
                _claim("诉请C", amount=8000),
                _claim("诉请B", amount=50000),
            ],
        )

        # Scenario 2-5: pure matcher checks (no DB rows required)
        reorder_key = uuid.uuid4()
        reorder_map = assign_claim_keys_for_direction(
            reorder_key,
            [
                (1, [_claim("诉请A"), _claim("诉请B", amount=50000)]),
                (2, [_claim("诉请B", amount=50000), _claim("诉请A")]),
            ],
        )
        assert reorder_map[(1, 0)] == reorder_map[(2, 1)]
        assert reorder_map[(1, 1)] == reorder_map[(2, 0)]

        delete_key = uuid.uuid4()
        delete_map = assign_claim_keys_for_direction(
            delete_key,
            [
                (1, [_claim("诉请A"), _claim("诉请B", amount=50000)]),
                (2, [_claim("诉请B", amount=50000)]),
            ],
        )
        assert delete_map[(1, 1)] == delete_map[(2, 0)]
        assert delete_map[(1, 0)] != delete_map[(2, 0)]

        rewrite_key = uuid.uuid4()
        rewrite_map = assign_claim_keys_for_direction(
            rewrite_key,
            [
                (1, [_claim("支付服务费")]),
                (2, [_claim("解除合同", claim_type="TERMINATION", amount=0)]),
            ],
        )
        assert rewrite_map[(1, 0)] != rewrite_map[(2, 0)]

        amount_key = uuid.uuid4()
        amount_map = assign_claim_keys_for_direction(
            amount_key,
            [
                (1, [_claim("支付服务费", amount=1_000_000)]),
                (2, [_claim("支付服务费", amount=1_200_000)]),
            ],
        )
        assert amount_map[(1, 0)] == amount_map[(2, 0)]

        session.flush()
        remediate_legacy_claim_identity(session.connection())
        session.commit()

        insert_claims = list(
            session.scalars(
                select(Claim)
                .where(Claim.legacy_claim_direction_key == insert_key)
                .order_by(Claim.version, Claim.legacy_claim_index)
            )
        )
        a_key = next(c.claim_key for c in insert_claims if "诉请A" in c.statement)
        b_key = next(c.claim_key for c in insert_claims if "诉请B" in c.statement)
        c_key = next(c.claim_key for c in insert_claims if "诉请C" in c.statement)
        assert {c.version for c in insert_claims if c.claim_key == a_key} == {1, 2}
        assert {c.version for c in insert_claims if c.claim_key == b_key} == {1, 2}
        assert c_key not in {a_key, b_key}
        assert len(insert_claims) == 5

        v2_a = next(
            c for c in insert_claims if c.claim_key == a_key and c.version == 2
        )
        v1_a = next(
            c for c in insert_claims if c.claim_key == a_key and c.version == 1
        )
        assert v2_a.supersedes_id == v1_a.id
        assert v2_a.is_current is True

        current_count = session.scalar(
            select(func.count())
            .select_from(Claim)
            .where(
                Claim.legacy_claim_direction_key == insert_key,
                Claim.is_current.is_(True),
            )
        )
        assert current_count == 3

        print("PASS live_claim_migration_identity_acceptance")
        print(f"case_id={case.id}")
        _assert_scenario("insert_acb", insert_key, insert_claims)
        print("  OK reorder")
        print("  OK delete")
        print("  OK rewrite")
        print("  OK amount_amendment")
        print(f"A={a_key} B={b_key} C={c_key}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
