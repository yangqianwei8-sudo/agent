"""phase6_claim_identity_remediation — fix legacy claim_key mapping + provenance."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from backend.migration.claim_identity import remediate_legacy_claim_identity

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

    remediate_legacy_claim_identity(op.get_bind())


def downgrade() -> None:
    op.drop_column("claims", "legacy_source_ref")
    op.drop_column("claims", "legacy_claim_index")
    op.drop_column("claims", "legacy_claim_direction_version")
