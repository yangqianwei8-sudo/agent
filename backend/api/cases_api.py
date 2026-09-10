"""JSON APIs for lawyer MVP: cases, workspace, material upload, party create."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from backend.agent.dto import AgentResponse
from backend.application.case_actions import CaseActionService
from backend.application.issue_matrix import IssueMatrixService
from backend.application.material_management import MaterialManagementService
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


class VoidMaterialBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=500)


class CaseActionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: str = Field(min_length=1, max_length=64)
    target: str | None = Field(default=None, max_length=64)
    conversation_id: UUID | None = None
    actor_id: UUID | None = None


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


@router.post("/{case_id}/actions", response_model=AgentResponse)
def api_case_action(
    case_id: UUID,
    body: CaseActionBody,
    session: Session = Depends(get_db_session),  # noqa: B008
) -> AgentResponse:
    case = session.get(Case, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    actor = body.actor_id or case.owner_user_id
    svc = CaseActionService(session, actor_id=actor)
    try:
        return svc.execute(
            case_id,
            action_type=body.action_type,
            target=body.target,
            conversation_id=body.conversation_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


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


@router.get("/{case_id}/issue-matrix")
def api_issue_matrix(
    case_id: UUID,
    session: Session = Depends(get_db_session),  # noqa: B008
) -> dict[str, Any]:
    case = session.get(Case, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    try:
        view = IssueMatrixService(session).build(case_id)
        return view.model_dump(mode="json")
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc


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
        "usable": result.usable,
        "in_material_pool": result.usable,
        "message": result.message,
        "needs_ocr": result.needs_ocr,
        "max_material_bytes": get_settings().max_material_bytes,
    }


@router.post("/{case_id}/materials/{material_id}/void")
def api_void_material(
    case_id: UUID,
    material_id: UUID,
    body: VoidMaterialBody | None = None,
    session: Session = Depends(get_db_session),  # noqa: B008
) -> dict[str, Any]:
    case = session.get(Case, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    payload = body or VoidMaterialBody()
    try:
        material = MaterialManagementService(session).void_material(
            case_id=case_id,
            material_id=material_id,
            actor_id=case.owner_user_id,
            reason=payload.reason,
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
        "material_id": str(material.id),
        "filename": material.filename,
        "life_status": material.life_status,
        "void_reason": material.void_reason,
    }


@router.post("/{case_id}/void-pool/add-pending")
def api_void_pool_add_pending(
    case_id: UUID,
    body: VoidMaterialBody | None = None,
    session: Session = Depends(get_db_session),  # noqa: B008
) -> dict[str, Any]:
    """增加：将全部「待处理」材料移入作废池。"""
    case = session.get(Case, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    payload = body or VoidMaterialBody()
    try:
        result = MaterialManagementService(session).void_all_pending(
            case_id=case_id,
            actor_id=case.owner_user_id,
            reason=payload.reason,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    except DomainError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    return {
        "count": result.count,
        "material_ids": [str(i) for i in result.affected_ids],
    }


@router.post("/{case_id}/void-pool/clear")
def api_void_pool_clear(
    case_id: UUID,
    session: Session = Depends(get_db_session),  # noqa: B008
) -> dict[str, Any]:
    """清空：从作废池列表中清除（软隐藏，不物理删文件）。"""
    case = session.get(Case, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    try:
        result = MaterialManagementService(session).clear_void_pool(
            case_id=case_id,
            actor_id=case.owner_user_id,
        )
    except DomainError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    return {
        "count": result.count,
        "material_ids": [str(i) for i in result.affected_ids],
    }


@router.post("/{case_id}/materials/{material_id}/reparse")
def api_reparse_material(
    case_id: UUID,
    material_id: UUID,
    session: Session = Depends(get_db_session),  # noqa: B008
) -> dict[str, Any]:
    case = session.get(Case, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    try:
        result = MaterialManagementService(session).reparse_material(
            case_id=case_id,
            material_id=material_id,
            actor_id=case.owner_user_id,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    except DomainError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    ec = result.outcome.extracted_content
    if result.usable:
        message = "重新解析成功，文件已进入案件材料。"
    else:
        reason = getattr(ec, "error_detail", None) if ec else None
        message = (
            "重新解析仍失败，文件继续留在待处理文件中。"
            + (f" 原因：{reason}" if reason else "")
        )
        if result.outcome.needs_ocr:
            message += " 扫描件可走 CamScanner 转 Markdown，或上传可读取版本。"
    return {
        "material_id": str(result.material.id),
        "filename": result.material.filename,
        "parse_status": result.material.parse_status,
        "extraction_status": ec.status if ec else None,
        "extraction_error": getattr(ec, "error_detail", None) if ec else None,
        "extracted_content_id": str(ec.id) if ec else None,
        "previous_extracted_content_id": (
            str(result.previous_extracted_content_id)
            if result.previous_extracted_content_id
            else None
        ),
        "success": result.outcome.success,
        "usable": result.usable,
        "in_material_pool": result.usable,
        "needs_ocr": result.outcome.needs_ocr,
        "message": message,
    }
