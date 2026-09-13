"""phase9_issue_centered_v2 — IssuePosition, ProofTask, Conflict, ProofGap, LawyerAssessment.

Issue #60 / #73 / #80 SSOT: includes proof_gaps and lawyer_assessments with every CheckConstraint:
- proof_gaps: ck_proof_gaps_type, ck_proof_gaps_status, ck_proof_gaps_source
- lawyer_assessments: ck_lawyer_assessments_status
- issue_positions: ck_issue_positions_side/type/source/status
- proof_tasks: ck_proof_tasks_status, ck_proof_tasks_source
- proof_task_fact_links: ck_proof_task_fact_link_role, ck_proof_task_fact_link_status
- issue_conflicts: ck_issue_conflicts_status, ck_issue_conflicts_source
- conflict_fact_links: ck_conflict_fact_link_role
- issue_legal_theory_links: ck_issue_legal_theory_link_role, ck_issue_legal_theory_link_status
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# INV SSOT (#73): canonical allowed-value sets for CheckConstraints (used in upgrade()).
_PROOF_GAP_TYPES = ("FACT", "EVIDENCE", "SOURCE", "LEGAL_RESEARCH")
_PROOF_GAP_STATUSES = ("OPEN", "RESOLVED", "WAIVED", "SUPERSEDED")
_PROOF_GAP_SOURCES = ("AI_DETECTED", "LAWYER_CREATED")
_LAWYER_ASSESSMENT_STATUSES = ("ACTIVE", "SUPERSEDED", "WITHDRAWN")


def _in_check(name: str, column: str, values: tuple[str, ...]) -> sa.CheckConstraint:
    quoted = ", ".join(f"'{v}'" for v in values)
    return sa.CheckConstraint(f"{column} IN ({quoted})", name=name)


revision: str = "h9b0c1d2e3f4"
down_revision: str | Sequence[str] | None = "g8a9b0c1d2e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "issue_positions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("position_key", sa.UUID(), nullable=False),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("issue_key", sa.UUID(), nullable=False),
        sa.Column("issue_version", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("side", sa.String(length=32), nullable=False),
        sa.Column("position_type", sa.String(length=32), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="CANDIDATE"),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("opponent_material_ref", sa.String(length=512), nullable=True),
        sa.Column("supersedes_id", sa.UUID(), nullable=True),
        sa.Column("confirm_decision_id", sa.UUID(), nullable=True),
        sa.Column("analyst_run_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.ForeignKeyConstraint(
            ["issue_key", "issue_version"],
            ["issues.issue_key", "issues.version"],
            name="fk_issue_positions_issue",
        ),
        sa.ForeignKeyConstraint(["supersedes_id"], ["issue_positions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("position_key", "version", name="uq_issue_positions_key_version"),
        sa.CheckConstraint("side IN ('OUR','OPPONENT')", name="ck_issue_positions_side"),
        sa.CheckConstraint(
            "position_type IN ('ASSERTION','ANTICIPATED_DEFENSE','FORMAL_DEFENSE')",
            name="ck_issue_positions_type",
        ),
        sa.CheckConstraint(
            "source_type IN ('AI_PROPOSED','LAWYER_CREATED','LAWYER_REFINED','OPPONENT_MATERIAL')",
            name="ck_issue_positions_source",
        ),
        sa.CheckConstraint(
            "status IN ('CANDIDATE','CONFIRMED','REJECTED','SUPERSEDED')",
            name="ck_issue_positions_status",
        ),
    )
    op.create_index("ix_issue_positions_case", "issue_positions", ["case_id"])
    op.create_index(
        "uq_issue_positions_current",
        "issue_positions",
        ["position_key"],
        unique=True,
        postgresql_where=sa.text("is_current = true"),
    )

    op.create_table(
        "proof_tasks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("proof_task_key", sa.UUID(), nullable=False),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("issue_key", sa.UUID(), nullable=False),
        sa.Column("issue_version", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="CANDIDATE"),
        sa.Column("source_type", sa.String(length=32), nullable=False, server_default="AI_PROPOSED"),
        sa.Column("supersedes_id", sa.UUID(), nullable=True),
        sa.Column("confirm_decision_id", sa.UUID(), nullable=True),
        sa.Column("analyst_run_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.ForeignKeyConstraint(
            ["issue_key", "issue_version"],
            ["issues.issue_key", "issues.version"],
            name="fk_proof_tasks_issue",
        ),
        sa.ForeignKeyConstraint(["supersedes_id"], ["proof_tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("proof_task_key", "version", name="uq_proof_tasks_key_version"),
        sa.CheckConstraint(
            "status IN ('CANDIDATE','ADOPTED','REJECTED','SUPERSEDED','WAIVED')",
            name="ck_proof_tasks_status",
        ),
        sa.CheckConstraint(
            "source_type IN ('AI_PROPOSED','LAWYER_CREATED','LAWYER_REFINED')",
            name="ck_proof_tasks_source",
        ),
    )
    op.create_index("ix_proof_tasks_case", "proof_tasks", ["case_id"])
    op.create_index(
        "uq_proof_tasks_current",
        "proof_tasks",
        ["proof_task_key"],
        unique=True,
        postgresql_where=sa.text("is_current = true"),
    )

    op.create_table(
        "proof_task_fact_links",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("proof_task_key", sa.UUID(), nullable=False),
        sa.Column("proof_task_version", sa.Integer(), nullable=False),
        sa.Column("fact_key", sa.UUID(), nullable=False),
        sa.Column("fact_version", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False, server_default="SUPPORT"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="ACTIVE"),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.ForeignKeyConstraint(
            ["proof_task_key", "proof_task_version"],
            ["proof_tasks.proof_task_key", "proof_tasks.version"],
            name="fk_proof_task_fact_links_task",
        ),
        sa.ForeignKeyConstraint(
            ["fact_key", "fact_version"],
            ["facts.fact_key", "facts.version"],
            name="fk_proof_task_fact_links_fact",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "proof_task_key",
            "proof_task_version",
            "fact_key",
            "fact_version",
            "role",
            name="uq_proof_task_fact_links",
        ),
        sa.CheckConstraint(
            "role IN ('SUPPORT','ADVERSE','CONTEXT')",
            name="ck_proof_task_fact_link_role",
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE','VOID')",
            name="ck_proof_task_fact_link_status",
        ),
    )
    op.create_index("ix_proof_task_fact_links_case", "proof_task_fact_links", ["case_id"])

    op.create_table(
        "issue_conflicts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("conflict_key", sa.UUID(), nullable=False),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("issue_key", sa.UUID(), nullable=False),
        sa.Column("issue_version", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="CANDIDATE"),
        sa.Column("source_type", sa.String(length=32), nullable=False, server_default="AI_DETECTED"),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("resolve_decision_id", sa.UUID(), nullable=True),
        sa.Column("analyst_run_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.ForeignKeyConstraint(
            ["issue_key", "issue_version"],
            ["issues.issue_key", "issues.version"],
            name="fk_issue_conflicts_issue",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('CANDIDATE','OPEN','RESOLVED','DISMISSED')",
            name="ck_issue_conflicts_status",
        ),
        sa.CheckConstraint(
            "source_type IN ('AI_DETECTED','LAWYER_CREATED')",
            name="ck_issue_conflicts_source",
        ),
    )
    op.create_index("ix_issue_conflicts_case", "issue_conflicts", ["case_id"])
    op.create_index(
        "ix_issue_conflicts_issue", "issue_conflicts", ["issue_key", "issue_version"]
    )

    op.create_table(
        "conflict_fact_links",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("conflict_id", sa.UUID(), nullable=False),
        sa.Column("fact_key", sa.UUID(), nullable=False),
        sa.Column("fact_version", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.ForeignKeyConstraint(["conflict_id"], ["issue_conflicts.id"]),
        sa.ForeignKeyConstraint(
            ["fact_key", "fact_version"],
            ["facts.fact_key", "facts.version"],
            name="fk_conflict_fact_links_fact",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "conflict_id",
            "fact_key",
            "fact_version",
            "role",
            name="uq_conflict_fact_links",
        ),
        sa.CheckConstraint(
            "role IN ('SIDE_A','SIDE_B','CONTEXT')",
            name="ck_conflict_fact_link_role",
        ),
    )
    op.create_index("ix_conflict_fact_links_case", "conflict_fact_links", ["case_id"])

    op.create_table(
        "proof_gaps",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("gap_key", sa.UUID(), nullable=False),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("issue_key", sa.UUID(), nullable=False),
        sa.Column("issue_version", sa.Integer(), nullable=False),
        sa.Column("proof_task_key", sa.UUID(), nullable=True),
        sa.Column("proof_task_version", sa.Integer(), nullable=True),
        sa.Column("gap_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="OPEN"),
        sa.Column("source_type", sa.String(length=32), nullable=False, server_default="AI_DETECTED"),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("what_exists", sa.Text(), nullable=True),
        sa.Column("what_is_missing", sa.Text(), nullable=True),
        sa.Column("why_it_matters", sa.Text(), nullable=True),
        sa.Column("suggested_material_types", sa.JSON(), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("resolve_decision_id", sa.UUID(), nullable=True),
        sa.Column("analyst_run_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.ForeignKeyConstraint(
            ["issue_key", "issue_version"],
            ["issues.issue_key", "issues.version"],
            name="fk_proof_gaps_issue",
        ),
        sa.ForeignKeyConstraint(
            ["proof_task_key", "proof_task_version"],
            ["proof_tasks.proof_task_key", "proof_tasks.version"],
            name="fk_proof_gaps_proof_task",
        ),
        sa.PrimaryKeyConstraint("id"),
        _in_check("ck_proof_gaps_type", "gap_type", _PROOF_GAP_TYPES),
        _in_check("ck_proof_gaps_status", "status", _PROOF_GAP_STATUSES),
        _in_check("ck_proof_gaps_source", "source_type", _PROOF_GAP_SOURCES),
    )
    op.create_index("ix_proof_gaps_case", "proof_gaps", ["case_id"])
    op.create_index("ix_proof_gaps_issue", "proof_gaps", ["issue_key", "issue_version"])

    op.create_table(
        "lawyer_assessments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("assessment_key", sa.UUID(), nullable=False),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("issue_key", sa.UUID(), nullable=False),
        sa.Column("issue_version", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="ACTIVE"),
        sa.Column("supersedes_id", sa.UUID(), nullable=True),
        sa.Column("confirm_decision_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.ForeignKeyConstraint(
            ["issue_key", "issue_version"],
            ["issues.issue_key", "issues.version"],
            name="fk_lawyer_assessments_issue",
        ),
        sa.ForeignKeyConstraint(["supersedes_id"], ["lawyer_assessments.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("assessment_key", "version", name="uq_lawyer_assessments_key_version"),
        _in_check("ck_lawyer_assessments_status", "status", _LAWYER_ASSESSMENT_STATUSES),
    )
    op.create_index("ix_lawyer_assessments_case", "lawyer_assessments", ["case_id"])
    op.create_index(
        "uq_lawyer_assessments_current",
        "lawyer_assessments",
        ["assessment_key"],
        unique=True,
        postgresql_where=sa.text("is_current = true"),
    )

    op.create_table(
        "issue_legal_theory_links",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("issue_key", sa.UUID(), nullable=False),
        sa.Column("issue_version", sa.Integer(), nullable=False),
        sa.Column("legal_theory_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False, server_default="OUR_THEORY"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="ACTIVE"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.ForeignKeyConstraint(
            ["issue_key", "issue_version"],
            ["issues.issue_key", "issues.version"],
            name="fk_issue_legal_theory_links_issue",
        ),
        sa.ForeignKeyConstraint(["legal_theory_id"], ["legal_theories.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "issue_key",
            "issue_version",
            "legal_theory_id",
            "role",
            name="uq_issue_legal_theory_links",
        ),
        sa.CheckConstraint(
            "role IN ('OUR_THEORY','COUNTER_THEORY','CONTEXT')",
            name="ck_issue_legal_theory_link_role",
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE','VOID')",
            name="ck_issue_legal_theory_link_status",
        ),
    )
    op.create_index("ix_issue_legal_theory_links_case", "issue_legal_theory_links", ["case_id"])


def downgrade() -> None:
    op.drop_table("issue_legal_theory_links")
    op.drop_table("lawyer_assessments")
    op.drop_table("proof_gaps")
    op.drop_table("conflict_fact_links")
    op.drop_table("issue_conflicts")
    op.drop_table("proof_task_fact_links")
    op.drop_table("proof_tasks")
    op.drop_table("issue_positions")
