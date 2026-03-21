from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.core.config import Settings
from app.core.pipeline_config import SelfRagConfig
from app.pipeline.state import ProviderMessage
from app.router.error_types import RouterErrorType
from app.router.llm_router import LLMRouter, ProviderAdapter, ProviderRequest, ProviderResponse, RouterCallError


@dataclass
class SequenceAdapter(ProviderAdapter):
    events: list[ProviderResponse | Exception]

    def __post_init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append((request.provider, request.model))
        if not self.events:
            raise AssertionError("No events configured")
        event = self.events.pop(0)
        if isinstance(event, Exception):
            raise event
        return event


def _config(*, transient_max: int = 0) -> SelfRagConfig:
    return SelfRagConfig.model_validate(
        {
            "llm": {
                "fallback_chain": ["primary", "secondary", "tertiary"],
                "fallback_models": {
                    "primary": "m-primary",
                    "secondary": "m-secondary",
                    "tertiary": "m-tertiary",
                },
                "nodes": {
                    "retrieval_decision": {"provider": "primary", "model": "m-primary"},
                    "relevance_grader": {"provider": "primary", "model": "m-primary"},
                    "query_rewriter": {"provider": "primary", "model": "m-primary"},
                    "answer_generator": {"provider": "primary", "model": "m-primary"},
                    "hallucination_grader": {"provider": "primary", "model": "m-primary"},
                },
                "timeouts_ms": {"default": 1000, "generation": 1000},
                "retries": {"transient_max": transient_max},
            }
        }
    )


def _response(content: str) -> ProviderResponse:
    return ProviderResponse(provider="mock", model="mock-model", content=content, raw_payload={"content": content})


@pytest.mark.asyncio
async def test_fallback_on_timeout_then_success() -> None:
    adapters = {
        "primary": SequenceAdapter(
            events=[
                RouterCallError(error_type=RouterErrorType.TIMEOUT, message="timeout", retryable=True),
            ]
        ),
        "secondary": SequenceAdapter(events=[_response('{"decision":"retrieve","reason":"ok"}')]),
        "tertiary": SequenceAdapter(events=[]),
    }
    router = LLMRouter(settings=Settings(), pipeline_config=_config(), adapters=adapters)

    result = await router.call_node(
        node_name="retrieval_decision",
        messages=[ProviderMessage(role="user", content="question")],
    )

    assert result.provider == "secondary"
    assert len(result.trace) == 2
    assert result.trace[0].error_type == RouterErrorType.TIMEOUT
    assert result.trace[1].success is True


@pytest.mark.asyncio
async def test_fallback_on_rate_limit_then_success() -> None:
    adapters = {
        "primary": SequenceAdapter(
            events=[
                RouterCallError(error_type=RouterErrorType.RATE_LIMIT, message="429", retryable=True),
            ]
        ),
        "secondary": SequenceAdapter(events=[_response('{"decision":"skip","reason":"ok"}')]),
        "tertiary": SequenceAdapter(events=[]),
    }
    router = LLMRouter(settings=Settings(), pipeline_config=_config(), adapters=adapters)

    result = await router.call_node(
        node_name="retrieval_decision",
        messages=[ProviderMessage(role="user", content="question")],
    )

    assert result.provider == "secondary"
    assert result.trace[0].error_type == RouterErrorType.RATE_LIMIT


@pytest.mark.asyncio
async def test_auth_error_stops_without_fallback() -> None:
    adapters = {
        "primary": SequenceAdapter(
            events=[
                RouterCallError(error_type=RouterErrorType.AUTH, message="unauthorized", retryable=False),
            ]
        ),
        "secondary": SequenceAdapter(events=[_response("should not run")]),
        "tertiary": SequenceAdapter(events=[]),
    }
    router = LLMRouter(settings=Settings(), pipeline_config=_config(), adapters=adapters)

    with pytest.raises(RouterCallError) as exc_info:
        await router.call_node(
            node_name="retrieval_decision",
            messages=[ProviderMessage(role="user", content="question")],
        )

    assert exc_info.value.error_type == RouterErrorType.AUTH
    assert len(exc_info.value.trace) == 1
    assert adapters["secondary"].calls == []


@pytest.mark.asyncio
async def test_schema_error_stops_without_fallback() -> None:
    adapters = {
        "primary": SequenceAdapter(
            events=[
                RouterCallError(error_type=RouterErrorType.SCHEMA_ERROR, message="bad schema", retryable=False),
            ]
        ),
        "secondary": SequenceAdapter(events=[_response("should not run")]),
        "tertiary": SequenceAdapter(events=[]),
    }
    router = LLMRouter(settings=Settings(), pipeline_config=_config(), adapters=adapters)

    with pytest.raises(RouterCallError) as exc_info:
        await router.call_node(
            node_name="retrieval_decision",
            messages=[ProviderMessage(role="user", content="question")],
        )

    assert exc_info.value.error_type == RouterErrorType.SCHEMA_ERROR
    assert adapters["secondary"].calls == []


@pytest.mark.asyncio
async def test_transient_retry_happens_before_fallback() -> None:
    adapters = {
        "primary": SequenceAdapter(
            events=[
                RouterCallError(error_type=RouterErrorType.TIMEOUT, message="timeout", retryable=True),
                _response('{"decision":"retrieve","reason":"retry success"}'),
            ]
        ),
        "secondary": SequenceAdapter(events=[_response("should not run")]),
        "tertiary": SequenceAdapter(events=[]),
    }
    router = LLMRouter(settings=Settings(), pipeline_config=_config(transient_max=1), adapters=adapters)

    result = await router.call_node(
        node_name="retrieval_decision",
        messages=[ProviderMessage(role="user", content="question")],
    )

    assert result.provider == "primary"
    assert len(adapters["primary"].calls) == 2
    assert adapters["secondary"].calls == []
