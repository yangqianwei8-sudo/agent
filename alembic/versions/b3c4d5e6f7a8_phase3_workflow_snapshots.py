"""Add workflow_snapshots for immutable NodeRun input snapshots (Phase 3).

Reason: NodeRun.input_snapshot_ref needs a durable store; Phase 2 only had the
string ref column. Snapshots must survive process restart and be reused on retry.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b3c4d5e6f7a8"
down_revision: str | Sequence[str] | None = "19845b2e628d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflow_snapshots",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("instance_id", sa.UUID(), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("confirmation_set_hash", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["instance_id"], ["workflow_instances.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_workflow_snapshots_instance",
        "workflow_snapshots",
        ["instance_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_workflow_snapshots_instance", table_name="workflow_snapshots")
    op.drop_table("workflow_snapshots")
