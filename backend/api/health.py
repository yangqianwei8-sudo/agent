"""Health check endpoints (Phase 1 acceptance)."""

from fastapi import APIRouter

from backend.infrastructure.config import get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    settings = get_settings()
    return {
        "status": "ok",
        "service": settings.app_name,
        "phase": "1",
        "environment": settings.app_env,
    }
