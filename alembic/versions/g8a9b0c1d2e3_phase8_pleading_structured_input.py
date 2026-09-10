"""phase8_pleading_structured_input — DraftCitation source_span trace."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "g8a9b0c1d2e3"
down_revision: str | Sequence[str] | None = "f7a8b9c0d1e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "draft_citations",
        sa.Column("source_span_id", sa.UUID(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("draft_citations", "source_span_id")
