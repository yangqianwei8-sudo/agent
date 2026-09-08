"""ORM models — Decisions, Commands, Messages, Drafts, Audit."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base


class HumanDecision(Base):
    __tablename__ = "human_decisions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id"), nullable=False
    )
    instance_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflow_instances.id", use_alter=True, name="fk_human_decisions_instance"),
    )
    decision_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    question: Mapped[str | None] = mapped_column(Text)
    options_json: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSONB)
    input_payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    result: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "result IN ('CONFIRMED','REJECTED','SELECTED','AMENDED')",
            name="ck_human_decisions_result",
        ),
        Index("ix_human_decisions_case", "case_id", "created_at"),
    )


class SystemCommand(Base):
    __tablename__ = "system_commands"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id"), nullable=False
    )
    instance_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflow_instances.id", use_alter=True, name="fk_system_commands_instance"),
    )
    command_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACCEPTED")
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('ACCEPTED','REJECTED','DONE')",
            name="ck_system_commands_status",
        ),
        Index("ix_system_commands_case", "case_id", "created_at"),
    )


class AgentMessage(Base):
    __tablename__ = "agent_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id"), nullable=False
    )
    instance_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflow_instances.id", use_alter=True, name="fk_agent_messages_instance"),
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    parsed_intent_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    related_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("human_decisions.id")
    )
    related_command_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("system_commands.id")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("role IN ('USER','AGENT','SYSTEM')", name="ck_agent_messages_role"),
        Index("ix_agent_messages_case", "case_id", "created_at"),
    )


class DocumentDraft(Base):
    __tablename__ = "document_drafts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id"), nullable=False
    )
    doc_type: Mapped[str] = mapped_column(String(64), nullable=False, default="COMPLAINT")
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT")
    body_structured_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    based_on_confirmation_set_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    writer_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("node_runs.id")
    )
    stale_reason: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("case_id", "doc_type", "version", name="uq_document_drafts_version"),
        CheckConstraint(
            "status IN ('DRAFT','IN_REVIEW','APPROVED_BY_LAWYER','STALE')",
            name="ck_document_drafts_status",
        ),
        Index("ix_document_drafts_case", "case_id", "doc_type"),
    )


class DraftCitation(Base):
    __tablename__ = "draft_citations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    draft_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_drafts.id"), nullable=False
    )
    block_id: Mapped[str] = mapped_column(String(100), nullable=False)
    citation_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    fact_key: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    fact_version: Mapped[int | None] = mapped_column(Integer)
    evidence_item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    evidence_item_version: Mapped[int | None] = mapped_column(Integer)
    requires_lawyer_confirm: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "(fact_key IS NOT NULL) OR (evidence_item_id IS NOT NULL) OR "
            "(citation_kind = 'ANNOTATION')",
            name="ck_draft_citations_target",
        ),
        Index("ix_draft_citations_draft", "draft_id"),
        Index("ix_draft_citations_fact", "fact_key", "fact_version"),
        Index("ix_draft_citations_evidence", "evidence_item_id", "evidence_item_version"),
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    actor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    case_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id")
    )
    before_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_audit_logs_case_created", "case_id", "created_at"),
        Index("ix_audit_logs_entity", "entity_type", "entity_id"),
    )
