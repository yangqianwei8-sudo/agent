"""Reject historical invalid party name candidates via Domain (no SQL delete).

Finds current CANDIDATE parties whose names fail PartyNameValidator and
rejects them through DomainService.reject_party when still CANDIDATE.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from backend.agent.action_safety import PartyNameValidator
from backend.domain.services import DomainService
from backend.infrastructure.db import get_engine, get_session_factory
from backend.models import CaseParty


def main() -> None:
    get_engine()
    Session = get_session_factory()
    actor = uuid.UUID("00000000-0000-4000-8000-000000000001")
    rejected: list[dict] = []
    skipped: list[dict] = []

    with Session() as session:
        rows = list(
            session.scalars(
                select(CaseParty).where(
                    CaseParty.is_current.is_(True),
                    CaseParty.layer == "CANDIDATE",
                )
            )
        )
        domain = DomainService(session)
        for p in rows:
            if PartyNameValidator.is_valid(p.name):
                continue
            info = {
                "party_key": str(p.party_key),
                "case_id": str(p.case_id),
                "role": p.role,
                "name": p.name,
                "layer": p.layer,
            }
            try:
                domain.reject_party(p.party_key, actor_id=actor)
                rejected.append(info)
                print("REJECTED", info)
            except Exception as exc:  # noqa: BLE001
                info["error"] = str(exc)
                skipped.append(info)
                print("SKIP", info)
        session.commit()

    print(f"done rejected={len(rejected)} skipped={len(skipped)}")


if __name__ == "__main__":
    main()
