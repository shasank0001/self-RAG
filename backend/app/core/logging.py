from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from logging.config import dictConfig
from typing import Any

from app.api.middleware.request_context import REQUEST_CONTEXT_KEYS, get_request_context
from app.core.config import Settings, get_settings

_SENSITIVE_KEY_PATTERN = re.compile(
    r"(authorization|api[_-]?key|secret|token|password|prompt|document|raw_text)",
    re.IGNORECASE,
)
_SENSITIVE_BEARER_PATTERN = re.compile(r"bearer\s+[a-z0-9_\-\.]+", re.IGNORECASE)


def _is_sensitive_key(key: str) -> bool:
    return bool(_SENSITIVE_KEY_PATTERN.search(key))


def _sanitize(value: Any, *, key: str | None = None) -> Any:
    if key and _is_sensitive_key(key):
        return "[REDACTED]"

    if isinstance(value, dict):
        return {str(item_key): _sanitize(item_value, key=str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize(item) for item in value)
    if isinstance(value, str):
        if _SENSITIVE_BEARER_PATTERN.search(value):
            return _SENSITIVE_BEARER_PATTERN.sub("Bearer [REDACTED]", value)
        if len(value) > 500:
            return f"{value[:250]}...[truncated]"
    return value


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in list(record.__dict__.items()):
            if key in {"msg", "args"}:
                continue
            record.__dict__[key] = _sanitize(value, key=key)

        if isinstance(record.msg, dict):
            record.msg = _sanitize(record.msg)
        elif isinstance(record.msg, str):
            record.msg = _sanitize(record.msg)

        if record.args:
            record.args = _sanitize(record.args)
        return True


class RequestContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        context = get_request_context()
        for key in REQUEST_CONTEXT_KEYS:
            value = context.get(key)
            if getattr(record, key, None) in {None, ""}:
                setattr(record, key, value if value is not None else "-")
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
            "chat_id": getattr(record, "chat_id", "-"),
            "job_id": getattr(record, "job_id", "-"),
            "bin_id": getattr(record, "bin_id", "-"),
            "provider": getattr(record, "provider", "-"),
            "model": getattr(record, "model", "-"),
            "fallback_attempt": getattr(record, "fallback_attempt", "-"),
        }

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        for key, value in record.__dict__.items():
            if key in {
                "args",
                "created",
                "exc_info",
                "exc_text",
                "filename",
                "funcName",
                "levelname",
                "levelno",
                "lineno",
                "module",
                "msecs",
                "msg",
                "name",
                "pathname",
                "process",
                "processName",
                "relativeCreated",
                "stack_info",
                "thread",
                "threadName",
            }:
                continue
            if key in payload:
                continue
            payload[key] = value

        return json.dumps(_sanitize(payload), default=str)


def setup_logging(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    log_level = settings.log_level.upper()

    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {
                "request_context": {"()": "app.core.logging.RequestContextFilter"},
                "redaction": {"()": "app.core.logging.RedactionFilter"},
            },
            "formatters": {
                "json": {"()": "app.core.logging.JsonFormatter"},
            },
            "handlers": {
                "default": {
                    "class": "logging.StreamHandler",
                    "level": log_level,
                    "formatter": "json",
                    "filters": ["request_context", "redaction"],
                }
            },
            "root": {"level": log_level, "handlers": ["default"]},
        }
    )
