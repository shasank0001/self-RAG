from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from app.core.config import Settings, get_settings


def enforce_request_size_limit(request: Request, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    limit = max(settings.request_body_limit_bytes, 1)
    content_length = request.headers.get("content-length")
    if content_length is None:
        return

    try:
        size = int(content_length)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "invalid_content_length", "message": "Invalid Content-Length header"}},
        ) from exc

    if size > limit:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "error": {
                    "code": "request_too_large",
                    "message": "Request body exceeds configured limit",
                    "details": {"limit_bytes": limit},
                }
            },
        )


def guard_text_payload(*, text: str, field_name: str, max_chars: int = 12000) -> None:
    if len(text) > max_chars:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "error": {
                    "code": "text_too_large",
                    "message": f"Field '{field_name}' exceeds max length",
                    "details": {"field": field_name, "max_chars": max_chars},
                }
            },
        )


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, settings: Settings | None = None) -> None:
        super().__init__(app)
        self._settings = settings or get_settings()

    async def dispatch(self, request: Request, call_next) -> Response:
        limit = max(self._settings.request_body_limit_bytes, 1)
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                size = int(content_length)
            except ValueError:
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={"error": {"code": "invalid_content_length", "message": "Invalid Content-Length header"}},
                )
            if size > limit:
                return JSONResponse(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    content={
                        "error": {
                            "code": "request_too_large",
                            "message": "Request body exceeds configured limit",
                            "details": {"limit_bytes": limit},
                        }
                    },
                )

        body = await request.body()
        if len(body) > limit:
            return JSONResponse(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                content={
                    "error": {
                        "code": "request_too_large",
                        "message": "Request body exceeds configured limit",
                        "details": {"limit_bytes": limit},
                    }
                },
            )

        return await call_next(request)


def dependency_guard_payload(request: Request) -> dict[str, Any]:
    enforce_request_size_limit(request)
    return {}
