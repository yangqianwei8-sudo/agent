"""phase6_claim_domain — versioned Claim + link tables, migrate from ClaimDirection."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from backend.migration.claim_identity import insert_claims_from_claim_directions

revision: str = "e6f7a8b9c0d1"
down_revision: str | Sequence[str] | None = "d5e6f7a8b9c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "claims",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("claim_key", sa.UUID(), nullable=False),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("claim_type", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("amount", sa.Float(), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=True),
        sa.Column("amount_is_suggested", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("change_reason", sa.Text(), nullable=True),
        sa.Column("supersedes_id", sa.UUID(), nullable=True),
        sa.Column("confirm_decision_id", sa.UUID(), nullable=True),
        sa.Column("legacy_claim_direction_key", sa.UUID(), nullable=True),
        sa.Column("analyst_run_id", sa.UUID(), nullable=True),
        sa.Column("stale", sa.Boolean(), nullable=False),
        sa.Column("stale_reason", sa.String(length=64), nullable=True),
        sa.Column("stale_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('CANDIDATE','CONFIRMED','REJECTED','SUPERSEDED')",
            name="ck_claims_status",
        ),
        sa.CheckConstraint(
            "source_type IN ('AI_PROPOSED','LAWYER_CREATED','LAWYER_REFINED',"
            "'OPPONENT_RAISED','COURT_ADJUSTED')",
            name="ck_claims_source_type",
        ),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.ForeignKeyConstraint(["supersedes_id"], ["claims.id"]),
        sa.ForeignKeyConstraint(
            ["confirm_decision_id"],
            ["human_decisions.id"],
            name="fk_claims_decision",
            use_alter=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("claim_key", "version", name="uq_claims_key_version"),
    )
    op.create_index("ix_claims_case_current", "claims", ["case_id", "is_current"])
    op.create_index(
        "uq_claims_current",
        "claims",
        ["claim_key"],
        unique=True,
        postgresql_where=sa.text("is_current = true"),
    )

    op.create_table(
        "claim_issue_links",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("claim_key", sa.UUID(), nullable=False),
        sa.Column("claim_version", sa.Integer(), nullable=False),
        sa.Column("issue_key", sa.UUID(), nullable=False),
        sa.Column("issue_version", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('BASIS','LIMITATION','CONTEXT')",
            name="ck_claim_issue_link_role",
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE','VOID')",
            name="ck_claim_issue_link_status",
        ),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.ForeignKeyConstraint(
            ["claim_key", "claim_version"],
            ["claims.claim_key", "claims.version"],
            name="fk_claim_issue_links_claim",
        ),
        sa.ForeignKeyConstraint(
            ["issue_key", "issue_version"],
            ["issues.issue_key", "issues.version"],
            name="fk_claim_issue_links_issue",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "claim_key",
            "claim_version",
            "issue_key",
            "issue_version",
            "role",
            name="uq_claim_issue_links",
        ),
    )
    op.create_index("ix_claim_issue_links_case", "claim_issue_links", ["case_id"])

    op.create_table(
        "claim_fact_links",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("claim_key", sa.UUID(), nullable=False),
        sa.Column("claim_version", sa.Integer(), nullable=False),
        sa.Column("fact_key", sa.UUID(), nullable=False),
        sa.Column("fact_version", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('BASIS','AMOUNT_BASIS','LIMITATION','CONTEXT')",
            name="ck_claim_fact_link_role",
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE','VOID')",
            name="ck_claim_fact_link_status",
        ),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.ForeignKeyConstraint(
            ["claim_key", "claim_version"],
            ["claims.claim_key", "claims.version"],
            name="fk_claim_fact_links_claim",
        ),
        sa.ForeignKeyConstraint(
            ["fact_key", "fact_version"],
            ["facts.fact_key", "facts.version"],
            name="fk_claim_fact_links_fact",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "claim_key",
            "claim_version",
            "fact_key",
            "fact_version",
            "role",
            name="uq_claim_fact_links",
        ),
    )
    op.create_index("ix_claim_fact_links_case", "claim_fact_links", ["case_id"])

    conn = op.get_bind()
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
        conn, list(rows), include_provenance_columns=False
    )


def downgrade() -> None:
    op.drop_index("ix_claim_fact_links_case", table_name="claim_fact_links")
    op.drop_table("claim_fact_links")
    op.drop_index("ix_claim_issue_links_case", table_name="claim_issue_links")
    op.drop_table("claim_issue_links")
    op.drop_index("uq_claims_current", table_name="claims")
    op.drop_index("ix_claims_case_current", table_name="claims")
    op.drop_table("claims")
