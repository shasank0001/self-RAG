from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.middleware.rate_limit import RateLimitMiddleware
from app.api.middleware.request_context import RequestContextMiddleware
from app.api.dependencies.security import BodySizeLimitMiddleware
from app.api.v1.routes.auth import router as auth_router
from app.api.v1.routes.bins import router as bins_router
from app.api.v1.routes.chat import router as chat_router
from app.api.v1.routes.health import router as health_router
from app.api.v1.routes.ingestion import router as ingestion_router
from app.api.v1.routes.messages import router as messages_router
from app.api.v1.routes.observability import router as observability_router
from app.api.v1.routes.sessions import router as sessions_router
from app.api.v1.routes.webhooks import router as webhooks_router
from app.core.config import Settings, get_settings
from app.core.errors import register_error_handlers
from app.core.logging import setup_logging
from app.core.tracing import setup_tracing
from app.observability.metrics import get_metrics_router
from app.services.ingestion.worker import recover_ingestion_worker, schedule_ingestion_worker, shutdown_ingestion_worker


@asynccontextmanager
async def lifespan(_: FastAPI):
    await recover_ingestion_worker()
    await schedule_ingestion_worker()
    try:
        yield
    finally:
        await shutdown_ingestion_worker()


def _resolve_cors_origins(settings: Settings) -> list[str]:
    configured = [origin.strip() for origin in settings.cors_allowed_origins.split(",") if origin.strip()]
    if configured:
        return configured

    # Local development defaults for Vite and localhost aliases.
    return [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
        "http://localhost:5175",
        "http://127.0.0.1:5175",
    ]


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings)

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.add_middleware(BodySizeLimitMiddleware, settings=settings)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(RateLimitMiddleware, settings=settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_resolve_cors_origins(settings),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_error_handlers(app)
    setup_tracing(app, settings)

    api_v1 = APIRouter(prefix="/api/v1")
    api_v1.include_router(health_router)
    api_v1.include_router(auth_router)
    api_v1.include_router(bins_router)
    api_v1.include_router(ingestion_router)
    api_v1.include_router(sessions_router)
    api_v1.include_router(messages_router)
    api_v1.include_router(chat_router)
    api_v1.include_router(observability_router)
    app.include_router(api_v1)
    app.include_router(webhooks_router)
    if settings.prometheus_enabled:
        app.include_router(get_metrics_router())

    return app


app = create_app()
