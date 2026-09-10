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

from sqlalchemy import select, text  # noqa: E402

from backend.domain.services import DomainService  # noqa: E402
from backend.infrastructure.db import get_session_factory  # noqa: E402
from backend.models import Claim  # noqa: E402
from backend.migration.claim_identity import insert_claims_from_claim_directions  # noqa: E402


def _claim(description: str, *, amount: float = 100000) -> dict:
    return {
        "claim_type": "PAYMENT",
        "description": description,
        "amount": amount,
        "currency": "CNY",
        "supporting_fact_ids": [],
    }


def main() -> int:
    factory = get_session_factory()
    with factory() as session:
        svc = DomainService(session)
        actor = uuid.uuid4()
        case = svc.create_case(title="Live legacy identity", owner_user_id=actor)
        direction_key = uuid.uuid4()

        for version, is_current, status, claims in (
            (
                1,
                False,
                "SUPERSEDED",
                [_claim("诉请A"), _claim("诉请B", amount=50000)],
            ),
            (
                2,
                True,
                "CONFIRMED",
                [
                    _claim("诉请A"),
                    _claim("诉请C", amount=8000),
                    _claim("诉请B", amount=50000),
                ],
            ),
        ):
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
                    "case_id": case.id,
                    "version": version,
                    "is_current": is_current,
                    "status": status,
                    "payload": json.dumps(
                        {"overall_strategy": "live", "claims": claims}
                    ),
                },
            )
        session.flush()

        session.execute(
            text(
                "DELETE FROM claims WHERE legacy_claim_direction_key IS NOT NULL"
            )
        )
        rows = session.execute(
            text(
                """
                SELECT id, claim_direction_key, case_id, version, is_current, status,
                       payload, confirm_decision_id, stale, stale_reason, stale_at,
                       created_at, updated_at, supersedes_id
                FROM claim_directions
                WHERE claim_direction_key = :key
                ORDER BY version
                """
            ),
            {"key": direction_key},
        ).fetchall()
        insert_claims_from_claim_directions(
            session.connection(), list(rows), include_provenance_columns=True
        )
        session.commit()

        claims = list(
            session.scalars(
                select(Claim)
                .where(Claim.legacy_claim_direction_key == direction_key)
                .order_by(Claim.version, Claim.legacy_claim_index)
            )
        )
        a_key = next(c.claim_key for c in claims if "诉请A" in c.statement)
        b_key = next(c.claim_key for c in claims if "诉请B" in c.statement)
        c_key = next(c.claim_key for c in claims if "诉请C" in c.statement)

        a_versions = {c.version for c in claims if c.claim_key == a_key}
        b_versions = {c.version for c in claims if c.claim_key == b_key}
        assert a_versions == {1, 2}, a_versions
        assert b_versions == {1, 2}, b_versions
        assert c_key not in {a_key, b_key}
        assert len(claims) == 5

        print("PASS live_claim_migration_identity_acceptance")
        print(f"case_id={case.id} direction_key={direction_key}")
        print(f"A={a_key} B={b_key} C={c_key}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
