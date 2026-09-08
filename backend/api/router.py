"""Aggregate API routers."""

from fastapi import APIRouter

from backend.api.agent import router as agent_router
from backend.api.health import router as health_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(agent_router)
