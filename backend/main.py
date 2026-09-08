"""FastAPI application entry (Phase 1: health check only)."""

from fastapi import FastAPI

from backend.api.router import api_router
from backend.infrastructure.config import get_settings

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="诉讼案件工作 Agent V1 — Phase 1 engineering skeleton",
)

app.include_router(api_router)


@app.get("/")
def root() -> dict[str, str]:
    return {
        "service": settings.app_name,
        "phase": "1",
        "status": "ok",
    }
