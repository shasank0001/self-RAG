from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from fastapi import FastAPI

from app.core.config import Settings, get_settings
from app.db.session import engine

_TRACING_AVAILABLE = True

try:
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
except ImportError:  # pragma: no cover - fallback path for minimal environments
    _TRACING_AVAILABLE = False


class _NoopSpan:
    def set_attribute(self, _key: str, _value: Any) -> None:
        return None

    def record_exception(self, _exc: Exception) -> None:
        return None


@contextmanager
def start_span(name: str, attributes: dict[str, Any] | None = None):
    if _TRACING_AVAILABLE:
        tracer = trace.get_tracer("self-rag-backend")
        with tracer.start_as_current_span(name) as span:
            if attributes:
                for key, value in attributes.items():
                    span.set_attribute(key, value)
            yield span
        return

    _ = name
    _ = attributes
    yield _NoopSpan()


def setup_tracing(app: FastAPI, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    if not settings.otel_tracing_enabled or not _TRACING_AVAILABLE:
        return

    resource = Resource(attributes={"service.name": settings.otel_service_name})
    provider = TracerProvider(resource=resource)

    if settings.otel_exporter_otlp_endpoint:
        exporter = OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint)
        provider.add_span_processor(BatchSpanProcessor(exporter))

    trace.set_tracer_provider(provider)

    FastAPIInstrumentor.instrument_app(app)
    SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)
    HTTPXClientInstrumentor().instrument()
