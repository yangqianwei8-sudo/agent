"""ORM models — Evidence, Fact, Analysis artifacts, ClaimDirection."""

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


class EvidenceItem(Base):
    __tablename__ = "evidence_items"

    row_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    number: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    acceptance: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    organizer_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    stale_reason: Mapped[str | None] = mapped_column(String(64))
    stale_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("id", "version", name="uq_evidence_items_id_version"),
        CheckConstraint(
            "acceptance IN ('PENDING','ACCEPTED','EXCLUDED')",
            name="ck_evidence_acceptance",
        ),
        Index("ix_evidence_items_case_current", "case_id", "is_current"),
        Index(
            "uq_evidence_items_current",
            "id",
            unique=True,
            postgresql_where=text("is_current = true"),
        ),
    )


class EvidenceItemSpan(Base):
    __tablename__ = "evidence_item_spans"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    evidence_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    evidence_item_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_span_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_spans.id"), nullable=False
    )
    role_in_item: Mapped[str] = mapped_column(String(32), nullable=False, default="PRIMARY")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["evidence_item_id", "evidence_item_version"],
            ["evidence_items.id", "evidence_items.version"],
            name="fk_evidence_item_spans_item",
        ),
        UniqueConstraint(
            "evidence_item_id",
            "evidence_item_version",
            "source_span_id",
            name="uq_evidence_item_spans",
        ),
        CheckConstraint(
            "role_in_item IN ('PRIMARY','ATTACHMENT','SIGNATURE_PAGE')",
            name="ck_evidence_item_span_role",
        ),
    )


class Fact(Base):
    __tablename__ = "facts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    fact_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id"), nullable=False
    )
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="CANDIDATE")
    importance: Mapped[str] = mapped_column(String(32), nullable=False, default="SUPPORTING")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facts.id")
    )
    confirm_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("human_decisions.id", use_alter=True, name="fk_facts_decision"),
    )
    analyst_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    stale_reason: Mapped[str | None] = mapped_column(String(64))
    stale_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("fact_key", "version", name="uq_facts_key_version"),
        CheckConstraint(
            "status IN ('CANDIDATE','CONFIRMED','REJECTED','SUPERSEDED')",
            name="ck_facts_status",
        ),
        CheckConstraint(
            "importance IN ('CORE','SUPPORTING','BACKGROUND')",
            name="ck_facts_importance",
        ),
        Index("ix_facts_case_status_current", "case_id", "status", "is_current"),
        Index(
            "uq_facts_current",
            "fact_key",
            unique=True,
            postgresql_where=text("is_current = true"),
        ),
    )


class FactEvidenceLink(Base):
    __tablename__ = "fact_evidence_links"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    fact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facts.id"), nullable=False
    )
    evidence_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    evidence_item_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_span_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_spans.id")
    )
    link_role: Mapped[str] = mapped_column(String(32), nullable=False, default="PROVES")
    explanation: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["evidence_item_id", "evidence_item_version"],
            ["evidence_items.id", "evidence_items.version"],
            name="fk_fact_links_evidence",
        ),
        CheckConstraint(
            "link_role IN ('PROVES','CORROBORATES','CONTEXT')",
            name="ck_fact_link_role",
        ),
        CheckConstraint("status IN ('ACTIVE','STALE','VOID')", name="ck_fact_link_status"),
        Index("ix_fact_links_evidence", "evidence_item_id"),
        Index("ix_fact_links_fact", "fact_id"),
    )


class TimelineEvent(Base):
    __tablename__ = "timeline_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id"), nullable=False
    )
    event_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    time_precision: Mapped[str | None] = mapped_column(String(32))
    description: Mapped[str] = mapped_column(Text, nullable=False)
    layer: Mapped[str] = mapped_column(String(32), nullable=False, default="CANDIDATE")
    fact_key: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    evidence_item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    related_fact_ids: Mapped[list[Any] | None] = mapped_column(JSONB)
    analyst_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    stale_reason: Mapped[str | None] = mapped_column(String(64))
    stale_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "layer IN ('CANDIDATE','CONFIRMED','REJECTED','SUPERSEDED')",
            name="ck_timeline_layer",
        ),
        Index("ix_timeline_case", "case_id"),
    )


class Issue(Base):
    __tablename__ = "issues"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id"), nullable=False
    )
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    layer: Mapped[str] = mapped_column(String(32), nullable=False, default="CANDIDATE")
    related_fact_ids: Mapped[list[Any] | None] = mapped_column(JSONB)
    analyst_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    stale_reason: Mapped[str | None] = mapped_column(String(64))
    stale_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "layer IN ('CANDIDATE','CONFIRMED','REJECTED','SUPERSEDED')",
            name="ck_issues_layer",
        ),
        Index("ix_issues_case", "case_id"),
    )


class LegalTheory(Base):
    __tablename__ = "legal_theories"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id"), nullable=False
    )
    theory_summary: Mapped[str] = mapped_column(Text, nullable=False)
    norms_suggested_json: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSONB)
    supporting_fact_ids: Mapped[list[Any] | None] = mapped_column(JSONB)
    layer: Mapped[str] = mapped_column(String(32), nullable=False, default="CANDIDATE")
    analyst_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    stale_reason: Mapped[str | None] = mapped_column(String(64))
    stale_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "layer IN ('CANDIDATE','CONFIRMED','REJECTED','SUPERSEDED')",
            name="ck_legal_theories_layer",
        ),
        Index("ix_legal_theories_case", "case_id"),
    )


class ClaimDirection(Base):
    __tablename__ = "claim_directions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    claim_direction_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id"), nullable=False
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="CANDIDATE")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("claim_directions.id")
    )
    confirm_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("human_decisions.id", use_alter=True, name="fk_claim_directions_decision"),
    )
    stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    stale_reason: Mapped[str | None] = mapped_column(String(64))
    stale_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("claim_direction_key", "version", name="uq_claim_directions_key_version"),
        CheckConstraint(
            "status IN ('CANDIDATE','CONFIRMED','SUPERSEDED','REJECTED')",
            name="ck_claim_directions_status",
        ),
        Index("ix_claim_directions_case_current", "case_id", "is_current"),
        Index(
            "uq_claim_directions_current",
            "claim_direction_key",
            unique=True,
            postgresql_where=text("is_current = true"),
        ),
    )
