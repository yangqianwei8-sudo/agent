"""FastAPI routes for autonomous dev infrastructure."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse

from autonomous_dev.config import get_autonomous_settings
from autonomous_dev.dashboard import DASHBOARD_HTML, build_dashboard_payload
from autonomous_dev.github_client import GitHubClientError
from autonomous_dev.github_webhook import (
    WebhookVerificationError,
    parse_json_payload,
    verify_github_signature,
)
from autonomous_dev.state import DeliveryStatus, StateStore
from autonomous_dev.task_router import TaskRouter

logger = logging.getLogger(__name__)

router = APIRouter(tags=["autonomous-dev"])

_store: StateStore | None = None
_router: TaskRouter | None = None


def _get_store() -> StateStore:
    global _store
    if _store is None:
        settings = get_autonomous_settings()
        _store = StateStore(settings.state_db_path)
    return _store


def _get_router() -> TaskRouter:
    global _router
    if _router is None:
        settings = get_autonomous_settings()
        _router = TaskRouter(settings, _get_store())
    return _router


def reset_autonomous_singletons() -> None:
    global _store, _router
    from autonomous_dev.review_worker import reset_review_worker_singleton

    _store = None
    _router = None
    reset_review_worker_singleton()


@router.get("/healthz")
def healthz() -> dict[str, str]:
    settings = get_autonomous_settings()
    store = _get_store()
    reviewer_creds = settings.resolve_reviewer_credentials()
    return {
        "status": "ok",
        "autonomous_dev_enabled": str(settings.autonomous_dev_enabled).lower(),
        "worker_mode": settings.autonomous_worker_mode,
        "worker_locked": str(store.is_locked()).lower(),
        "reviewer_configured": str(reviewer_creds is not None).lower(),
        "reviewer_locked": str(store.is_reviewer_locked()).lower(),
    }


@router.get("/autonomous/status.json")
def autonomous_status_json() -> JSONResponse:
    settings = get_autonomous_settings()
    payload = build_dashboard_payload(settings, _get_store())
    return JSONResponse(content=payload)


@router.get("/autonomous/status", response_class=HTMLResponse)
def autonomous_status_page() -> Response:
    return HTMLResponse(content=DASHBOARD_HTML, headers={"Cache-Control": "no-store"})


@router.post("/webhooks/github")
async def github_webhook(
    request: Request,
    x_github_event: str | None = Header(default=None, alias="X-GitHub-Event"),
    x_github_delivery: str | None = Header(default=None, alias="X-GitHub-Delivery"),
    x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
) -> dict[str, object]:
    settings = get_autonomous_settings()
    if not settings.autonomous_dev_enabled:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="autonomous dev disabled")

    body = await request.body()
    try:
        verify_github_signature(body, x_hub_signature_256, settings.github_webhook_secret)
    except WebhookVerificationError as exc:
        logger.warning("webhook rejected: %s", exc)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    if not x_github_delivery:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="missing X-GitHub-Delivery")

    payload = parse_json_payload(body)
    store = _get_store()

    if store.delivery_exists(x_github_delivery):
        logger.info("duplicate delivery ignored: %s", x_github_delivery)
        return {"status": "duplicate", "delivery_id": x_github_delivery}

    action = payload.get("action")
    event_type = x_github_event or "unknown"
    store.record_delivery(
        delivery_id=x_github_delivery,
        event_type=event_type,
        action=action if isinstance(action, str) else None,
        payload=payload,
        status=DeliveryStatus.RECEIVED,
    )

    result = _get_router().handle(
        event_type=event_type,
        action=action if isinstance(action, str) else None,
        delivery_id=x_github_delivery,
        payload=payload,
    )
    logger.info(
        "webhook handled delivery=%s event=%s action=%s result=%s",
        x_github_delivery,
        event_type,
        action,
        result.get("status"),
    )
    return {"delivery_id": x_github_delivery, **result}


@router.post("/autonomous/review/pass")
async def review_pass(
    request: Request,
    x_autonomous_secret: str | None = Header(default=None, alias="X-Autonomous-Secret"),
) -> dict[str, object]:
    """Reviewer PASS — transitions ready-for-review → completed (not worker-initiated)."""
    settings = get_autonomous_settings()
    if not settings.autonomous_dev_enabled:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="autonomous dev disabled")
    if x_autonomous_secret != settings.github_webhook_secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid secret")

    body = await request.json()
    issue_number = body.get("issue_number")
    if not isinstance(issue_number, int):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="issue_number required")

    try:
        result = _get_router().seal_review_pass(issue_number)
    except (GitHubClientError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return result
