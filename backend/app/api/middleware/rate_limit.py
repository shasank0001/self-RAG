from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from app.core.config import Settings, get_settings


@dataclass(slots=True)
class _Bucket:
    tokens: float
    updated_at: float


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, settings: Settings | None = None) -> None:
        super().__init__(app)
        self._settings = settings or get_settings()
        self._buckets: dict[str, _Bucket] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _client_key(request: Request) -> str:
        client_host = request.client.host if request.client else "unknown"
        return f"{client_host}:{request.url.path}"

    async def _allow(self, key: str) -> tuple[bool, float]:
        now = time.monotonic()
        refill_rate = max(self._settings.rate_limit_rps, 0.001)
        burst = max(self._settings.rate_limit_burst, 1)

        async with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(tokens=float(burst), updated_at=now)
                self._buckets[key] = bucket

            elapsed = max(now - bucket.updated_at, 0.0)
            bucket.tokens = min(float(burst), bucket.tokens + elapsed * refill_rate)
            bucket.updated_at = now

            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return True, 0.0

            retry_after = (1.0 - bucket.tokens) / refill_rate
            return False, max(retry_after, 0.001)

    async def dispatch(self, request: Request, call_next) -> Response:
        if not self._settings.rate_limit_enabled:
            return await call_next(request)

        if request.url.path in {"/api/v1/health", "/api/v1/ready", "/metrics"}:
            return await call_next(request)

        allowed, retry_after = await self._allow(self._client_key(request))
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "rate_limit_exceeded",
                        "message": "Request rate limit exceeded",
                        "details": {"retry_after_seconds": round(retry_after, 3)},
                    }
                },
                headers={"Retry-After": str(max(int(retry_after), 1))},
            )

        return await call_next(request)
