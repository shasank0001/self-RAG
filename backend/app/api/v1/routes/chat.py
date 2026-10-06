from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.security import guard_text_payload
from app.api.middleware.request_context import bind_request_context
from app.auth.dependencies import get_current_user
from app.core.config import get_settings
from app.core.errors import AppError, BadRequestError
from app.db.session import SessionLocal, get_db_session
from app.models.chat_message import ChatMessage, MessageRole, RetrievalMode
from app.models.user import User
from app.observability.events import LogEvent
from app.pipeline.graph import stream_self_rag_graph
from app.pipeline.state import GraphState, NodeOutcome
from app.services.authorization import require_session_owner
from app.services.chat_service import persist_chat_turn_result, prepare_chat_turn, run_chat_turn

router = APIRouter(prefix="/chat", tags=["chat"])
logger = logging.getLogger(__name__)

THINKING_LABELS: dict[str, str] = {
    "retrieval_decision": "Selecting retrieval mode",
    "retrieval": "Searching selected bins",
    "relevance_grader": "Checking evidence relevance",
    "query_rewriter": "Rewriting search query",
    "answer_generator": "Drafting answer",
    "hallucination_grader": "Verifying grounding",
    "research_planner": "Planning deep research",
    "research_executor": "Running research searches",
    "research_synthesizer": "Writing research report",
}


class StreamEvent(BaseModel):
    id: str
    event: str
    data: dict[str, Any]
    ts: str


class ChatTurnRequest(BaseModel):
    message: str | None = Field(default=None, min_length=1)
    bin_ids: list[UUID] | None = None
    cursor: str | None = None
    research: bool = False


class ChatTurnResponse(BaseModel):
    message_id: UUID
    session_id: UUID
    content: str
    retrieval_mode: str
    citations: list[dict]
    bin_ids_used: list[UUID]
    provider_metadata: dict
    prompt_versions: dict[str, str]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _chunk_text(content: str, *, size: int = 28) -> list[str]:
    cleaned = content or ""
    if not cleaned:
        return []
    return [cleaned[index : index + size] for index in range(0, len(cleaned), size)]


def _sequence_events(
    *,
    assistant_message_id: UUID,
    events: list[StreamEvent],
    start_sequence: int = 1,
) -> list[StreamEvent]:
    assigned: list[StreamEvent] = []
    for offset, event in enumerate(events):
        assigned.append(
            StreamEvent(
                id=f"{assistant_message_id}:{start_sequence + offset}",
                event=event.event,
                data=event.data,
                ts=event.ts,
            )
        )
    return assigned


def _build_provider_metadata(
    graph_state: GraphState,
    *,
    thinking_steps: list[dict[str, Any]],
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


def _done_payload(
    *,
    assistant_message_id: UUID,
    session_id: UUID,
    graph_state: GraphState,
    thinking_steps: list[dict[str, Any]],
) -> dict[str, Any]:
    retrieval_mode = graph_state.retrieval_mode or RetrievalMode.PARAMETRIC
    return {
        "message_id": str(assistant_message_id),
        "session_id": str(session_id),
        "content": graph_state.final_answer or "",
        "retrieval_mode": retrieval_mode.value if hasattr(retrieval_mode, "value") else str(retrieval_mode),
        "citations": [item.model_dump(mode="json") for item in graph_state.citations],
        "bin_ids_used": [str(item) for item in graph_state.bin_ids_used],
        "provider_metadata": _build_provider_metadata(graph_state, thinking_steps=thinking_steps),
        "prompt_versions": {str(key): str(value) for key, value in graph_state.prompt_versions.items()},
    }


def _build_post_graph_events(
    *,
    assistant_message_id: UUID,
    session_id: UUID,
    graph_state: GraphState,
    thinking_steps: list[dict[str, Any]],
    heartbeat_interval_ms: int,
    start_sequence: int,
) -> list[StreamEvent]:
    done_payload = _done_payload(
        assistant_message_id=assistant_message_id,
        session_id=session_id,
        graph_state=graph_state,
        thinking_steps=thinking_steps,
    )
    chunks = _chunk_text(graph_state.final_answer or "")
    heartbeat_every = max(1, heartbeat_interval_ms // 250)

    events: list[StreamEvent] = []
    for index, chunk in enumerate(chunks, start=1):
        events.append(StreamEvent(id="", event="token", data={"text": chunk}, ts=_now_iso()))
        if index % heartbeat_every == 0 and index < len(chunks):
            events.append(StreamEvent(id="", event="heartbeat", data={"status": "alive"}, ts=_now_iso()))

    events.append(
        StreamEvent(
            id="",
            event="citations",
            data={
                "citations": done_payload["citations"],
                "retrieval_mode": done_payload["retrieval_mode"],
            },
            ts=_now_iso(),
        )
    )
    events.append(StreamEvent(id="", event="done", data=done_payload, ts=_now_iso()))
    return _sequence_events(
        assistant_message_id=assistant_message_id,
        events=events,
        start_sequence=start_sequence,
    )


def _legacy_chat_turn_response(assistant: ChatMessage) -> ChatTurnResponse:
    retrieval_mode = assistant.retrieval_mode.value if assistant.retrieval_mode is not None else RetrievalMode.PARAMETRIC.value
    citations_payload = assistant.citations if isinstance(assistant.citations, list) else []
    provider_metadata = assistant.provider_metadata if isinstance(assistant.provider_metadata, dict) else {}
    prompt_versions = assistant.prompt_versions if isinstance(assistant.prompt_versions, dict) else {}

    return ChatTurnResponse(
        message_id=assistant.id,
        session_id=assistant.session_id,
        content=assistant.content,
        retrieval_mode=retrieval_mode,
        citations=[dict(item) for item in citations_payload if isinstance(item, dict)],
        bin_ids_used=assistant.bin_ids_used,
        provider_metadata=provider_metadata,
        prompt_versions={str(key): str(value) for key, value in prompt_versions.items()},
    )


def _encode_sse(event: StreamEvent) -> str:
    envelope = {
        "id": event.id,
        "event": event.event,
        "data": event.data,
        "ts": event.ts,
    }
    return f"event: {event.event}\nid: {event.id}\ndata: {json.dumps(envelope)}\n\n"


def _error_event(*, event_id: str, code: str, message: str, details: dict[str, Any] | None = None) -> StreamEvent:
    return StreamEvent(
        id=event_id,
        event="error",
        data={
            "code": code,
            "message": message,
            "details": details or {},
        },
        ts=_now_iso(),
    )


def _cursor_parts(cursor: str) -> tuple[UUID, int]:
    if ":" not in cursor:
        raise ValueError("Cursor must be in '<assistant_message_id>:<sequence_number>' format")

    raw_message_id, raw_seq = cursor.split(":", 1)
    assistant_message_id = UUID(raw_message_id)
    sequence_number = int(raw_seq)
    if sequence_number < 0:
        raise ValueError("Cursor sequence number must be non-negative")
    return assistant_message_id, sequence_number


def _decode_stream_events(payload: object) -> list[StreamEvent]:
    if not isinstance(payload, list):
        raise ValueError("Stream payload is not a list")

    decoded: list[StreamEvent] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("Stream payload item is not an object")
        decoded.append(StreamEvent.model_validate(item))
    return decoded


def _thinking_step_payload(
    *,
    step_id: str,
    node_name: str,
    status: str,
    detail: str,
    attempt: int,
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "step_id": step_id,
        "node_name": node_name,
        "label": THINKING_LABELS[node_name],
        "status": status,
        "detail": detail,
        "attempt": attempt,
    }

    provider_trace = result.get("provider_trace") if isinstance(result, dict) else None
    if isinstance(provider_trace, list):
        for item in reversed(provider_trace):
            if not isinstance(item, dict):
                continue
            if item.get("node_name") != node_name:
                continue
            provider = item.get("provider")
            model = item.get("model")
            if provider:
                payload["provider"] = str(provider)
            if model:
                payload["model"] = str(model)
            break

    return payload


def _thinking_started_detail(node_name: str) -> str:
    if node_name == "retrieval_decision":
        return "Checking whether retrieval is needed."
    if node_name == "retrieval":
        return "Searching the active bins for evidence."
    if node_name == "relevance_grader":
        return "Filtering the most relevant evidence."
    if node_name == "query_rewriter":
        return "Preparing a tighter search query."
    if node_name == "answer_generator":
        return "Composing the assistant response."
    if node_name == "hallucination_grader":
        return "Running a grounding check on the draft."
    if node_name == "research_planner":
        return "Breaking the question into research steps."
    if node_name == "research_executor":
        return "Searching the active bins for each step."
    if node_name == "research_synthesizer":
        return "Writing the research report."
    return "Running this step."


def _thinking_completed_detail(node_name: str, result: dict[str, Any]) -> str:
    if node_name == "retrieval_decision":
        next_step = result.get("next_step")
        if next_step == NodeOutcome.RETRIEVE:
            return "Selected grounded retrieval."
        return "Selected direct answer generation."
    if node_name == "retrieval":
        docs = result.get("retrieved_documents")
        if isinstance(docs, list):
            return f"Found {len(docs)} document(s) to inspect."
        return "Search completed."
    if node_name == "relevance_grader":
        docs = result.get("relevant_documents")
        if isinstance(docs, list):
            return f"Kept {len(docs)} relevant document(s)."
        return "Evidence review completed."
    if node_name == "query_rewriter":
        rewritten_query = result.get("rewritten_query")
        if isinstance(rewritten_query, str) and rewritten_query.strip():
            return "Prepared another retrieval pass."
        return "Rewrite step completed."
    if node_name == "answer_generator":
        retrieval_mode = result.get("retrieval_mode")
        if hasattr(retrieval_mode, "value"):
            retrieval_mode = retrieval_mode.value
        if retrieval_mode == "grounded":
            return "Drafted a grounded answer."
        return "Drafted a parametric answer."
    if node_name == "hallucination_grader":
        next_step = result.get("next_step")
        if next_step == NodeOutcome.REGENERATE:
            return "Requested another answer pass."
        return "Grounding check passed."
    if node_name == "research_planner":
        plan = result.get("research_plan")
        if isinstance(plan, list) and plan:
            return f"Planned {len(plan)} research step(s)."
        return "Falling back to standard search."
    if node_name == "research_executor":
        docs = result.get("relevant_documents")
        if isinstance(docs, list):
            return f"Gathered {len(docs)} research document(s)."
        return "Research search completed."
    if node_name == "research_synthesizer":
        retrieval_mode = result.get("retrieval_mode")
        if hasattr(retrieval_mode, "value"):
            retrieval_mode = retrieval_mode.value
        if retrieval_mode == "grounded":
            return "Drafted the research report."
        return "Drafted a parametric answer."
    return "Step completed."


def _thinking_failed_detail(node_name: str, result: dict[str, Any]) -> str:
    error = result.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message.strip():
            return message
    return f"{THINKING_LABELS[node_name]} failed."


def _task_to_thinking_event(
    *,
    assistant_message_id: UUID,
    sequence_number: int,
    task_payload: dict[str, Any],
    task_attempts: dict[str, int],
    task_context: dict[str, dict[str, Any]],
) -> StreamEvent | None:
    node_name = task_payload.get("name")
    if not isinstance(node_name, str) or node_name not in THINKING_LABELS:
        return None

    task_id = task_payload.get("id")
    if not isinstance(task_id, str):
        return None

    event_ts = _now_iso()
    if "result" not in task_payload and "error" not in task_payload:
        attempt = task_attempts.get(node_name, 0) + 1
        task_attempts[node_name] = attempt
        step_id = f"{node_name}:{attempt}"
        task_context[task_id] = {"attempt": attempt, "step_id": step_id}
        payload = _thinking_step_payload(
            step_id=step_id,
            node_name=node_name,
            status="started",
            detail=_thinking_started_detail(node_name),
            attempt=attempt,
        )
        return StreamEvent(id=f"{assistant_message_id}:{sequence_number}", event="thinking", data=payload, ts=event_ts)

    context = task_context.get(task_id, {})
    attempt = int(context.get("attempt", task_attempts.get(node_name, 1)))
    step_id = str(context.get("step_id", f"{node_name}:{attempt}"))
    result = task_payload.get("result")
    result_payload = result if isinstance(result, dict) else {}
    error = task_payload.get("error")
    status_name = "failed" if error is not None else "completed"
    detail = (
        _thinking_failed_detail(node_name, result_payload)
        if status_name == "failed"
        else _thinking_completed_detail(node_name, result_payload)
    )
    payload = _thinking_step_payload(
        step_id=step_id,
        node_name=node_name,
        status=status_name,
        detail=detail,
        attempt=attempt,
        result=result_payload,
    )
    return StreamEvent(id=f"{assistant_message_id}:{sequence_number}", event="thinking", data=payload, ts=event_ts)


async def _load_resume_events(
    session: AsyncSession,
    *,
    current_user: User,
    session_id: UUID,
    cursor: str,
) -> list[StreamEvent]:
    try:
        assistant_message_id, sequence_number = _cursor_parts(cursor)
    except (ValueError, TypeError) as exc:
        raise BadRequestError(code="cursor_invalid", message="Resume cursor is invalid") from exc

    result = await session.execute(
        select(ChatMessage).where(
            ChatMessage.id == assistant_message_id,
            ChatMessage.session_id == session_id,
            ChatMessage.user_id == current_user.id,
            ChatMessage.role == MessageRole.ASSISTANT,
        )
    )
    assistant_message = result.scalar_one_or_none()
    if assistant_message is None:
        raise BadRequestError(code="cursor_invalid", message="Resume cursor is stale or invalid")

    metadata = assistant_message.provider_metadata if isinstance(assistant_message.provider_metadata, dict) else {}
    stream_payload = metadata.get("stream")
    if not isinstance(stream_payload, dict):
        raise BadRequestError(code="cursor_invalid", message="Resume cursor is stale or invalid")

    try:
        events = _decode_stream_events(stream_payload.get("events"))
    except ValueError as exc:
        raise BadRequestError(code="cursor_invalid", message="Resume cursor is stale or invalid") from exc

    if not events:
        raise BadRequestError(code="cursor_invalid", message="Resume cursor is stale or invalid")

    max_sequence = len(events)
    if sequence_number >= max_sequence:
        raise BadRequestError(code="cursor_invalid", message="Resume cursor is stale or invalid")

    return events[sequence_number:]


@router.post(
    "/sessions/{session_id}/message",
    status_code=status.HTTP_200_OK,
    summary="Run and stream a chat turn",
    description="Runs Self-RAG graph for a user message and streams SSE events (`thinking`, `token`, `citations`, `done`, `heartbeat`, `error`).",
)
async def create_chat_turn(
    session_id: UUID,
    payload: ChatTurnRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
):
    settings = get_settings()
    heartbeat_interval_ms = max(settings.chat_stream_heartbeat_interval_ms, 1)
    current_user_id = current_user.id

    async def generate():
        try:
            with bind_request_context(chat_id=str(session_id)):
                logger.info(LogEvent.SSE_STREAM_STARTED, extra={"chat_id": str(session_id)})
                async with SessionLocal() as stream_session:
                    stream_user = User(id=current_user_id, clerk_user_id=current_user.clerk_user_id, email=current_user.email)
                    await require_session_owner(stream_session, session_id=session_id, owner_user_id=current_user_id)
                    effective_cursor = payload.cursor or last_event_id

                    if effective_cursor:
                        logger.info(
                            LogEvent.SSE_STREAM_RESUMED,
                            extra={"chat_id": str(session_id), "cursor": effective_cursor},
                        )
                        replay_events = await _load_resume_events(
                            stream_session,
                            current_user=stream_user,
                            session_id=session_id,
                            cursor=effective_cursor,
                        )
                        for item in replay_events:
                            await asyncio.sleep(0)
                            yield _encode_sse(item)
                            if item.event == "done":
                                logger.info(
                                    LogEvent.SSE_STREAM_DONE,
                                    extra={"chat_id": str(session_id), "assistant_message_id": item.id.split(":", 1)[0]},
                                )
                        return

                    if payload.message is None or not payload.message.strip():
                        raise BadRequestError(code="message_required", message="Message is required when cursor is not provided")
                    guard_text_payload(text=payload.message, field_name="message")

                    prepared_turn = await prepare_chat_turn(
                        stream_session,
                        current_user=stream_user,
                        session_id=session_id,
                        user_message=payload.message,
                        active_bin_ids=payload.bin_ids,
                        research_mode=payload.research,
                    )

                    assistant_message_id = uuid4()
                    next_sequence = 1
                    thinking_events: list[StreamEvent] = []
                    thinking_steps: list[dict[str, Any]] = []
                    task_attempts: dict[str, int] = {}
                    task_context: dict[str, dict[str, Any]] = {}
                    last_graph_state_payload: dict[str, Any] | None = None

                    async for mode, data in stream_self_rag_graph(
                        runtime=prepared_turn.graph_runtime,
                        user_query=payload.message,
                        selected_bins=prepared_turn.selected_bins,
                        research_mode=prepared_turn.research_mode,
                    ):
                        if mode == "values" and isinstance(data, dict):
                            last_graph_state_payload = data
                            continue
                        if mode != "tasks" or not isinstance(data, dict):
                            continue

                        thinking_event = _task_to_thinking_event(
                            assistant_message_id=assistant_message_id,
                            sequence_number=next_sequence,
                            task_payload=data,
                            task_attempts=task_attempts,
                            task_context=task_context,
                        )
                        if thinking_event is None:
                            continue

                        next_sequence += 1
                        thinking_events.append(thinking_event)
                        thinking_steps.append({**thinking_event.data, "ts": thinking_event.ts})
                        await asyncio.sleep(0)
                        yield _encode_sse(thinking_event)

                    if last_graph_state_payload is None:
                        raise BadRequestError(code="stream_empty", message="Graph produced no terminal state")

                    graph_state = GraphState.model_validate(last_graph_state_payload)
                    post_graph_events = _build_post_graph_events(
                        assistant_message_id=assistant_message_id,
                        session_id=session_id,
                        graph_state=graph_state,
                        thinking_steps=thinking_steps,
                        heartbeat_interval_ms=heartbeat_interval_ms,
                        start_sequence=next_sequence,
                    )
                    all_events = thinking_events + post_graph_events

                    await persist_chat_turn_result(
                        stream_session,
                        current_user=stream_user,
                        session_id=session_id,
                        prepared_turn=prepared_turn,
                        graph_state=graph_state,
                        assistant_message_id=assistant_message_id,
                        thinking_steps=thinking_steps,
                        stream_events=[item.model_dump(mode="json") for item in all_events],
                    )

                    for item in post_graph_events:
                        await asyncio.sleep(0)
                        yield _encode_sse(item)
                        if item.event == "done":
                            logger.info(
                                LogEvent.SSE_STREAM_DONE,
                                extra={"chat_id": str(session_id), "assistant_message_id": item.id.split(":", 1)[0]},
                            )
        except asyncio.CancelledError:
            return
        except AppError as exc:
            logger.warning(
                LogEvent.SSE_STREAM_ERROR,
                extra={"chat_id": str(session_id), "error_code": exc.code, "reason": exc.message},
            )
            event_id = payload.cursor or last_event_id or "error:0"
            yield _encode_sse(_error_event(event_id=event_id, code=exc.code, message=exc.message, details=exc.details))
        except Exception as exc:  # noqa: BLE001
            logger.exception(LogEvent.SSE_STREAM_ERROR, extra={"chat_id": str(session_id)})
            event_id = payload.cursor or last_event_id or "error:0"
            yield _encode_sse(
                _error_event(
                    event_id=event_id,
                    code="stream_unexpected_error",
                    message="Unexpected stream failure",
                    details={"reason": str(exc)},
                )
            )

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/sessions/{session_id}/message-sync",
    response_model=ChatTurnResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Run a chat turn (sync)",
    description="Compatibility endpoint for non-streaming clients. Runs Self-RAG and returns a finalized assistant payload.",
)
async def create_chat_turn_sync(
    session_id: UUID,
    payload: ChatTurnRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> ChatTurnResponse:
    if payload.message is None or not payload.message.strip():
        raise BadRequestError(code="message_required", message="Message is required")
    guard_text_payload(text=payload.message, field_name="message")

    result = await run_chat_turn(
        session,
        current_user=current_user,
        session_id=session_id,
        user_message=payload.message,
        active_bin_ids=payload.bin_ids,
        research_mode=payload.research,
    )
    return _legacy_chat_turn_response(result.assistant_message)
