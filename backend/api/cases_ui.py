"""Jinja HTML routes for lawyer MVP UI."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from backend.api.cases_api import DEMO_OWNER
from backend.application.workspace import NODE_LABELS_ZH, WorkspaceQueryService
from backend.domain.services import DomainService
from backend.infrastructure.db import get_db_session
from backend.models import Case

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(tags=["cases-ui"])


@router.get("/cases", response_class=HTMLResponse)
def cases_list(
    request: Request,
    session: Session = Depends(get_db_session),  # noqa: B008
) -> HTMLResponse:
    items = WorkspaceQueryService(session).list_cases()
    return templates.TemplateResponse(
        request,
        "cases/list.html",
        {"cases": items, "title": "案件列表"},
    )


@router.get("/cases/new", response_class=HTMLResponse)
def cases_new(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "cases/create.html",
        {"title": "创建案件"},
    )


@router.post("/cases/new")
def cases_create(
    title: str = Form(...),  # noqa: B008
    goal_summary: str = Form(""),  # noqa: B008
    session: Session = Depends(get_db_session),  # noqa: B008
) -> RedirectResponse:
    title = title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="案件名称不能为空")
    case = DomainService(session).create_case(
        title=title,
        owner_user_id=DEMO_OWNER,
        goal_summary=goal_summary.strip() or None,
        actor_id=DEMO_OWNER,
    )
    return RedirectResponse(url=f"/cases/{case.id}", status_code=303)


@router.get("/cases/{case_id}", response_class=HTMLResponse)
def case_workspace(
    case_id: UUID,
    request: Request,
    session: Session = Depends(get_db_session),  # noqa: B008
) -> HTMLResponse:
    case = session.get(Case, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    try:
        data = WorkspaceQueryService(session).get_workspace(case_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    boot = json.dumps(data, ensure_ascii=False)
    return templates.TemplateResponse(
        request,
        "cases/workspace.html",
        {
            "title": case.title,
            "case_id": str(case_id),
            "workspace": data,
            "workspace_json": boot,
            "node_labels": NODE_LABELS_ZH,
        },
    )
