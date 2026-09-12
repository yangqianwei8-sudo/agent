"""ORM models — Issue-centered workspace V2 domain objects."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class IssuePosition(Base):
    __tablename__ = "issue_positions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    position_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id"), nullable=False
    )
    issue_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    issue_version: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    side: Mapped[str] = mapped_column(String(32), nullable=False)
    position_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="CANDIDATE")
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    opponent_material_ref: Mapped[str | None] = mapped_column(String(512))
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("issue_positions.id")
    )
    confirm_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("human_decisions.id", use_alter=True, name="fk_issue_positions_decision"),
    )
    analyst_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["issue_key", "issue_version"],
            ["issues.issue_key", "issues.version"],
            name="fk_issue_positions_issue",
        ),
        UniqueConstraint("position_key", "version", name="uq_issue_positions_key_version"),
        CheckConstraint("side IN ('OUR','OPPONENT')", name="ck_issue_positions_side"),
        CheckConstraint(
            "position_type IN ('ASSERTION','ANTICIPATED_DEFENSE','FORMAL_DEFENSE')",
            name="ck_issue_positions_type",
        ),
        CheckConstraint(
            "source_type IN ('AI_PROPOSED','LAWYER_CREATED','LAWYER_REFINED','OPPONENT_MATERIAL')",
            name="ck_issue_positions_source",
        ),
        CheckConstraint(
            "status IN ('CANDIDATE','CONFIRMED','REJECTED','SUPERSEDED')",
            name="ck_issue_positions_status",
        ),
        Index("ix_issue_positions_case", "case_id"),
        Index(
            "uq_issue_positions_current",
            "position_key",
            unique=True,
            postgresql_where=text("is_current = true"),
        ),
    )


class ProofTask(Base):
    __tablename__ = "proof_tasks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    proof_task_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id"), nullable=False
    )
    issue_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    issue_version: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="CANDIDATE")
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="AI_PROPOSED")
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("proof_tasks.id")
    )
    confirm_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("human_decisions.id", use_alter=True, name="fk_proof_tasks_decision"),
    )
    analyst_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["issue_key", "issue_version"],
            ["issues.issue_key", "issues.version"],
            name="fk_proof_tasks_issue",
        ),
        UniqueConstraint("proof_task_key", "version", name="uq_proof_tasks_key_version"),
        CheckConstraint(
            "status IN ('CANDIDATE','ADOPTED','REJECTED','SUPERSEDED','WAIVED')",
            name="ck_proof_tasks_status",
        ),
        CheckConstraint(
            "source_type IN ('AI_PROPOSED','LAWYER_CREATED','LAWYER_REFINED')",
            name="ck_proof_tasks_source",
        ),
        Index("ix_proof_tasks_case", "case_id"),
        Index(
            "uq_proof_tasks_current",
            "proof_task_key",
            unique=True,
            postgresql_where=text("is_current = true"),
        ),
    )


class ProofTaskFactLink(Base):
    __tablename__ = "proof_task_fact_links"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id"), nullable=False
    )
    proof_task_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    proof_task_version: Mapped[int] = mapped_column(Integer, nullable=False)
    fact_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    fact_version: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="SUPPORT")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["proof_task_key", "proof_task_version"],
            ["proof_tasks.proof_task_key", "proof_tasks.version"],
            name="fk_proof_task_fact_links_task",
        ),
        ForeignKeyConstraint(
            ["fact_key", "fact_version"],
            ["facts.fact_key", "facts.version"],
            name="fk_proof_task_fact_links_fact",
        ),
        UniqueConstraint(
            "proof_task_key",
            "proof_task_version",
            "fact_key",
            "fact_version",
            "role",
            name="uq_proof_task_fact_links",
        ),
        CheckConstraint(
            "role IN ('SUPPORT','ADVERSE','CONTEXT')",
            name="ck_proof_task_fact_link_role",
        ),
        CheckConstraint(
            "status IN ('ACTIVE','VOID')",
            name="ck_proof_task_fact_link_status",
        ),
        Index("ix_proof_task_fact_links_case", "case_id"),
    )


class IssueConflict(Base):
    __tablename__ = "issue_conflicts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conflict_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id"), nullable=False
    )
    issue_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    issue_version: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="CANDIDATE")
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="AI_DETECTED")
    resolution_note: Mapped[str | None] = mapped_column(Text)
    resolve_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("human_decisions.id", use_alter=True, name="fk_issue_conflicts_decision"),
    )
    analyst_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["issue_key", "issue_version"],
            ["issues.issue_key", "issues.version"],
            name="fk_issue_conflicts_issue",
        ),
        CheckConstraint(
            "status IN ('CANDIDATE','OPEN','RESOLVED','DISMISSED')",
            name="ck_issue_conflicts_status",
        ),
        CheckConstraint(
            "source_type IN ('AI_DETECTED','LAWYER_CREATED')",
            name="ck_issue_conflicts_source",
        ),
        Index("ix_issue_conflicts_case", "case_id"),
        Index("ix_issue_conflicts_issue", "issue_key", "issue_version"),
    )


class ConflictFactLink(Base):
    __tablename__ = "conflict_fact_links"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id"), nullable=False
    )
    conflict_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("issue_conflicts.id"), nullable=False
    )
    fact_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    fact_version: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["fact_key", "fact_version"],
            ["facts.fact_key", "facts.version"],
            name="fk_conflict_fact_links_fact",
        ),
        UniqueConstraint(
            "conflict_id",
            "fact_key",
            "fact_version",
            "role",
            name="uq_conflict_fact_links",
        ),
        CheckConstraint(
            "role IN ('SIDE_A','SIDE_B','CONTEXT')",
            name="ck_conflict_fact_link_role",
        ),
        Index("ix_conflict_fact_links_case", "case_id"),
    )


class ProofGap(Base):
    __tablename__ = "proof_gaps"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    gap_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id"), nullable=False
    )
    issue_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    issue_version: Mapped[int] = mapped_column(Integer, nullable=False)
    proof_task_key: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    proof_task_version: Mapped[int | None] = mapped_column(Integer)
    gap_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="OPEN")
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="AI_DETECTED")
    description: Mapped[str] = mapped_column(Text, nullable=False)
    what_exists: Mapped[str | None] = mapped_column(Text)
    what_is_missing: Mapped[str | None] = mapped_column(Text)
    why_it_matters: Mapped[str | None] = mapped_column(Text)
    suggested_material_types: Mapped[list[Any] | None] = mapped_column(JSONB)
    resolution_note: Mapped[str | None] = mapped_column(Text)
    resolve_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("human_decisions.id", use_alter=True, name="fk_proof_gaps_decision"),
    )
    analyst_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["issue_key", "issue_version"],
            ["issues.issue_key", "issues.version"],
            name="fk_proof_gaps_issue",
        ),
        ForeignKeyConstraint(
            ["proof_task_key", "proof_task_version"],
            ["proof_tasks.proof_task_key", "proof_tasks.version"],
            name="fk_proof_gaps_proof_task",
        ),
        CheckConstraint(
            "gap_type IN ('FACT','EVIDENCE','SOURCE','LEGAL_RESEARCH')",
            name="ck_proof_gaps_type",
        ),
        CheckConstraint(
            "status IN ('OPEN','RESOLVED','WAIVED','SUPERSEDED')",
            name="ck_proof_gaps_status",
        ),
        CheckConstraint(
            "source_type IN ('AI_DETECTED','LAWYER_CREATED')",
            name="ck_proof_gaps_source",
        ),
        Index("ix_proof_gaps_case", "case_id"),
        Index("ix_proof_gaps_issue", "issue_key", "issue_version"),
    )


class LawyerAssessment(Base):
    __tablename__ = "lawyer_assessments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    assessment_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id"), nullable=False
    )
    issue_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    issue_version: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("lawyer_assessments.id")
    )
    confirm_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("human_decisions.id", use_alter=True, name="fk_lawyer_assessments_decision"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["issue_key", "issue_version"],
            ["issues.issue_key", "issues.version"],
            name="fk_lawyer_assessments_issue",
        ),
        UniqueConstraint("assessment_key", "version", name="uq_lawyer_assessments_key_version"),
        CheckConstraint(
            "status IN ('ACTIVE','SUPERSEDED','WITHDRAWN')",
            name="ck_lawyer_assessments_status",
        ),
        Index("ix_lawyer_assessments_case", "case_id"),
        Index(
            "uq_lawyer_assessments_current",
            "assessment_key",
            unique=True,
            postgresql_where=text("is_current = true"),
        ),
    )


class IssueLegalTheoryLink(Base):
    __tablename__ = "issue_legal_theory_links"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id"), nullable=False
    )
    issue_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    issue_version: Mapped[int] = mapped_column(Integer, nullable=False)
    legal_theory_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("legal_theories.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="OUR_THEORY")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["issue_key", "issue_version"],
            ["issues.issue_key", "issues.version"],
            name="fk_issue_legal_theory_links_issue",
        ),
        UniqueConstraint(
            "issue_key",
            "issue_version",
            "legal_theory_id",
            "role",
            name="uq_issue_legal_theory_links",
        ),
        CheckConstraint(
            "role IN ('OUR_THEORY','COUNTER_THEORY','CONTEXT')",
            name="ck_issue_legal_theory_link_role",
        ),
        CheckConstraint(
            "status IN ('ACTIVE','VOID')",
            name="ck_issue_legal_theory_link_status",
        ),
        Index("ix_issue_legal_theory_links_case", "case_id"),
    )
