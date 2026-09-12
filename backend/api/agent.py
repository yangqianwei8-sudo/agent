"""Minimal Case Agent HTTP API (Phase 9)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from backend.agent.case_agent import CaseAgent
from backend.agent.dto import AgentResponse
from backend.infrastructure.db import get_db_session
from backend.models import Case

router = APIRouter(prefix="/cases", tags=["agent"])


class AgentMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: UUID | None = None
    message: str = Field(min_length=1, max_length=8000)
    actor_id: UUID | None = None
    current_issue_key: UUID | None = None
    current_issue_version: int | None = None
    current_object_type: str | None = Field(default=None, max_length=64)
    current_object_ref: str | None = Field(default=None, max_length=128)


@router.post("/{case_id}/agent/messages", response_model=AgentResponse)
def post_agent_message(
    case_id: UUID,
    body: AgentMessageRequest,
    session: Session = Depends(get_db_session),  # noqa: B008
) -> AgentResponse:
    case = session.get(Case, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    actor_id = body.actor_id or case.owner_user_id
    agent = CaseAgent(session, actor_id=actor_id)
    try:
        resp = agent.handle_message(
            case_id,
            body.message,
            conversation_id=body.conversation_id,
            current_issue_key=body.current_issue_key,
            current_issue_version=body.current_issue_version,
            current_object_type=body.current_object_type,
            current_object_ref=body.current_object_ref,
        )
        return resp
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/{case_id}/agent/status", response_model=AgentResponse)
def get_agent_status(
    case_id: UUID,
    conversation_id: UUID | None = None,
    actor_id: UUID | None = None,
    session: Session = Depends(get_db_session),  # noqa: B008
) -> AgentResponse:
    case = session.get(Case, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    agent = CaseAgent(session, actor_id=actor_id or case.owner_user_id)
    return agent.status(case_id, conversation_id=conversation_id)
