"""FastAPI application entry — V1 + lawyer MVP UI + autonomous dev."""

from contextlib import asynccontextmanager
from pathlib import Path

from autonomous_dev.app import router as autonomous_dev_router
from autonomous_dev.config import get_autonomous_settings
from autonomous_dev.watchdog import start_watchdog, stop_watchdog
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from backend.api.router import api_router
from backend.infrastructure.config import get_settings

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if get_autonomous_settings().autonomous_dev_enabled:
        start_watchdog()
    yield
    stop_watchdog()


app = FastAPI(
    title=settings.app_name,
    version="0.3.0",
    description="诉讼案件工作 Agent — Lawyer MVP UI",
    lifespan=lifespan,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.include_router(api_router)
app.include_router(autonomous_dev_router)


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/cases", status_code=302)
