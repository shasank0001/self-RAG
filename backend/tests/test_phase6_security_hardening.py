from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies.security import BodySizeLimitMiddleware
from app.api.middleware.rate_limit import RateLimitMiddleware
from app.core.config import Settings


def _build_app(*, request_body_limit_bytes: int, rate_limit_rps: float, rate_limit_burst: int) -> FastAPI:
    settings = Settings(
        REQUEST_BODY_LIMIT_BYTES=request_body_limit_bytes,
        RATE_LIMIT_ENABLED=True,
        RATE_LIMIT_RPS=rate_limit_rps,
        RATE_LIMIT_BURST=rate_limit_burst,
    )
    app = FastAPI()
    app.add_middleware(BodySizeLimitMiddleware, settings=settings)
    app.add_middleware(RateLimitMiddleware, settings=settings)

    @app.post("/api/v1/chat/sessions/demo/message")
    async def post_message(payload: dict[str, str]) -> dict[str, str]:
        return payload

    @app.get("/api/v1/chat/stream")
    async def stream_probe() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def test_body_size_limit_blocks_oversized_payload() -> None:
    app = _build_app(request_body_limit_bytes=32, rate_limit_rps=100.0, rate_limit_burst=100)
    with TestClient(app) as client:
        response = client.post("/api/v1/chat/sessions/demo/message", json={"message": "x" * 200})

    assert response.status_code == 413
    payload = response.json()
    assert payload["error"]["code"] == "request_too_large"


def test_rate_limit_applies_to_chat_and_stream_routes() -> None:
    app = _build_app(request_body_limit_bytes=1024 * 1024, rate_limit_rps=0.001, rate_limit_burst=1)
    with TestClient(app) as client:
        first_chat = client.post("/api/v1/chat/sessions/demo/message", json={"message": "ok"})
        second_chat = client.post("/api/v1/chat/sessions/demo/message", json={"message": "ok"})

        first_stream = client.get("/api/v1/chat/stream")
        second_stream = client.get("/api/v1/chat/stream")

        health_1 = client.get("/api/v1/health")
        health_2 = client.get("/api/v1/health")

    assert first_chat.status_code == 200
    assert second_chat.status_code == 429
    assert second_chat.json()["error"]["code"] == "rate_limit_exceeded"
    assert first_stream.status_code == 200
    assert second_stream.status_code == 429
    assert health_1.status_code == 200
    assert health_2.status_code == 200
