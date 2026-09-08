"""ORM models — Case, Party, Material, Extraction, Span."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.models.base import Base


class Case(Base):
    __tablename__ = "cases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    case_no_internal: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="OPEN")
    owner_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    goal_summary: Mapped[str | None] = mapped_column(Text)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("status IN ('OPEN','ARCHIVED')", name="ck_cases_status"),
        Index("ix_cases_owner_updated", "owner_user_id", "updated_at"),
    )


class CaseParty(Base):
    __tablename__ = "case_parties"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id"), nullable=False
    )
    party_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    party_type: Mapped[str] = mapped_column(String(32), nullable=False)
    identifiers_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    layer: Mapped[str] = mapped_column(String(32), nullable=False, default="CANDIDATE")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("case_parties.id")
    )
    confirm_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("human_decisions.id", use_alter=True, name="fk_case_parties_decision"),
    )
    stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    stale_reason: Mapped[str | None] = mapped_column(String(64))
    stale_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("party_key", "version", name="uq_case_parties_key_version"),
        CheckConstraint(
            "layer IN ('CANDIDATE','CONFIRMED','REJECTED','SUPERSEDED')",
            name="ck_case_parties_layer",
        ),
        CheckConstraint(
            "role IN ('PLAINTIFF','DEFENDANT','THIRD_PARTY','OTHER')",
            name="ck_case_parties_role",
        ),
        CheckConstraint("party_type IN ('ORG','PERSON')", name="ck_case_parties_type"),
        Index("ix_case_parties_case_layer", "case_id", "layer"),
        Index("ix_case_parties_party_key", "party_key"),
        Index(
            "uq_case_parties_current",
            "case_id",
            "party_key",
            unique=True,
            postgresql_where=text("is_current = true"),
        ),
    )


class CaseMaterial(Base):
    __tablename__ = "case_materials"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    mime: Mapped[str] = mapped_column(String(200), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1000), nullable=False)
    life_status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    parse_status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    void_reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    extracted_contents: Mapped[list[ExtractedContent]] = relationship(back_populates="material")

    __table_args__ = (
        CheckConstraint("life_status IN ('ACTIVE','VOID')", name="ck_materials_life"),
        CheckConstraint(
            "parse_status IN ('PENDING','RUNNING','SUCCEEDED','FAILED')",
            name="ck_materials_parse",
        ),
        Index("ix_materials_case_created", "case_id", "created_at"),
    )


class ExtractedContent(Base):
    __tablename__ = "extracted_contents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    material_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("case_materials.id"), nullable=False
    )
    extraction_method: Mapped[str] = mapped_column(String(100), nullable=False)
    extraction_version: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="SUCCEEDED")
    full_text_ref: Mapped[str | None] = mapped_column(String(1000))
    full_text: Mapped[str | None] = mapped_column(Text)
    page_count: Mapped[int | None] = mapped_column(Integer)
    layout_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    material: Mapped[CaseMaterial] = relationship(back_populates="extracted_contents")
    spans: Mapped[list[SourceSpan]] = relationship(back_populates="extracted_content")

    __table_args__ = (
        UniqueConstraint(
            "material_id",
            "extraction_method",
            "extraction_version",
            name="uq_extracted_contents_material_method_version",
        ),
        Index("ix_extracted_contents_material", "material_id", "created_at"),
        CheckConstraint(
            "status IN ('PENDING','RUNNING','SUCCEEDED','FAILED')",
            name="ck_extracted_contents_status",
        ),
    )


class SourceSpan(Base):
    __tablename__ = "source_spans"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    material_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("case_materials.id"), nullable=False
    )
    extracted_content_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("extracted_contents.id"), nullable=False
    )
    page: Mapped[int | None] = mapped_column(Integer)
    paragraph: Mapped[int | None] = mapped_column(Integer)
    character_start: Mapped[int] = mapped_column(Integer, nullable=False)
    character_end: Mapped[int] = mapped_column(Integer, nullable=False)
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    quote_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    bbox_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    extraction_method: Mapped[str] = mapped_column(String(100), nullable=False)
    extraction_version: Mapped[str] = mapped_column(String(50), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    extracted_content: Mapped[ExtractedContent] = relationship(back_populates="spans")

    __table_args__ = (
        CheckConstraint(
            "character_start >= 0 AND character_end > character_start",
            name="ck_source_spans_offsets",
        ),
        Index("ix_source_spans_ec_start", "extracted_content_id", "character_start"),
        Index("ix_source_spans_material_page", "material_id", "page"),
    )
