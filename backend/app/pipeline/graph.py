from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, cast

from langgraph.graph import END, START, StateGraph

from app.core.config import Settings, get_settings
from app.core.pipeline_config import SelfRagConfig, get_pipeline_config
from app.core.tracing import start_span
from app.observability.metrics import get_metrics_registry
from app.pipeline.nodes.answer_generator import answer_generator_node
from app.pipeline.nodes.hallucination_grader import hallucination_grader_node
from app.pipeline.nodes.query_rewriter import query_rewriter_node
from app.pipeline.nodes.relevance_grader import relevance_grader_node
from app.pipeline.nodes.retrieval import retrieval_node
from app.pipeline.nodes.retrieval_decision import retrieval_decision_node
from app.pipeline.prompts.registry import PromptRegistry
from app.pipeline.state import GraphError, GraphState, NodeOutcome, SelectedBin
from app.router.embedding_router import EmbeddingAlignmentError, EmbeddingRouter
from app.router.llm_router import LLMRouter, RouterCallError
from app.router.error_types import RouterErrorType
from app.services.ingestion.errors import ExternalDependencyError
from app.services.ingestion.vector_index import VectorIndex, build_vector_index

logger = logging.getLogger(__name__)
metrics = get_metrics_registry()


@dataclass(slots=True)
class GraphExecutionResult:
    state: GraphState


@dataclass(slots=True)
class GraphRuntime:
    settings: Settings
    pipeline_config: SelfRagConfig
    prompt_registry: PromptRegistry
    router: LLMRouter
    embedding_router: EmbeddingRouter
    vector_index: VectorIndex


def build_graph_runtime(
    *,
    settings: Settings | None = None,
    pipeline_config: SelfRagConfig | None = None,
    router: LLMRouter | None = None,
    embedding_router: EmbeddingRouter | None = None,
    vector_index: VectorIndex | None = None,
) -> GraphRuntime:
    settings = settings or get_settings()
    pipeline_config = pipeline_config or get_pipeline_config()
    prompt_registry = PromptRegistry(pipeline_config=pipeline_config)

    llm_router = router or LLMRouter(settings=settings, pipeline_config=pipeline_config)
    embed_router = embedding_router or EmbeddingRouter(settings=settings, pipeline_config=pipeline_config)
    resolved_vector_index = vector_index or build_vector_index(settings)

    return GraphRuntime(
        settings=settings,
        pipeline_config=pipeline_config,
        prompt_registry=prompt_registry,
        router=llm_router,
        embedding_router=embed_router,
        vector_index=resolved_vector_index,
    )


def _route_after_decision(state: GraphState) -> str:
    if state.next_step == NodeOutcome.ABORT or state.error is not None:
        return "abort"
    if state.next_step == NodeOutcome.RETRIEVE:
        return "retrieval"
    return "answer_generator"


def _route_after_retrieval(state: GraphState) -> str:
    if state.next_step == NodeOutcome.ABORT or state.error is not None:
        return "abort"
    if state.next_step == NodeOutcome.REWRITE:
        return "query_rewriter"
    return "relevance_grader"


def _route_after_relevance(state: GraphState) -> str:
    if state.next_step == NodeOutcome.ABORT or state.error is not None:
        return "abort"
    if state.next_step == NodeOutcome.REWRITE:
        return "query_rewriter"
    return "answer_generator"


def _route_after_rewrite(state: GraphState) -> str:
    if state.next_step == NodeOutcome.ABORT or state.error is not None:
        return "abort"
    if state.next_step == NodeOutcome.RETRIEVE:
        return "retrieval"
    return "answer_generator"


def _route_after_answer(state: GraphState) -> str:
    if state.next_step == NodeOutcome.ABORT or state.error is not None:
        return "abort"
    if state.next_step == NodeOutcome.REGENERATE:
        return "hallucination_grader"
    return END


def _route_after_hallucination(state: GraphState) -> str:
    if state.next_step == NodeOutcome.ABORT or state.error is not None:
        return "abort"
    if state.next_step == NodeOutcome.REGENERATE:
        return "answer_generator"
    return END


def _route_from_start(state: GraphState) -> str:
    if not state.selected_bins:
        return "answer_generator"
    return "retrieval_decision"


def _structured_graph_error(
    *,
    code: str,
    message: str,
    node_name: str,
    error_type: RouterErrorType | None,
    retryable: bool,
    details: dict[str, Any] | None = None,
) -> dict:
    return {
        "error": GraphError(
            code=code,
            message=message,
            node_name=node_name,
            error_type=error_type,
            retryable=retryable,
            details=details or {},
        ),
        "next_step": NodeOutcome.ABORT,
        "edge_transitions": [f"{node_name}:abort"],
        "retrieval_mode": None,
    }


NodeCallable = Callable[[GraphState], Awaitable[dict[str, Any]]]


def _with_node_timeout(
    *,
    node_name: str,
    timeout_ms: int,
    node_callable: NodeCallable,
) -> NodeCallable:
    async def _wrapped(state: GraphState) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            with start_span("graph.node", attributes={"node": node_name}):
                result = await asyncio.wait_for(node_callable(state), timeout=max(timeout_ms, 1) / 1000)
            duration_ms = (time.perf_counter() - started) * 1000
            metrics.observe("selfrag_graph_node_latency_ms", duration_ms, node=node_name, status="ok")
            return result
        except asyncio.TimeoutError:
            duration_ms = (time.perf_counter() - started) * 1000
            metrics.observe("selfrag_graph_node_latency_ms", duration_ms, node=node_name, status="timeout")
            metrics.inc("selfrag_graph_node_errors_total", node=node_name, error_type=RouterErrorType.TIMEOUT.value)
            return _structured_graph_error(
                code="graph_node_timeout",
                message=f"Node '{node_name}' timed out",
                node_name=node_name,
                error_type=RouterErrorType.TIMEOUT,
                retryable=True,
            )
        except RouterCallError as exc:
            duration_ms = (time.perf_counter() - started) * 1000
            metrics.observe("selfrag_graph_node_latency_ms", duration_ms, node=node_name, status="error")
            metrics.inc("selfrag_graph_node_errors_total", node=node_name, error_type=exc.error_type.value)
            payload = _structured_graph_error(
                code="provider_call_failed",
                message=exc.message,
                node_name=node_name,
                error_type=exc.error_type,
                retryable=exc.retryable,
                details={"trace": [entry.model_dump(mode="json") for entry in exc.trace]},
            )
            payload["provider_trace"] = [*state.provider_trace, *exc.trace]
            payload["edge_transitions"] = [*state.edge_transitions, f"{node_name}:abort"]
            return payload
        except EmbeddingAlignmentError as exc:
            duration_ms = (time.perf_counter() - started) * 1000
            metrics.observe("selfrag_graph_node_latency_ms", duration_ms, node=node_name, status="error")
            metrics.inc("selfrag_graph_node_errors_total", node=node_name, error_type=RouterErrorType.BAD_REQUEST.value)
            payload = _structured_graph_error(
                code=exc.code,
                message=exc.message,
                node_name=node_name,
                error_type=RouterErrorType.BAD_REQUEST,
                retryable=False,
                details={"alignment": exc.details},
            )
            payload["edge_transitions"] = [*state.edge_transitions, f"{node_name}:abort"]
            return payload
        except ExternalDependencyError as exc:
            duration_ms = (time.perf_counter() - started) * 1000
            metrics.observe("selfrag_graph_node_latency_ms", duration_ms, node=node_name, status="error")
            error_type = RouterErrorType.UPSTREAM_5XX if exc.retriable else RouterErrorType.BAD_REQUEST
            metrics.inc("selfrag_graph_node_errors_total", node=node_name, error_type=error_type.value)
            payload = _structured_graph_error(
                code=exc.code,
                message=exc.message,
                node_name=node_name,
                error_type=error_type,
                retryable=exc.retriable,
                details={"stage": exc.stage},
            )
            payload["edge_transitions"] = [*state.edge_transitions, f"{node_name}:abort"]
            return payload
        except ValueError as exc:
            duration_ms = (time.perf_counter() - started) * 1000
            metrics.observe("selfrag_graph_node_latency_ms", duration_ms, node=node_name, status="error")
            metrics.inc("selfrag_graph_node_errors_total", node=node_name, error_type=RouterErrorType.SCHEMA_ERROR.value)
            payload = _structured_graph_error(
                code="schema_validation_failed",
                message=str(exc),
                node_name=node_name,
                error_type=RouterErrorType.SCHEMA_ERROR,
                retryable=False,
            )
            payload["edge_transitions"] = [*state.edge_transitions, f"{node_name}:abort"]
            return payload
        except Exception as exc:  # noqa: BLE001
            duration_ms = (time.perf_counter() - started) * 1000
            metrics.observe("selfrag_graph_node_latency_ms", duration_ms, node=node_name, status="error")
            metrics.inc("selfrag_graph_node_errors_total", node=node_name, error_type=RouterErrorType.UNKNOWN.value)
            payload = _structured_graph_error(
                code="graph_node_failure",
                message=str(exc),
                node_name=node_name,
                error_type=RouterErrorType.UNKNOWN,
                retryable=False,
            )
            payload["edge_transitions"] = [*state.edge_transitions, f"{node_name}:abort"]
            return payload

    return _wrapped


def _build_compiled_graph(runtime: GraphRuntime):
    config = runtime.pipeline_config
    timeout_ms = config.pipeline.node_timeout_ms

    async def _retrieval_decision(state: GraphState) -> dict[str, Any]:
        return await retrieval_decision_node(state, router=runtime.router, prompt_registry=runtime.prompt_registry)

    async def _retrieval(state: GraphState) -> dict[str, Any]:
        return await retrieval_node(
            state,
            vector_index=runtime.vector_index,
            embedding_router=runtime.embedding_router,
            pipeline_config=runtime.pipeline_config,
        )

    async def _relevance_grader(state: GraphState) -> dict[str, Any]:
        return await relevance_grader_node(
            state,
            router=runtime.router,
            prompt_registry=runtime.prompt_registry,
            pipeline_config=runtime.pipeline_config,
        )

    async def _query_rewriter(state: GraphState) -> dict[str, Any]:
        return await query_rewriter_node(
            state,
            router=runtime.router,
            prompt_registry=runtime.prompt_registry,
            pipeline_config=runtime.pipeline_config,
        )

    async def _answer_generator(state: GraphState) -> dict[str, Any]:
        return await answer_generator_node(state, router=runtime.router, prompt_registry=runtime.prompt_registry)

    async def _hallucination_grader(state: GraphState) -> dict[str, Any]:
        return await hallucination_grader_node(
            state,
            router=runtime.router,
            prompt_registry=runtime.prompt_registry,
            pipeline_config=runtime.pipeline_config,
        )

    async def _abort(_: GraphState) -> dict[str, Any]:
        return {}

    builder: Any = StateGraph(GraphState)
    builder.add_node(
        "retrieval_decision",
        cast(Any, _with_node_timeout(node_name="retrieval_decision", timeout_ms=timeout_ms, node_callable=_retrieval_decision)),
    )
    builder.add_node(
        "retrieval",
        cast(Any, _with_node_timeout(node_name="retrieval", timeout_ms=timeout_ms, node_callable=_retrieval)),
    )
    builder.add_node(
        "relevance_grader",
        cast(Any, _with_node_timeout(node_name="relevance_grader", timeout_ms=timeout_ms, node_callable=_relevance_grader)),
    )
    builder.add_node(
        "query_rewriter",
        cast(Any, _with_node_timeout(node_name="query_rewriter", timeout_ms=timeout_ms, node_callable=_query_rewriter)),
    )
    builder.add_node(
        "answer_generator",
        cast(Any, _with_node_timeout(node_name="answer_generator", timeout_ms=timeout_ms, node_callable=_answer_generator)),
    )
    builder.add_node(
        "hallucination_grader",
        cast(Any, _with_node_timeout(node_name="hallucination_grader", timeout_ms=timeout_ms, node_callable=_hallucination_grader)),
    )
    builder.add_node("abort", cast(Any, _abort))

    builder.add_conditional_edges(START, _route_from_start, ["retrieval_decision", "answer_generator"])
    builder.add_conditional_edges("retrieval_decision", _route_after_decision, ["retrieval", "answer_generator", "abort"])
    builder.add_conditional_edges("retrieval", _route_after_retrieval, ["relevance_grader", "query_rewriter", "abort"])
    builder.add_conditional_edges(
        "relevance_grader",
        _route_after_relevance,
        ["query_rewriter", "answer_generator", "abort"],
    )
    builder.add_conditional_edges("query_rewriter", _route_after_rewrite, ["retrieval", "answer_generator", "abort"])
    builder.add_conditional_edges("answer_generator", _route_after_answer, ["hallucination_grader", END, "abort"])
    builder.add_conditional_edges("hallucination_grader", _route_after_hallucination, ["answer_generator", END, "abort"])
    builder.add_edge("abort", END)

    return builder.compile()


def _initial_graph_state(*, user_query: str, selected_bins: list[SelectedBin]) -> GraphState:
    selected_bin_ids = [item.id for item in selected_bins]
    return GraphState(
        user_query=user_query,
        selected_bins=selected_bins,
        selected_bin_ids=selected_bin_ids,
        bin_ids_used=selected_bin_ids,
    )


def _finalize_graph_state(final_state: GraphState) -> None:
    if final_state.provider_trace:
        first_success = next((item for item in final_state.provider_trace if item.success), None)
        if first_success is not None:
            metrics.inc(
                "selfrag_provider_usage_by_model_total",
                provider=first_success.provider,
                model=first_success.model,
            )
    metrics.inc("selfrag_graph_runs_total", status="error" if final_state.error else "ok")
    logger.info(
        "graph.execution.completed",
        extra={
            "status": "error" if final_state.error else "ok",
            "chosen_provider": final_state.chosen_provider,
            "chosen_model": final_state.chosen_model,
        },
    )


async def stream_self_rag_graph(
    *,
    runtime: GraphRuntime,
    user_query: str,
    selected_bins: list[SelectedBin],
) -> AsyncIterator[tuple[str, Any]]:
    initial_state = _initial_graph_state(user_query=user_query, selected_bins=selected_bins)
    graph = _build_compiled_graph(runtime)
    last_state_payload: dict[str, Any] | None = None

    logger.info("graph.execution.started", extra={"bin_count": len(selected_bins)})
    with start_span("graph.execution", attributes={"selected_bin_count": len(selected_bins)}):
        async for item in graph.astream(initial_state, stream_mode=["tasks", "values"]):
            if isinstance(item, tuple) and len(item) == 2 and item[0] == "values" and isinstance(item[1], dict):
                last_state_payload = item[1]
            yield item

    if last_state_payload is not None:
        _finalize_graph_state(GraphState.model_validate(last_state_payload))


async def run_self_rag_graph(
    *,
    runtime: GraphRuntime,
    user_query: str,
    selected_bins: list[SelectedBin],
) -> GraphExecutionResult:
    initial_state = _initial_graph_state(user_query=user_query, selected_bins=selected_bins)
    graph = _build_compiled_graph(runtime)
    logger.info("graph.execution.started", extra={"bin_count": len(selected_bins)})
    with start_span("graph.execution", attributes={"selected_bin_count": len(selected_bins)}):
        result = await graph.ainvoke(initial_state)
    final_state = GraphState.model_validate(result)
    _finalize_graph_state(final_state)
    return GraphExecutionResult(state=final_state)
