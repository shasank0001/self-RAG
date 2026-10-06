from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.middleware.request_context import bind_request_context
from app.core.errors import BadRequestError, NotFoundError, UpstreamError
from app.core.tracing import start_span
from app.models.chat_message import ChatMessage, MessageRole
from app.models.chat_session import ChatSession
from app.models.user import User
from app.observability.metrics import get_metrics_registry, record_usage_rollup
from app.pipeline.graph import GraphExecutionResult, GraphRuntime, build_graph_runtime, run_self_rag_graph
from app.pipeline.state import GraphState, NodeOutcome, RetrievalMode, SelectedBin
from app.services.authorization import list_owned_bins, require_session_owner
from app.services.usage_rollups import upsert_usage_rollup

logger = logging.getLogger(__name__)
metrics = get_metrics_registry()


@dataclass(slots=True)
class ChatTurnResult:
    assistant_message: ChatMessage
    graph_state: GraphState


@dataclass(slots=True)
class PreparedChatTurn:
    chat_session: ChatSession
    graph_runtime: GraphRuntime
    selected_bin_ids: list[UUID]
    selected_bins: list[SelectedBin]
    research_mode: bool = False


def _to_selected_bin(bin_record) -> SelectedBin:
    if (
        bin_record.embedding_provider is None
        or bin_record.embedding_model is None
        or bin_record.embedding_dimensions is None
    ):
        raise BadRequestError(
            code="bin_embedding_unconfigured",
            message="Bin embedding metadata is incomplete; re-run ingestion setup",
            details={"bin_id": str(bin_record.id)},
        )

    return SelectedBin(
        id=bin_record.id,
        title=bin_record.title,
        vector_namespace=bin_record.vector_namespace,
        embedding_provider=bin_record.embedding_provider,
        embedding_model=bin_record.embedding_model,
        embedding_dimensions=bin_record.embedding_dimensions,
    )


async def _resolve_active_bins(
    session: AsyncSession,
    *,
    current_user: User,
    requested_bin_ids: list[UUID],
) -> list[SelectedBin]:
    bins = await list_owned_bins(session, owner_user_id=current_user.id, bin_ids=requested_bin_ids)
    return [_to_selected_bin(item) for item in bins]


def _provider_metadata(
    graph_state: GraphState,
    *,
    thinking_steps: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    metadata = {
        "chosen_provider": graph_state.chosen_provider,
        "chosen_model": graph_state.chosen_model,
        "provider_trace": [item.model_dump(mode="json") for item in graph_state.provider_trace],
        "edge_transitions": graph_state.edge_transitions,
    }
    if thinking_steps:
        metadata["thinking_steps"] = [dict(item) for item in thinking_steps]
    return metadata


def _raise_for_graph_failure(graph_state: GraphState) -> None:
    if graph_state.error is not None:
        error = graph_state.error
        if error.code in {"embedding_alignment_mismatch", "bin_embedding_unconfigured", "schema_validation_failed"}:
            raise BadRequestError(code=error.code, message=error.message, details=error.details)
        if error.code == "not_found":
            raise NotFoundError(message=error.message)
        raise UpstreamError(code=error.code, message=error.message, details=error.details)

    if graph_state.next_step == NodeOutcome.ABORT:
        raise UpstreamError(code="graph_aborted", message="Graph execution aborted unexpectedly")


async def _record_usage_metrics(
    session: AsyncSession,
    *,
    session_id: UUID,
    graph_state: GraphState,
) -> None:
    fallback = any(
        entry.fallback_attempt is not None and entry.fallback_attempt > 1 for entry in graph_state.provider_trace
    )
    for entry in graph_state.provider_trace:
        if not entry.success:
            continue
        rollup = record_usage_rollup(
            provider=entry.provider,
            model=entry.model,
            tokens_in=entry.tokens_in or 0,
            tokens_out=entry.tokens_out or 0,
            provider_reported_cost_usd=entry.provider_reported_cost_usd or 0.0,
            estimated_cost_usd=entry.estimated_cost_usd or 0.0,
            fallback=fallback,
        )
        if hasattr(session, "execute"):
            await upsert_usage_rollup(session, rollup)
        metrics.inc("selfrag_usage_rollup_requests_total", rollup.requests, provider=rollup.provider, model=rollup.model)
        metrics.inc("selfrag_usage_rollup_tokens_in_total", rollup.tokens_in, provider=rollup.provider, model=rollup.model)
        metrics.inc(
            "selfrag_usage_rollup_tokens_out_total",
            rollup.tokens_out,
            provider=rollup.provider,
            model=rollup.model,
        )
        metrics.observe(
            "selfrag_usage_rollup_estimated_cost_usd",
            rollup.estimated_cost_usd,
            provider=rollup.provider,
            model=rollup.model,
        )
        drift = abs((entry.provider_reported_cost_usd or 0.0) - (entry.estimated_cost_usd or 0.0))
        metrics.observe(
            "selfrag_usage_cost_drift_usd",
            drift,
            provider=entry.provider,
            model=entry.model,
        )
        if drift > 0.0:
            metrics.inc("selfrag_usage_cost_drift_events_total", provider=entry.provider, model=entry.model)
        if fallback:
            metrics.inc("selfrag_usage_rollup_fallback_events_total", provider=entry.provider, model=entry.model)

    await session.commit()

    logger.info(
        "chat.turn.completed",
        extra={
            "chat_id": str(session_id),
            "provider": graph_state.chosen_provider,
            "model": graph_state.chosen_model,
            "fallback": fallback,
            "citations": len(graph_state.citations),
        },
    )


async def prepare_chat_turn(
    session: AsyncSession,
    *,
    current_user: User,
    session_id: UUID,
    user_message: str,
    active_bin_ids: list[UUID] | None = None,
    runtime: GraphRuntime | None = None,
    research_mode: bool = False,
) -> PreparedChatTurn:
    chat_session = await require_session_owner(session, session_id=session_id, owner_user_id=current_user.id)
    selected_bin_ids = active_bin_ids if active_bin_ids is not None else list(chat_session.last_active_bin_ids)
    selected_bins = await _resolve_active_bins(
        session,
        current_user=current_user,
        requested_bin_ids=selected_bin_ids,
    )

    session.add(
        ChatMessage(
            session_id=chat_session.id,
            user_id=current_user.id,
            role=MessageRole.USER,
            content=user_message,
            bin_ids_used=selected_bin_ids,
            retrieval_mode=None,
            citations=[],
            provider_metadata={},
            prompt_versions={},
        )
    )
    await session.flush()

    return PreparedChatTurn(
        chat_session=chat_session,
        graph_runtime=runtime or build_graph_runtime(),
        selected_bin_ids=selected_bin_ids,
        selected_bins=selected_bins,
        research_mode=research_mode,
    )


async def persist_chat_turn_result(
    session: AsyncSession,
    *,
    current_user: User,
    session_id: UUID,
    prepared_turn: PreparedChatTurn,
    graph_state: GraphState,
    assistant_message_id: UUID | None = None,
    thinking_steps: list[dict[str, Any]] | None = None,
    stream_events: list[dict[str, Any]] | None = None,
) -> ChatTurnResult:
    _raise_for_graph_failure(graph_state)

    retrieval_mode = graph_state.retrieval_mode or RetrievalMode.PARAMETRIC
    provider_metadata = _provider_metadata(graph_state, thinking_steps=thinking_steps)
    if stream_events:
        resolved_id = assistant_message_id or UUID(str(stream_events[0]["id"]).split(":", 1)[0])
        provider_metadata["stream"] = {
            "assistant_message_id": str(resolved_id),
            "cursor_max": stream_events[-1]["id"],
            "events": [dict(item) for item in stream_events],
        }

    assistant_kwargs: dict[str, Any] = {
        "session_id": prepared_turn.chat_session.id,
        "user_id": current_user.id,
        "role": MessageRole.ASSISTANT,
        "content": graph_state.final_answer or "",
        "bin_ids_used": graph_state.bin_ids_used,
        "retrieval_mode": retrieval_mode,
        "citations": [item.model_dump(mode="json") for item in graph_state.citations],
        "provider_metadata": provider_metadata,
        "prompt_versions": graph_state.prompt_versions,
    }
    if assistant_message_id is not None:
        assistant_kwargs["id"] = assistant_message_id

    assistant_record = ChatMessage(**assistant_kwargs)
    session.add(assistant_record)

    prepared_turn.chat_session.last_active_bin_ids = prepared_turn.selected_bin_ids
    await session.commit()
    await session.refresh(assistant_record)

    await _record_usage_metrics(session, session_id=session_id, graph_state=graph_state)
    return ChatTurnResult(assistant_message=assistant_record, graph_state=graph_state)


async def run_chat_turn(
    session: AsyncSession,
    *,
    current_user: User,
    session_id: UUID,
    user_message: str,
    active_bin_ids: list[UUID] | None = None,
    runtime: GraphRuntime | None = None,
    research_mode: bool = False,
) -> ChatTurnResult:
    with bind_request_context(chat_id=str(session_id)):
        with start_span("chat.turn", attributes={"chat_id": str(session_id), "bin_count": len(active_bin_ids or [])}):
            prepared_turn = await prepare_chat_turn(
                session,
                current_user=current_user,
                session_id=session_id,
                user_message=user_message,
                active_bin_ids=active_bin_ids,
                runtime=runtime,
                research_mode=research_mode,
            )
            execution = await run_self_rag_graph(
                runtime=prepared_turn.graph_runtime,
                user_query=user_message,
                selected_bins=prepared_turn.selected_bins,
                research_mode=prepared_turn.research_mode,
            )

        return await persist_chat_turn_result(
            session,
            current_user=current_user,
            session_id=session_id,
            prepared_turn=prepared_turn,
            graph_state=execution.state,
        )
