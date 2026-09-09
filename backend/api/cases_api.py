"""JSON APIs for lawyer MVP: cases, workspace, material upload, party create."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from backend.application.material_upload import MaterialUploadService
from backend.application.party_management import PartyManagementService
from backend.application.workspace import WorkspaceQueryService
from backend.domain.errors import ConflictError, DomainError, NotFoundError, ValidationError
from backend.domain.services import DomainService
from backend.infrastructure.config import get_settings
from backend.infrastructure.db import get_db_session
from backend.models import Case

router = APIRouter(prefix="/api/cases", tags=["cases-api"])

DEMO_OWNER = UUID("00000000-0000-4000-8000-000000000001")


class CreateCaseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=500)
    goal_summary: str | None = Field(default=None, max_length=4000)
    owner_user_id: UUID | None = None


class CreatePartyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str
    name: str
    party_type: str | None = None
    address: str | None = None
    legal_representative: str | None = None
    credit_code: str | None = None
    contact: str | None = None
    # Forbidden confirm flags — rejected explicitly in Application if present
    status: str | None = None
    confirmed: bool | None = None
    layer: str | None = None


@router.get("")
def api_list_cases(
    session: Session = Depends(get_db_session),  # noqa: B008
) -> dict[str, Any]:
    items = WorkspaceQueryService(session).list_cases()
    return {"cases": items}


@router.post("")
def api_create_case(
    body: CreateCaseBody,
    session: Session = Depends(get_db_session),  # noqa: B008
) -> dict[str, Any]:
    owner = body.owner_user_id or DEMO_OWNER
    case = DomainService(session).create_case(
        title=body.title.strip(),
        owner_user_id=owner,
        goal_summary=(body.goal_summary or "").strip() or None,
        actor_id=owner,
    )
    return {"id": str(case.id), "title": case.title}


@router.get("/{case_id}/workspace")
def api_workspace(
    case_id: UUID,
    session: Session = Depends(get_db_session),  # noqa: B008
) -> dict[str, Any]:
    try:
        return WorkspaceQueryService(session).get_workspace(case_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{case_id}/parties")
def api_create_party(
    case_id: UUID,
    body: CreatePartyBody,
    session: Session = Depends(get_db_session),  # noqa: B008
) -> dict[str, Any]:
    case = session.get(Case, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    try:
        dto = PartyManagementService(session).create_party_candidate(
            case_id=case_id,
            role=body.role,
            name=body.name,
            actor_id=case.owner_user_id,
            party_type=body.party_type,
            address=body.address,
            legal_representative=body.legal_representative,
            credit_code=body.credit_code,
            contact=body.contact,
            status=body.status,
            confirmed=body.confirmed,
            layer=body.layer,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    except DomainError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    return {
        "party_key": str(dto.party_key),
        "role": dto.role,
        "name": dto.name,
        "party_type": dto.party_type,
        "layer": dto.layer,
        "version": dto.version,
        "status_label": dto.to_dict()["status_label"],
        "role_label": dto.to_dict()["role_label"],
    }


@router.post("/{case_id}/materials")
async def api_upload_material(
    case_id: UUID,
    file: UploadFile = File(...),  # noqa: B008
    actor_id: UUID | None = Form(default=None),  # noqa: B008
    session: Session = Depends(get_db_session),  # noqa: B008
) -> dict[str, Any]:
    case = session.get(Case, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    raw = await file.read()
    actor = actor_id or case.owner_user_id
    try:
        result = MaterialUploadService(session).upload(
            case_id=case_id,
            filename=file.filename or "upload.bin",
            data=raw,
            actor_id=actor,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    except DomainError as exc:
        raise HTTPException(status_code=400, detail=getattr(exc, "message", str(exc))) from exc
    return {
        "material_id": str(result.material.id),
        "filename": result.material.filename,
        "parse_status": result.material.parse_status,
        "extraction_status": result.extraction_status,
        "extraction_error": result.extraction_error,
        "success": result.success,
        "max_material_bytes": get_settings().max_material_bytes,
    }
