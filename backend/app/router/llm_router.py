from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from app.api.middleware.request_context import bind_request_context, update_request_context
from app.core.config import Settings, get_settings
from app.core.pipeline_config import SelfRagConfig, get_pipeline_config
from app.core.tracing import start_span
from app.observability.costs import extract_usage_from_payload, get_cost_calculator
from app.observability.events import LogEvent
from app.observability.metrics import get_metrics_registry
from app.pipeline.state import ProviderMessage, ProviderRequest, ProviderResponse, ProviderTraceEntry
from app.router.error_types import RouterErrorType

logger = logging.getLogger(__name__)
metrics = get_metrics_registry()


class RouterCallError(Exception):
    def __init__(
        self,
        *,
        error_type: RouterErrorType,
        message: str,
        retryable: bool,
        status_code: int | None = None,
        trace: list[ProviderTraceEntry] | None = None,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message
        self.retryable = retryable
        self.status_code = status_code
        self.trace = trace or []


@dataclass(slots=True)
class RouterCallResult:
    provider: str
    model: str
    content: str
    raw_payload: dict[str, Any]
    trace: list[ProviderTraceEntry]


class ProviderAdapter(Protocol):
    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        ...


class _OpenAIMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    role: str
    content: str


class _OpenAIChoice(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message: _OpenAIMessage


class _OpenAIResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    choices: list[_OpenAIChoice]
    usage: dict[str, Any] | None = None


class _OllamaMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str
    content: str


class _OllamaResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: _OllamaMessage
    prompt_eval_count: int | None = None
    eval_count: int | None = None


def _classify_http_error(status_code: int) -> tuple[RouterErrorType, bool]:
    if status_code == 429:
        return (RouterErrorType.RATE_LIMIT, True)
    if status_code in {401, 403}:
        return (RouterErrorType.AUTH, False)
    if status_code >= 500:
        return (RouterErrorType.UPSTREAM_5XX, True)
    return (RouterErrorType.BAD_REQUEST, False)


class OpenAICompatibleAdapter:
    def __init__(self, *, provider_name: str, base_url: str, api_key: str, extra_headers: dict[str, str] | None = None) -> None:
        self._provider_name = provider_name
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._extra_headers = extra_headers or {}

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        if not self._api_key:
            raise RouterCallError(
                error_type=RouterErrorType.AUTH,
                message=f"{self._provider_name} API key is missing",
                retryable=False,
            )

        payload = {
            "model": request.model,
            "messages": [item.model_dump(mode="json") for item in request.messages],
            "temperature": request.temperature,
            "stream": False,
        }

        timeout_seconds = max(request.timeout_ms, 1) / 1000
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            **self._extra_headers,
        }

        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await client.post(f"{self._base_url}/chat/completions", json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise RouterCallError(
                error_type=RouterErrorType.TIMEOUT,
                message=f"{self._provider_name} request timed out",
                retryable=True,
            ) from exc
        except httpx.RequestError as exc:
            raise RouterCallError(
                error_type=RouterErrorType.NETWORK,
                message=f"{self._provider_name} request failed",
                retryable=True,
            ) from exc

        if response.status_code >= 400:
            error_type, retryable = _classify_http_error(response.status_code)
            raise RouterCallError(
                error_type=error_type,
                message=f"{self._provider_name} returned HTTP {response.status_code}",
                retryable=retryable,
                status_code=response.status_code,
            )

        try:
            response_payload = _OpenAIResponse.model_validate(response.json())
        except (ValidationError, ValueError) as exc:
            raise RouterCallError(
                error_type=RouterErrorType.SCHEMA_ERROR,
                message=f"{self._provider_name} returned malformed payload",
                retryable=False,
            ) from exc

        if not response_payload.choices:
            raise RouterCallError(
                error_type=RouterErrorType.SCHEMA_ERROR,
                message=f"{self._provider_name} returned no choices",
                retryable=False,
            )

        return ProviderResponse(
            provider=request.provider,
            model=request.model,
            content=response_payload.choices[0].message.content,
            raw_payload=response_payload.model_dump(mode="json"),
            usage=extract_usage_from_payload(response_payload.model_dump(mode="json")),
        )


class OllamaAdapter:
    def __init__(self, *, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        payload = {
            "model": request.model,
            "messages": [item.model_dump(mode="json") for item in request.messages],
            "stream": False,
            "options": {"temperature": request.temperature},
        }
        timeout_seconds = max(request.timeout_ms, 1) / 1000

        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await client.post(f"{self._base_url}/api/chat", json=payload)
        except httpx.TimeoutException as exc:
            raise RouterCallError(
                error_type=RouterErrorType.TIMEOUT,
                message="ollama request timed out",
                retryable=True,
            ) from exc
        except httpx.RequestError as exc:
            raise RouterCallError(
                error_type=RouterErrorType.NETWORK,
                message="ollama request failed",
                retryable=True,
            ) from exc

        if response.status_code >= 400:
            error_type, retryable = _classify_http_error(response.status_code)
            raise RouterCallError(
                error_type=error_type,
                message=f"ollama returned HTTP {response.status_code}",
                retryable=retryable,
                status_code=response.status_code,
            )

        try:
            response_payload = _OllamaResponse.model_validate(response.json())
        except (ValidationError, ValueError) as exc:
            raise RouterCallError(
                error_type=RouterErrorType.SCHEMA_ERROR,
                message="ollama returned malformed payload",
                retryable=False,
            ) from exc

        return ProviderResponse(
            provider=request.provider,
            model=request.model,
            content=response_payload.message.content,
            raw_payload=response_payload.model_dump(mode="json"),
            usage={
                "prompt_tokens": response_payload.prompt_eval_count or 0,
                "completion_tokens": response_payload.eval_count or 0,
                "total_tokens": (response_payload.prompt_eval_count or 0) + (response_payload.eval_count or 0),
            },
        )


def _build_default_adapters(settings: Settings) -> dict[str, ProviderAdapter]:
    return {
        "openai": OpenAICompatibleAdapter(
            provider_name="openai",
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
        ),
        "openrouter": OpenAICompatibleAdapter(
            provider_name="openrouter",
            api_key=settings.openrouter_api_key,
            base_url=settings.openrouter_base_url,
            extra_headers={"HTTP-Referer": settings.api_base_url},
        ),
        "cerebras": OpenAICompatibleAdapter(
            provider_name="cerebras",
            api_key=settings.cerebras_api_key,
            base_url=settings.cerebras_base_url,
        ),
        "ollama": OllamaAdapter(base_url=settings.ollama_base_url),
    }


def _to_router_message(item: ProviderMessage) -> dict[str, str]:
    return {"role": item.role, "content": item.content}


class LLMRouter:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        pipeline_config: SelfRagConfig | None = None,
        adapters: dict[str, ProviderAdapter] | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._pipeline_config = pipeline_config or get_pipeline_config()
        self._adapters = adapters or _build_default_adapters(self._settings)

    async def call_node(
        self,
        *,
        node_name: str,
        messages: list[ProviderMessage],
        temperature: float = 0.0,
        timeout_ms: int | None = None,
    ) -> RouterCallResult:
        node_config = self._pipeline_config.llm.nodes[node_name]
        default_timeout = self._pipeline_config.llm.timeouts_ms.default
        generation_timeout = self._pipeline_config.llm.timeouts_ms.generation
        selected_timeout = timeout_ms or (generation_timeout if node_name == "answer_generator" else default_timeout)

        fallback_chain = [node_config.provider] + [
            provider for provider in self._pipeline_config.llm.fallback_chain if provider != node_config.provider
        ]

        attempts_per_provider = max(self._pipeline_config.llm.retries.transient_max + 1, 1)
        trace: list[ProviderTraceEntry] = []
        cost_calculator = get_cost_calculator(self._settings)
        previous_provider = node_config.provider

        for provider_index, provider_name in enumerate(fallback_chain, start=1):
            if provider_name not in self._adapters:
                raise RouterCallError(
                    error_type=RouterErrorType.BAD_REQUEST,
                    message=f"Provider adapter '{provider_name}' is not configured",
                    retryable=False,
                    trace=trace,
                )

            if provider_name == node_config.provider:
                model_name = node_config.model
            else:
                model_name = self._pipeline_config.llm.fallback_models.get(provider_name, node_config.model)

            if provider_name != node_config.provider:
                logger.warning(
                    LogEvent.PROVIDER_FALLBACK,
                    extra={
                        "provider": provider_name,
                        "model": model_name,
                        "fallback_attempt": provider_index,
                        "fallback_from": previous_provider,
                        "node_name": node_name,
                    },
                )
                metrics.inc(
                    "selfrag_provider_fallback_total",
                    provider=provider_name,
                    model=model_name,
                    node=node_name,
                )

            for attempt in range(1, attempts_per_provider + 1):
                request = ProviderRequest(
                    provider=provider_name,
                    model=model_name,
                    node_name=node_name,
                    messages=[ProviderMessage.model_validate(_to_router_message(item)) for item in messages],
                    timeout_ms=selected_timeout,
                    temperature=temperature,
                )

                started = time.perf_counter()
                update_request_context(provider=provider_name, model=model_name, fallback_attempt=provider_index)
                logger.info(
                    LogEvent.PROVIDER_CALL_STARTED,
                    extra={
                        "provider": provider_name,
                        "model": model_name,
                        "fallback_attempt": provider_index,
                        "node_name": node_name,
                        "attempt": attempt,
                    },
                )

                with bind_request_context(provider=provider_name, model=model_name, fallback_attempt=provider_index):
                    with start_span(
                        "provider.call",
                        attributes={
                            "provider": provider_name,
                            "model": model_name,
                            "node": node_name,
                            "fallback": provider_name != node_config.provider,
                            "fallback_attempt": provider_index,
                            "retry_attempt": attempt,
                        },
                    ) as span:
                        try:
                            response = await self._adapters[provider_name].generate(request)
                            usage = response.usage or {}
                            tokens_in = int(usage.get("prompt_tokens", 0) or 0)
                            tokens_out = int(usage.get("completion_tokens", 0) or 0)
                            span.set_attribute("tokens_in", tokens_in)
                            span.set_attribute("tokens_out", tokens_out)
                        except RouterCallError as exc:
                            duration_ms = round((time.perf_counter() - started) * 1000, 3)
                            span.set_attribute("duration_ms", duration_ms)
                            span.set_attribute("error_type", exc.error_type.value)
                            trace.append(
                                ProviderTraceEntry(
                                    node_name=node_name,
                                    provider=provider_name,
                                    model=model_name,
                                    attempt=attempt,
                                    success=False,
                                    duration_ms=duration_ms,
                                    error_type=exc.error_type,
                                    error_message=exc.message,
                                    fallback_from=previous_provider if provider_name != node_config.provider else None,
                                    fallback_attempt=provider_index,
                                )
                            )
                            metrics.inc(
                                "selfrag_provider_calls_total",
                                provider=provider_name,
                                model=model_name,
                                node=node_name,
                                success=False,
                            )
                            metrics.observe(
                                "selfrag_provider_call_latency_ms",
                                duration_ms,
                                provider=provider_name,
                                model=model_name,
                                node=node_name,
                            )
                            metrics.inc(
                                "selfrag_provider_errors_total",
                                provider=provider_name,
                                model=model_name,
                                error_type=exc.error_type.value,
                                node=node_name,
                            )
                            logger.warning(
                                LogEvent.PROVIDER_CALL_FAILED,
                                extra={
                                    "provider": provider_name,
                                    "model": model_name,
                                    "node_name": node_name,
                                    "attempt": attempt,
                                    "fallback_attempt": provider_index,
                                    "error_type": exc.error_type.value,
                                    "duration_ms": duration_ms,
                                },
                            )

                            if exc.retryable and attempt < attempts_per_provider:
                                previous_provider = provider_name
                                continue
                            if exc.retryable:
                                previous_provider = provider_name
                                break

                            raise RouterCallError(
                                error_type=exc.error_type,
                                message=exc.message,
                                retryable=False,
                                status_code=exc.status_code,
                                trace=trace,
                            ) from exc

                duration_ms = round((time.perf_counter() - started) * 1000, 3)
                usage = response.usage or {}
                tokens_in = int(usage.get("prompt_tokens", 0) or 0)
                tokens_out = int(usage.get("completion_tokens", 0) or 0)
                provider_reported_cost = float(usage.get("cost", 0.0) or 0.0)
                estimated_cost = cost_calculator.estimate_cost(
                    provider=provider_name,
                    model=model_name,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                )
                trace.append(
                    ProviderTraceEntry(
                        node_name=node_name,
                        provider=provider_name,
                        model=model_name,
                        attempt=attempt,
                        success=True,
                        duration_ms=duration_ms,
                        fallback_from=previous_provider if provider_name != node_config.provider else None,
                        fallback_attempt=provider_index,
                        tokens_in=tokens_in,
                        tokens_out=tokens_out,
                        estimated_cost_usd=estimated_cost,
                        provider_reported_cost_usd=provider_reported_cost,
                    )
                )
                metrics.inc(
                    "selfrag_provider_calls_total",
                    provider=provider_name,
                    model=model_name,
                    node=node_name,
                    success=True,
                )
                metrics.observe(
                    "selfrag_provider_call_latency_ms",
                    duration_ms,
                    provider=provider_name,
                    model=model_name,
                    node=node_name,
                )
                metrics.inc("selfrag_provider_tokens_in_total", tokens_in, provider=provider_name, model=model_name)
                metrics.inc("selfrag_provider_tokens_out_total", tokens_out, provider=provider_name, model=model_name)
                metrics.observe(
                    "selfrag_provider_estimated_cost_usd",
                    estimated_cost,
                    provider=provider_name,
                    model=model_name,
                )
                logger.info(
                    LogEvent.PROVIDER_CALL_COMPLETED,
                    extra={
                        "provider": provider_name,
                        "model": model_name,
                        "node_name": node_name,
                        "attempt": attempt,
                        "fallback_attempt": provider_index,
                        "duration_ms": duration_ms,
                        "tokens_in": tokens_in,
                        "tokens_out": tokens_out,
                        "estimated_cost_usd": estimated_cost,
                    },
                )
                return RouterCallResult(
                    provider=provider_name,
                    model=model_name,
                    content=response.content,
                    raw_payload=response.raw_payload,
                    trace=trace,
                )
            previous_provider = provider_name

        raise RouterCallError(
            error_type=RouterErrorType.UPSTREAM_5XX,
            message=f"All providers failed for node '{node_name}'",
            retryable=True,
            trace=trace,
        )
