"""FastAPI application entry — V1 + lawyer MVP UI."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from backend.api.router import api_router
from backend.infrastructure.config import get_settings

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version="0.3.0",
    description="诉讼案件工作 Agent — Lawyer MVP UI",
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.include_router(api_router)


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/cases", status_code=302)
