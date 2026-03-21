from __future__ import annotations

import logging
import time
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any
from uuid import uuid4

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.observability.events import LogEvent

REQUEST_CONTEXT_KEYS = (
    "request_id",
    "chat_id",
    "job_id",
    "bin_id",
    "provider",
    "model",
    "fallback_attempt",
)

_REQUEST_CONTEXT: ContextVar[dict[str, Any]] = ContextVar("request_context", default={})
logger = logging.getLogger(__name__)


def _normalize_context(raw_context: dict[str, Any] | None) -> dict[str, Any]:
    normalized = {key: None for key in REQUEST_CONTEXT_KEYS}
    if not raw_context:
        return normalized

    for key in REQUEST_CONTEXT_KEYS:
        normalized[key] = raw_context.get(key)
    return normalized


def get_request_context() -> dict[str, Any]:
    return _normalize_context(_REQUEST_CONTEXT.get())


def set_request_context(context: dict[str, Any]) -> Token[dict[str, Any]]:
    return _REQUEST_CONTEXT.set(_normalize_context(context))


def update_request_context(**updates: Any) -> None:
    current = get_request_context()
    for key, value in updates.items():
        if key in REQUEST_CONTEXT_KEYS and value is not None:
            current[key] = value
    _REQUEST_CONTEXT.set(current)


@contextmanager
def bind_request_context(**updates: Any):
    current = get_request_context()
    next_context = {**current}
    for key, value in updates.items():
        if key in REQUEST_CONTEXT_KEYS:
            next_context[key] = value
    token = _REQUEST_CONTEXT.set(next_context)
    try:
        yield
    finally:
        _REQUEST_CONTEXT.reset(token)


def _context_from_request(request: Request) -> dict[str, Any]:
    path_params = request.path_params
    context: dict[str, Any] = {
        "request_id": request.headers.get("x-request-id") or str(uuid4()),
        "chat_id": request.headers.get("x-chat-id") or path_params.get("session_id"),
        "job_id": request.headers.get("x-job-id") or path_params.get("job_id"),
        "bin_id": request.headers.get("x-bin-id") or path_params.get("bin_id"),
        "provider": request.headers.get("x-provider"),
        "model": request.headers.get("x-model"),
        "fallback_attempt": request.headers.get("x-fallback-attempt"),
    }
    return _normalize_context(context)


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable[[Request], Any]) -> Response:
        context = _context_from_request(request)
        token = set_request_context(context)
        request.state.request_id = context["request_id"]
        request.state.chat_id = context["chat_id"]
        request.state.job_id = context["job_id"]
        request.state.bin_id = context["bin_id"]
        logger.info(
            LogEvent.API_REQUEST_STARTED,
            extra={"method": request.method, "path": request.url.path},
        )

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:  # noqa: BLE001
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.exception(
                LogEvent.API_REQUEST_FAILED,
                extra={"method": request.method, "path": request.url.path, "duration_ms": duration_ms},
            )
            _REQUEST_CONTEXT.reset(token)
            raise

        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        logger.info(
            LogEvent.API_REQUEST_COMPLETED,
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        _REQUEST_CONTEXT.reset(token)
        response.headers["x-request-id"] = str(context["request_id"])
        response.headers["x-response-time-ms"] = str(duration_ms)
        return response
