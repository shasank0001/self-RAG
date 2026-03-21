from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.api.middleware.request_context import bind_request_context
from app.api.dependencies.security import guard_text_payload
from app.core.config import get_settings
from app.core.errors import AppError, BadRequestError
from app.observability.events import LogEvent
from app.db.session import get_db_session
from app.models.chat_message import ChatMessage, MessageRole, RetrievalMode
from app.models.user import User
from app.services.authorization import require_session_owner
from app.services.chat_service import run_chat_turn

router = APIRouter(prefix="/chat", tags=["chat"])
logger = logging.getLogger(__name__)


class StreamEvent(BaseModel):
    id: str
    event: str
    data: dict[str, Any]
    ts: str


class ChatTurnRequest(BaseModel):
    message: str | None = Field(default=None, min_length=1)
    bin_ids: list[UUID] | None = None
    cursor: str | None = None


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


def _with_cursor(*, assistant_message_id: UUID, events: list[StreamEvent]) -> list[StreamEvent]:
    assigned: list[StreamEvent] = []
    for sequence, event in enumerate(events, start=1):
        assigned.append(
            StreamEvent(
                id=f"{assistant_message_id}:{sequence}",
                event=event.event,
                data=event.data,
                ts=event.ts,
            )
        )
    return assigned


def _assistant_payload(message: ChatMessage) -> dict[str, Any]:
    retrieval_mode = message.retrieval_mode.value if message.retrieval_mode is not None else RetrievalMode.PARAMETRIC.value
    citations = message.citations if isinstance(message.citations, list) else []
    provider_metadata = message.provider_metadata if isinstance(message.provider_metadata, dict) else {}
    prompt_versions = message.prompt_versions if isinstance(message.prompt_versions, dict) else {}

    return {
        "message_id": str(message.id),
        "session_id": str(message.session_id),
        "content": message.content,
        "retrieval_mode": retrieval_mode,
        "citations": [dict(item) for item in citations if isinstance(item, dict)],
        "bin_ids_used": [str(item) for item in message.bin_ids_used],
        "provider_metadata": provider_metadata,
        "prompt_versions": {str(key): str(value) for key, value in prompt_versions.items()},
    }


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


def _build_stream_events(*, assistant_message: ChatMessage, heartbeat_interval_ms: int) -> list[StreamEvent]:
    payload = _assistant_payload(assistant_message)
    chunks = _chunk_text(assistant_message.content)
    heartbeat_every = max(1, heartbeat_interval_ms // 250)

    events: list[StreamEvent] = []
    for index, chunk in enumerate(chunks, start=1):
        events.append(
            StreamEvent(
                id="",
                event="token",
                data={"text": chunk},
                ts=_now_iso(),
            )
        )
        if index % heartbeat_every == 0 and index < len(chunks):
            events.append(
                StreamEvent(
                    id="",
                    event="heartbeat",
                    data={"status": "alive"},
                    ts=_now_iso(),
                )
            )

    events.append(
        StreamEvent(
            id="",
            event="citations",
            data={
                "citations": payload["citations"],
                "retrieval_mode": payload["retrieval_mode"],
            },
            ts=_now_iso(),
        )
    )
    events.append(
        StreamEvent(
            id="",
            event="done",
            data=payload,
            ts=_now_iso(),
        )
    )

    return _with_cursor(assistant_message_id=assistant_message.id, events=events)


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


async def _persist_stream_events(
    session: AsyncSession,
    *,
    assistant_message: ChatMessage,
    events: list[StreamEvent],
) -> None:
    metadata = dict(assistant_message.provider_metadata) if isinstance(assistant_message.provider_metadata, dict) else {}
    metadata["stream"] = {
        "assistant_message_id": str(assistant_message.id),
        "cursor_max": events[-1].id if events else f"{assistant_message.id}:0",
        "events": [item.model_dump(mode="json") for item in events],
    }
    assistant_message.provider_metadata = metadata
    session.add(assistant_message)
    await session.commit()
    await session.refresh(assistant_message)


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


async def _cleanup_cancelled_assistant(
    session: AsyncSession,
    *,
    current_user: User,
    session_id: UUID,
    assistant_message_id: UUID,
) -> None:
    if not hasattr(session, "delete"):
        return

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
        return

    await session.delete(assistant_message)
    await session.commit()


@router.post(
    "/sessions/{session_id}/message",
    status_code=status.HTTP_200_OK,
    summary="Run and stream a chat turn",
    description="Runs Self-RAG graph for a user message and streams SSE events (`token`, `citations`, `done`, `heartbeat`, `error`).",
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

    async def generate():
        assistant_message_id: UUID | None = None
        stream_completed = False
        try:
            with bind_request_context(chat_id=str(session_id)):
                logger.info(LogEvent.SSE_STREAM_STARTED, extra={"chat_id": str(session_id)})
                await require_session_owner(session, session_id=session_id, owner_user_id=current_user.id)
                effective_cursor = payload.cursor or last_event_id

                if effective_cursor:
                    logger.info(
                        LogEvent.SSE_STREAM_RESUMED,
                        extra={"chat_id": str(session_id), "cursor": effective_cursor},
                    )
                    events = await _load_resume_events(
                        session,
                        current_user=current_user,
                        session_id=session_id,
                        cursor=effective_cursor,
                    )
                else:
                    if payload.message is None or not payload.message.strip():
                        raise BadRequestError(code="message_required", message="Message is required when cursor is not provided")
                    guard_text_payload(text=payload.message, field_name="message")

                    result = await run_chat_turn(
                        session,
                        current_user=current_user,
                        session_id=session_id,
                        user_message=payload.message,
                        active_bin_ids=payload.bin_ids,
                    )
                    assistant = result.assistant_message
                    assistant_message_id = assistant.id
                    events = _build_stream_events(
                        assistant_message=assistant,
                        heartbeat_interval_ms=heartbeat_interval_ms,
                    )
                    await _persist_stream_events(session, assistant_message=assistant, events=events)

            for item in events:
                await asyncio.sleep(0)
                yield _encode_sse(item)
                if item.event == "done":
                    stream_completed = True
                    logger.info(
                        LogEvent.SSE_STREAM_DONE,
                        extra={"chat_id": str(session_id), "assistant_message_id": item.id.split(":", 1)[0]},
                    )
        except asyncio.CancelledError:
            if assistant_message_id is not None and not stream_completed:
                try:
                    await _cleanup_cancelled_assistant(
                        session,
                        current_user=current_user,
                        session_id=session_id,
                        assistant_message_id=assistant_message_id,
                    )
                except Exception:  # noqa: BLE001
                    return
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
    )
    return _legacy_chat_turn_response(result.assistant_message)
