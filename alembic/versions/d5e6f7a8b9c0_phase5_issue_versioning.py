"""phase5_issue_versioning — versioned Issue + semantic link tables."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d5e6f7a8b9c0"
down_revision: Union[str, Sequence[str], None] = "c4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("issues", sa.Column("issue_key", sa.UUID(), nullable=True))
    op.add_column("issues", sa.Column("version", sa.Integer(), nullable=True))
    op.add_column("issues", sa.Column("is_current", sa.Boolean(), nullable=True))
    op.add_column("issues", sa.Column("source_type", sa.String(length=32), nullable=True))
    op.add_column("issues", sa.Column("status", sa.String(length=32), nullable=True))
    op.add_column("issues", sa.Column("parent_issue_key", sa.UUID(), nullable=True))
    op.add_column("issues", sa.Column("change_reason", sa.Text(), nullable=True))
    op.add_column("issues", sa.Column("supersedes_id", sa.UUID(), nullable=True))
    op.add_column("issues", sa.Column("confirm_decision_id", sa.UUID(), nullable=True))
    op.add_column("issues", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))

    op.execute(
        """
        UPDATE issues SET
            issue_key = id,
            version = 1,
            is_current = true,
            status = layer,
            source_type = CASE
                WHEN analyst_run_id IS NOT NULL THEN 'AI_PROPOSED'
                ELSE 'LAWYER_CREATED'
            END
        WHERE issue_key IS NULL
        """
    )

    op.alter_column("issues", "issue_key", nullable=False)
    op.alter_column("issues", "version", nullable=False, server_default="1")
    op.alter_column("issues", "is_current", nullable=False, server_default=sa.text("true"))
    op.alter_column("issues", "source_type", nullable=False, server_default="AI_PROPOSED")
    op.alter_column("issues", "status", nullable=False, server_default="CANDIDATE")

    op.create_foreign_key(
        "fk_issues_supersedes",
        "issues",
        "issues",
        ["supersedes_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_issues_decision",
        "issues",
        "human_decisions",
        ["confirm_decision_id"],
        ["id"],
        use_alter=True,
    )
    op.create_unique_constraint("uq_issues_key_version", "issues", ["issue_key", "version"])
    op.create_index("ix_issues_case_current", "issues", ["case_id", "is_current"], unique=False)
    op.create_index(
        "uq_issues_current",
        "issues",
        ["issue_key"],
        unique=True,
        postgresql_where=sa.text("is_current = true"),
    )
    op.create_check_constraint(
        "ck_issues_status",
        "issues",
        "status IN ('CANDIDATE','CONFIRMED','REJECTED','SUPERSEDED')",
    )
    op.create_check_constraint(
        "ck_issues_source_type",
        "issues",
        "source_type IN ('AI_PROPOSED','LAWYER_CREATED','LAWYER_REFINED',"
        "'OPPONENT_RAISED','COURT_SUMMARIZED')",
    )

    op.create_table(
        "issue_fact_links",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("issue_key", sa.UUID(), nullable=False),
        sa.Column("issue_version", sa.Integer(), nullable=False),
        sa.Column("fact_key", sa.UUID(), nullable=False),
        sa.Column("fact_version", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('SUPPORT','ADVERSE','CONTEXT')",
            name="ck_issue_fact_link_role",
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE','VOID')",
            name="ck_issue_fact_link_status",
        ),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.ForeignKeyConstraint(
            ["issue_key", "issue_version"],
            ["issues.issue_key", "issues.version"],
            name="fk_issue_fact_links_issue",
        ),
        sa.ForeignKeyConstraint(
            ["fact_key", "fact_version"],
            ["facts.fact_key", "facts.version"],
            name="fk_issue_fact_links_fact",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "issue_key",
            "issue_version",
            "fact_key",
            "fact_version",
            "role",
            name="uq_issue_fact_links",
        ),
    )
    op.create_index(
        "ix_issue_fact_links_case", "issue_fact_links", ["case_id"], unique=False
    )

    op.create_table(
        "issue_evidence_links",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("issue_key", sa.UUID(), nullable=False),
        sa.Column("issue_version", sa.Integer(), nullable=False),
        sa.Column("evidence_item_id", sa.UUID(), nullable=False),
        sa.Column("evidence_item_version", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('SUPPORT','ADVERSE','CONTEXT')",
            name="ck_issue_evidence_link_role",
        ),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.ForeignKeyConstraint(
            ["issue_key", "issue_version"],
            ["issues.issue_key", "issues.version"],
            name="fk_issue_evidence_links_issue",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_item_id", "evidence_item_version"],
            ["evidence_items.id", "evidence_items.version"],
            name="fk_issue_evidence_links_evidence",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "issue_key",
            "issue_version",
            "evidence_item_id",
            "evidence_item_version",
            "role",
            name="uq_issue_evidence_links",
        ),
    )
    op.create_index(
        "ix_issue_evidence_links_case", "issue_evidence_links", ["case_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_issue_evidence_links_case", table_name="issue_evidence_links")
    op.drop_table("issue_evidence_links")
    op.drop_index("ix_issue_fact_links_case", table_name="issue_fact_links")
    op.drop_table("issue_fact_links")

    op.drop_constraint("ck_issues_source_type", "issues", type_="check")
    op.drop_constraint("ck_issues_status", "issues", type_="check")
    op.drop_index("uq_issues_current", table_name="issues")
    op.drop_index("ix_issues_case_current", table_name="issues")
    op.drop_constraint("uq_issues_key_version", "issues", type_="unique")
    op.drop_constraint("fk_issues_decision", "issues", type_="foreignkey")
    op.drop_constraint("fk_issues_supersedes", "issues", type_="foreignkey")

    op.drop_column("issues", "updated_at")
    op.drop_column("issues", "confirm_decision_id")
    op.drop_column("issues", "supersedes_id")
    op.drop_column("issues", "change_reason")
    op.drop_column("issues", "parent_issue_key")
    op.drop_column("issues", "status")
    op.drop_column("issues", "source_type")
    op.drop_column("issues", "is_current")
    op.drop_column("issues", "version")
    op.drop_column("issues", "issue_key")
