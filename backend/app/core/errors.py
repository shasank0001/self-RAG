import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


class AppError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        super().__init__(message)


class UnauthorizedError(AppError):
    def __init__(
        self,
        code: str = "unauthorized",
        message: str = "Authentication required",
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(status_code=401, code=code, message=message, details=details)


class ForbiddenError(AppError):
    def __init__(
        self,
        code: str = "forbidden",
        message: str = "Forbidden",
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(status_code=403, code=code, message=message, details=details)


class NotFoundError(AppError):
    def __init__(
        self,
        code: str = "not_found",
        message: str = "Resource not found",
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(status_code=404, code=code, message=message, details=details)


class BadRequestError(AppError):
    def __init__(
        self,
        code: str = "bad_request",
        message: str = "Bad request",
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(status_code=400, code=code, message=message, details=details)


class UpstreamError(AppError):
    def __init__(
        self,
        code: str = "upstream_error",
        message: str = "Upstream provider failure",
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(status_code=502, code=code, message=message, details=details)


class WebhookSignatureError(AppError):
    def __init__(self, message: str = "Invalid webhook signature") -> None:
        super().__init__(status_code=400, code="webhook_invalid_signature", message=message)


def _error_payload(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    return ErrorResponse(error=ErrorDetail(code=code, message=message, details=details)).model_dump(
        mode="json",
        exclude_none=True,
    )


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "-")
    logger.warning(
        "request failed with app error",
        extra={
            "request_id": request_id,
            "error_code": exc.code,
            "status_code": exc.status_code,
            "path": str(request.url.path),
        },
    )
    headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_payload(exc.code, exc.message, exc.details),
        headers=headers,
    )


async def validation_error_handler(request: Request, _: RequestValidationError) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "-")
    logger.info("request validation failed", extra={"request_id": request_id, "path": str(request.url.path)})
    return JSONResponse(
        status_code=422,
        content=_error_payload("validation_error", "Request validation failed"),
    )


async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict) and "error" in detail:
        return JSONResponse(status_code=exc.status_code, content=detail, headers=exc.headers)

    code = "http_error"
    message = str(detail)
    if exc.status_code == 413:
        code = "request_too_large"
        message = "Request body exceeds configured limit"
    return JSONResponse(status_code=exc.status_code, content=_error_payload(code, message), headers=exc.headers)


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(HTTPException, http_exception_handler)  # type: ignore[arg-type]
