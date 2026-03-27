from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.core.config import Settings
from app.core.pipeline_config import SelfRagConfig
from app.pipeline.state import ProviderMessage
from app.router.error_types import RouterErrorType
from app.router.llm_router import (
    LLMRouter,
    OpenAICompatibleAdapter,
    ProviderAdapter,
    ProviderRequest,
    ProviderResponse,
    RouterCallError,
)


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


@pytest.mark.asyncio
async def test_openai_compatible_adapter_flattens_content_parts(monkeypatch) -> None:
    class FakeResponse:
        status_code = 200

        @staticmethod
        def json() -> dict[str, object]:
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": [
                                {"type": "text", "text": "hello"},
                                {"type": "text", "text": " world"},
                            ],
                        }
                    }
                ]
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url: str, json: dict[str, object], headers: dict[str, str]):
            return FakeResponse()

    monkeypatch.setattr("app.router.llm_router.httpx.AsyncClient", lambda timeout: FakeClient())

    adapter = OpenAICompatibleAdapter(
        provider_name="openrouter",
        api_key="or-key",
        base_url="https://openrouter.ai/api/v1",
    )

    response = await adapter.generate(
        ProviderRequest(
            provider="openrouter",
            model="openai/gpt-5.4-mini",
            node_name="answer_generator",
            messages=[ProviderMessage(role="user", content="hello")],
            timeout_ms=1000,
        )
    )

    assert response.content == "hello world"


@pytest.mark.asyncio
async def test_openai_compatible_adapter_surfaces_http_error_detail(monkeypatch) -> None:
    class FakeResponse:
        status_code = 400
        text = '{"error":{"message":"temperature is not supported"}}'

        @staticmethod
        def json() -> dict[str, object]:
            return {"error": {"message": "temperature is not supported"}}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url: str, json: dict[str, object], headers: dict[str, str]):
            return FakeResponse()

    monkeypatch.setattr("app.router.llm_router.httpx.AsyncClient", lambda timeout: FakeClient())

    adapter = OpenAICompatibleAdapter(
        provider_name="openrouter",
        api_key="or-key",
        base_url="https://openrouter.ai/api/v1",
    )

    with pytest.raises(RouterCallError) as exc_info:
        await adapter.generate(
            ProviderRequest(
                provider="openrouter",
                model="openai/gpt-5.4-mini",
                node_name="answer_generator",
                messages=[ProviderMessage(role="user", content="hello")],
                timeout_ms=1000,
            )
        )

    assert exc_info.value.error_type == RouterErrorType.BAD_REQUEST
    assert exc_info.value.message == "openrouter returned HTTP 400: temperature is not supported"
