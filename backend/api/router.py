"""Aggregate API routers."""

from fastapi import APIRouter

from backend.api.agent import router as agent_router
from backend.api.cases_api import router as cases_api_router
from backend.api.cases_ui import router as cases_ui_router
from backend.api.health import router as health_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(agent_router)
api_router.include_router(cases_api_router)
api_router.include_router(cases_ui_router)
