from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import get_settings

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    app: str
    env: str


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(status="ok", app=settings.app_name, env=settings.app_env)


@router.get("/ready", response_model=HealthResponse)
async def ready() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(status="ready", app=settings.app_name, env=settings.app_env)
