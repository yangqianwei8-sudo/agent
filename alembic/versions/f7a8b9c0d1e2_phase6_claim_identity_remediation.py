"""phase6_claim_identity_remediation — fix legacy claim_key mapping + provenance."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from backend.migration.claim_identity import insert_claims_from_claim_directions

revision: str = "f7a8b9c0d1e2"
down_revision: str | Sequence[str] | None = "e6f7a8b9c0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "claims",
        sa.Column("legacy_claim_direction_version", sa.Integer(), nullable=True),
    )
    op.add_column(
        "claims",
        sa.Column("legacy_claim_index", sa.Integer(), nullable=True),
    )
    op.add_column(
        "claims",
        sa.Column("legacy_source_ref", sa.String(length=512), nullable=True),
    )

    conn = op.get_bind()

    # Remap links that reference legacy-migrated claims before identity fix.
    conn.execute(
        sa.text(
            """
            DELETE FROM claim_issue_links
            WHERE claim_key IN (
                SELECT claim_key FROM claims
                WHERE legacy_claim_direction_key IS NOT NULL
            )
            """
        )
    )
    conn.execute(
        sa.text(
            """
            DELETE FROM claim_fact_links
            WHERE claim_key IN (
                SELECT claim_key FROM claims
                WHERE legacy_claim_direction_key IS NOT NULL
            )
            """
        )
    )
    conn.execute(
        sa.text("DELETE FROM claims WHERE legacy_claim_direction_key IS NOT NULL")
    )

    rows = conn.execute(
        sa.text(
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
        conn, list(rows), include_provenance_columns=True
    )


def downgrade() -> None:
    op.drop_column("claims", "legacy_source_ref")
    op.drop_column("claims", "legacy_claim_index")
    op.drop_column("claims", "legacy_claim_direction_version")
