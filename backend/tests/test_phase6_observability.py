from __future__ import annotations

import logging
from uuid import uuid4

import pytest

from app.api.middleware.request_context import bind_request_context, get_request_context, update_request_context
from app.core.logging import JsonFormatter, RedactionFilter, RequestContextFilter
from app.observability.metrics import get_metrics_registry, record_usage_rollup
from app.pipeline.state import ProviderTraceEntry
from app.router.error_types import RouterErrorType


def test_request_context_bind_and_update() -> None:
    with bind_request_context(request_id="req-1", chat_id="chat-1"):
        context = get_request_context()
        assert context["request_id"] == "req-1"
        assert context["chat_id"] == "chat-1"

        update_request_context(provider="openrouter", model="m1", fallback_attempt=2)
        updated = get_request_context()
        assert updated["provider"] == "openrouter"
        assert updated["model"] == "m1"
        assert updated["fallback_attempt"] == 2


def test_request_context_filter_injects_fields() -> None:
    record = logging.LogRecord(
        name="ctx",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="event",
        args=(),
        exc_info=None,
    )
    with bind_request_context(request_id="req-2", chat_id="chat-2", job_id=str(uuid4())):
        assert RequestContextFilter().filter(record) is True
    assert getattr(record, "request_id") == "req-2"
    assert getattr(record, "chat_id") == "chat-2"


def test_json_formatter_outputs_context_fields() -> None:
    record = logging.LogRecord(
        name="json",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="provider.call.completed",
        args=(),
        exc_info=None,
    )
    with bind_request_context(request_id="req-3", provider="openai", model="gpt-4o-mini"):
        RequestContextFilter().filter(record)
        payload = JsonFormatter().format(record)

    assert '"request_id": "req-3"' in payload
    assert '"provider": "openai"' in payload
    assert '"model": "gpt-4o-mini"' in payload


def test_usage_rollup_record_includes_fallback_rate() -> None:
    row = record_usage_rollup(
        provider="openai",
        model="gpt-4o-mini",
        tokens_in=120,
        tokens_out=80,
        provider_reported_cost_usd=0.02,
        estimated_cost_usd=0.019,
        fallback=True,
    )
    assert row.requests == 1
    assert row.fallback_events == 1
    assert row.fallback_rate == 1.0


def test_metrics_registry_renders_prometheus() -> None:
    registry = get_metrics_registry()
    registry.inc("selfrag_test_counter_total", provider="openai")
    registry.observe("selfrag_test_latency_ms", 12.5, node="answer_generator")
    rendered = registry.render_prometheus()
    assert "selfrag_test_counter_total" in rendered
    assert "selfrag_test_latency_ms_count" in rendered


def test_provider_trace_entry_supports_fallback_usage_fields() -> None:
    entry = ProviderTraceEntry(
        node_name="answer_generator",
        provider="openrouter",
        model="m1",
        attempt=1,
        success=False,
        duration_ms=123.1,
        error_type=RouterErrorType.TIMEOUT,
        error_message="timeout",
        fallback_from="cerebras",
        fallback_attempt=2,
        tokens_in=100,
        tokens_out=200,
        estimated_cost_usd=0.001,
        provider_reported_cost_usd=0.002,
    )
    assert entry.fallback_attempt == 2
    assert entry.tokens_in == 100


def test_redaction_filter_masks_prompt_and_secret_keys() -> None:
    record = logging.LogRecord(
        name="redact",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg={"prompt": "hello", "secret": "top", "safe": "ok"},
        args=(),
        exc_info=None,
    )
    assert RedactionFilter().filter(record)
    assert record.msg["prompt"] == "[REDACTED]"
    assert record.msg["secret"] == "[REDACTED]"
    assert record.msg["safe"] == "ok"


@pytest.mark.asyncio
async def test_usage_rollup_query_route_requires_authentication() -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app()
    with TestClient(app) as client:
        response = client.get("/api/v1/observability/usage-rollups")
    assert response.status_code == 401
