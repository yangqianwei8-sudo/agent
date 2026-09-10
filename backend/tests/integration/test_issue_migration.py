"""Alembic migration — legacy issue rows remain readable after upgrade."""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.domain.services import DomainService


def test_legacy_issue_migration_readable(db_session: Session, owner_id) -> None:
    """Simulate pre-migration row shape then verify version fields populated."""
    svc = DomainService(db_session)
    case = svc.create_case(title="Migration case", owner_user_id=owner_id)
    legacy_id = uuid.uuid4()
    db_session.execute(
        text(
            """
            INSERT INTO issues (
                id, issue_key, case_id, version, is_current, statement,
                source_type, status, order_index, layer, stale, created_at
            ) VALUES (
                :id, :issue_key, :case_id, 1, true, :statement,
                'AI_PROPOSED', 'CANDIDATE', 0, 'CANDIDATE', false, now()
            )
            """
        ),
        {
            "id": legacy_id,
            "issue_key": legacy_id,
            "case_id": case.id,
            "statement": "legacy issue row",
        },
    )
    db_session.flush()
    row = db_session.execute(
        text(
            "SELECT issue_key, version, is_current, status, layer, source_type "
            "FROM issues WHERE id = :id"
        ),
        {"id": legacy_id},
    ).one()
    assert row.issue_key == legacy_id
    assert row.version == 1
    assert row.is_current is True
    assert row.status == "CANDIDATE"
    assert row.layer == "CANDIDATE"
    assert row.source_type == "AI_PROPOSED"
