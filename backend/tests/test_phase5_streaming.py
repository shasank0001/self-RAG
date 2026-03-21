from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.pipeline.graph import GraphExecutionResult
from app.pipeline.state import Citation, GraphState, ProviderTraceEntry, RetrievalMode
from app.models.chat_session import ChatSession
from app.models.user import User
from app.services import chat_service


class FakeSession:
    def __init__(self, chat_session: ChatSession) -> None:
        self.chat_session = chat_session
        self.messages = []

    def add(self, obj) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = uuid4()
        if obj.__class__.__name__ == "ChatMessage":
            self.messages.append(obj)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def refresh(self, _obj) -> None:
        return None

    async def execute(self, statement):
        from app.models.chat_message import ChatMessage

        for criterion in statement._where_criteria:  # noqa: SLF001
            left = getattr(criterion, "left", None)
            right = getattr(criterion, "right", None)
            if getattr(left, "name", None) == "id":
                target_id = getattr(right, "value", None)
                break
        else:
            target_id = None

        found = None
        for msg in self.messages:
            if isinstance(msg, ChatMessage) and msg.id == target_id:
                found = msg
                break

        class Result:
            def scalar_one_or_none(self_inner):
                return found

        return Result()


def _event_names_from_sse(text: str) -> list[str]:
    chunks = [item for item in text.split("\n\n") if item.strip()]
    names: list[str] = []
    for chunk in chunks:
        for line in chunk.split("\n"):
            if line.startswith("event: "):
                names.append(line.split("event: ", 1)[1])
                break
    return names


def _coerce_sse_chunk(part: object) -> str:
    if isinstance(part, bytes):
        return part.decode("utf-8")
    return str(part)


@pytest.mark.asyncio
async def test_stream_endpoint_emits_token_citations_done(monkeypatch) -> None:
    from app.api.v1.routes import chat as chat_routes

    user = User(id=uuid4(), clerk_user_id="user_chat", email="chat@example.com")
    chat_session_record = ChatSession(
        id=uuid4(),
        user_id=user.id,
        title="Chat",
        initial_bin_ids=[],
        last_active_bin_ids=[],
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    fake_session = FakeSession(chat_session_record)
    selected_bin_id = uuid4()

    async def fake_require_session_owner(_session, *, session_id, owner_user_id):
        assert session_id == chat_session_record.id
        assert owner_user_id == user.id
        return chat_session_record

    async def fake_list_owned_bins(_session, *, owner_user_id, bin_ids):
        assert owner_user_id == user.id
        return [
            SimpleNamespace(
                id=selected_bin_id,
                title="Knowledge",
                vector_namespace="ns-knowledge",
                embedding_provider="deterministic",
                embedding_model="deterministic-v1",
                embedding_dimensions=64,
            )
        ]

    async def fake_run_graph(*, runtime, user_query, selected_bins):
        return GraphExecutionResult(
            state=GraphState(
                user_query=user_query,
                selected_bins=selected_bins,
                selected_bin_ids=[selected_bin_id],
                bin_ids_used=[selected_bin_id],
                retrieval_mode=RetrievalMode.GROUNDED,
                final_answer="Grounded answer about policy obligations.",
                citations=[
                    Citation(
                        item_name="doc.txt",
                        chunk_excerpt="evidence",
                        bin_title="Knowledge",
                        chunk_id="chunk-1",
                        score=0.92,
                    )
                ],
                chosen_provider="openrouter",
                chosen_model="model-x",
                provider_trace=[
                    ProviderTraceEntry(
                        node_name="answer_generator",
                        provider="openrouter",
                        model="model-x",
                        attempt=1,
                        success=True,
                        duration_ms=5.0,
                    )
                ],
                prompt_versions={"answer_generator": "v1"},
                edge_transitions=["retrieval_decision:retrieve", "answer_generator:grounded"],
            )
        )

    monkeypatch.setattr(chat_service, "require_session_owner", fake_require_session_owner)
    monkeypatch.setattr(chat_service, "list_owned_bins", fake_list_owned_bins)
    monkeypatch.setattr(chat_service, "run_self_rag_graph", fake_run_graph)
    monkeypatch.setattr(chat_routes, "require_session_owner", fake_require_session_owner)

    payload = chat_routes.ChatTurnRequest(message="What is in my docs?", bin_ids=[selected_bin_id])
    response = await chat_routes.create_chat_turn(
        session_id=chat_session_record.id,
        payload=payload,
        session=cast(Any, fake_session),
        current_user=user,
        last_event_id=None,
    )

    chunks = []
    async for part in response.body_iterator:
        chunks.append(_coerce_sse_chunk(part))

    text = "".join(chunks)
    assert "event: token" in text
    assert "event: citations" in text
    assert "event: done" in text
    assert "event: error" not in text
    event_names = _event_names_from_sse(text)
    assert event_names[0] == "token"
    assert event_names[-2:] == ["citations", "done"]


@pytest.mark.asyncio
async def test_stream_emits_heartbeat_on_long_responses(monkeypatch) -> None:
    from app.api.v1.routes import chat as chat_routes

    user = User(id=uuid4(), clerk_user_id="user_chat", email="chat@example.com")
    chat_session_record = ChatSession(
        id=uuid4(),
        user_id=user.id,
        title="Chat",
        initial_bin_ids=[],
        last_active_bin_ids=[],
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    fake_session = FakeSession(chat_session_record)
    selected_bin_id = uuid4()

    async def fake_require_session_owner(_session, *, session_id, owner_user_id):
        return chat_session_record

    async def fake_list_owned_bins(_session, *, owner_user_id, bin_ids):
        return [
            SimpleNamespace(
                id=selected_bin_id,
                title="Knowledge",
                vector_namespace="ns-knowledge",
                embedding_provider="deterministic",
                embedding_model="deterministic-v1",
                embedding_dimensions=64,
            )
        ]

    async def fake_run_graph(*, runtime, user_query, selected_bins):
        return GraphExecutionResult(
            state=GraphState(
                user_query=user_query,
                selected_bins=selected_bins,
                selected_bin_ids=[selected_bin_id],
                bin_ids_used=[selected_bin_id],
                retrieval_mode=RetrievalMode.GROUNDED,
                final_answer=" ".join(["chunk"] * 60),
                citations=[],
                chosen_provider="openrouter",
                chosen_model="model-x",
                provider_trace=[],
                prompt_versions={"answer_generator": "v1"},
            )
        )

    monkeypatch.setattr(chat_service, "require_session_owner", fake_require_session_owner)
    monkeypatch.setattr(chat_service, "list_owned_bins", fake_list_owned_bins)
    monkeypatch.setattr(chat_service, "run_self_rag_graph", fake_run_graph)
    monkeypatch.setattr(chat_routes, "require_session_owner", fake_require_session_owner)
    monkeypatch.setattr(chat_routes, "get_settings", lambda: SimpleNamespace(chat_stream_heartbeat_interval_ms=250))

    payload = chat_routes.ChatTurnRequest(message="What is in my docs?", bin_ids=[selected_bin_id])
    response = await chat_routes.create_chat_turn(
        session_id=chat_session_record.id,
        payload=payload,
        session=cast(Any, fake_session),
        current_user=user,
        last_event_id=None,
    )
    text = "".join([_coerce_sse_chunk(part) async for part in response.body_iterator])
    assert "event: heartbeat" in text


@pytest.mark.asyncio
async def test_stream_resume_replays_from_cursor(monkeypatch) -> None:
    from app.api.v1.routes import chat as chat_routes
    from app.models.chat_message import ChatMessage, MessageRole

    user = User(id=uuid4(), clerk_user_id="user_chat", email="chat@example.com")
    session_id = uuid4()
    chat_session_record = ChatSession(
        id=session_id,
        user_id=user.id,
        title="Chat",
        initial_bin_ids=[],
        last_active_bin_ids=[],
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    fake_session = FakeSession(chat_session_record)

    assistant_message_id = uuid4()
    assistant_message = ChatMessage(
        id=assistant_message_id,
        session_id=session_id,
        user_id=user.id,
        role=MessageRole.ASSISTANT,
        content="hello",
        bin_ids_used=[],
        retrieval_mode=RetrievalMode.PARAMETRIC,
        citations=[],
        prompt_versions={},
        provider_metadata={
            "stream": {
                "assistant_message_id": str(assistant_message_id),
                "cursor_max": f"{assistant_message_id}:2",
                "events": [
                    chat_routes.StreamEvent(
                        id=f"{assistant_message_id}:1",
                        event="token",
                        data={"text": "hello"},
                        ts=datetime.now(UTC).isoformat(),
                    ).model_dump(mode="json"),
                    chat_routes.StreamEvent(
                        id=f"{assistant_message_id}:2",
                        event="done",
                        data={"ok": True},
                        ts=datetime.now(UTC).isoformat(),
                    ).model_dump(mode="json"),
                ],
            }
        },
    )
    fake_session.messages.append(assistant_message)

    async def fake_require_session_owner(_session, *, session_id, owner_user_id):
        assert owner_user_id == user.id
        assert session_id == chat_session_record.id
        return chat_session_record

    monkeypatch.setattr(chat_routes, "require_session_owner", fake_require_session_owner)

    payload = chat_routes.ChatTurnRequest(message=None, cursor=f"{assistant_message_id}:1")
    response = await chat_routes.create_chat_turn(
        session_id=session_id,
        payload=payload,
        session=cast(Any, fake_session),
        current_user=user,
        last_event_id=None,
    )

    text = "".join([_coerce_sse_chunk(part) async for part in response.body_iterator])
    assert "event: done" in text
    assert "event: token" not in text


@pytest.mark.asyncio
async def test_stream_invalid_cursor_emits_error_event(monkeypatch) -> None:
    from app.api.v1.routes import chat as chat_routes

    user = User(id=uuid4(), clerk_user_id="user_chat", email="chat@example.com")
    chat_session_record = ChatSession(
        id=uuid4(),
        user_id=user.id,
        title="Chat",
        initial_bin_ids=[],
        last_active_bin_ids=[],
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    fake_session = FakeSession(chat_session_record)

    async def fake_require_session_owner(_session, *, session_id, owner_user_id):
        return chat_session_record

    monkeypatch.setattr(chat_routes, "require_session_owner", fake_require_session_owner)

    payload = chat_routes.ChatTurnRequest(message=None, cursor="invalid")
    response = await chat_routes.create_chat_turn(
        session_id=chat_session_record.id,
        payload=payload,
        session=cast(Any, fake_session),
        current_user=user,
        last_event_id=None,
    )

    text = "".join([_coerce_sse_chunk(part) async for part in response.body_iterator])
    assert "event: error" in text
    assert "cursor_invalid" in text


def test_sse_frame_format_and_contract_examples() -> None:
    from app.api.v1.routes import chat as chat_routes

    event = chat_routes.StreamEvent(
        id="abc:1",
        event="token",
        data={"text": "hello"},
        ts=datetime.now(UTC).isoformat(),
    )
    frame = chat_routes._encode_sse(event)
    assert frame.startswith("event: token\nid: abc:1\ndata: ")
    assert frame.endswith("\n\n")

    payload = frame.split("data: ", 1)[1].strip()
    envelope = json.loads(payload)
    assert envelope["id"] == "abc:1"
    assert envelope["event"] == "token"
    assert envelope["data"]["text"] == "hello"


def test_stream_endpoint_requires_authentication() -> None:
    from app.db.session import get_db_session
    from app.main import create_app

    class FakeDb:
        pass

    app = create_app()

    async def override_db_session():
        yield FakeDb()

    app.dependency_overrides[get_db_session] = override_db_session
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/chat/sessions/{uuid4()}/message",
            json={"message": "hello"},
        )

    assert response.status_code == 401


def test_stream_endpoint_accepts_valid_authentication(monkeypatch) -> None:
    from app.api.v1.routes import chat as chat_routes
    from app.auth.dependencies import get_current_user
    from app.db.session import get_db_session
    from app.main import create_app
    from app.models.chat_message import ChatMessage, MessageRole

    user = User(id=uuid4(), clerk_user_id="user_ok", email="ok@example.com")
    session_record = ChatSession(
        id=uuid4(),
        user_id=user.id,
        title="Chat",
        initial_bin_ids=[],
        last_active_bin_ids=[],
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    class FakeDbSession:
        def __init__(self):
            self._messages = []

        def add(self, obj):
            if getattr(obj, "id", None) is None:
                obj.id = uuid4()
            if isinstance(obj, ChatMessage):
                self._messages.append(obj)

        async def flush(self):
            return None

        async def commit(self):
            return None

        async def refresh(self, _obj):
            return None

        async def execute(self, statement):
            target_id = None
            for criterion in statement._where_criteria:  # noqa: SLF001
                left = getattr(criterion, "left", None)
                right = getattr(criterion, "right", None)
                if getattr(left, "name", None) == "id":
                    target_id = getattr(right, "value", None)
                    break

            found = None
            for msg in self._messages:
                if msg.id == target_id and msg.role == MessageRole.ASSISTANT:
                    found = msg
                    break

            class Result:
                def scalar_one_or_none(self_inner):
                    return found

            return Result()

    fake_db = FakeDbSession()

    async def override_db_session():
        yield fake_db

    async def override_current_user():
        return user

    async def fake_require_session_owner(_session, *, session_id, owner_user_id):
        return session_record

    async def fake_run_chat_turn(_session, *, current_user, session_id, user_message, active_bin_ids=None, runtime=None):
        assistant = ChatMessage(
            session_id=session_record.id,
            user_id=user.id,
            role=MessageRole.ASSISTANT,
            content="hello from assistant",
            retrieval_mode=RetrievalMode.PARAMETRIC,
            citations=[],
            bin_ids_used=[],
            provider_metadata={},
            prompt_versions={},
        )
        fake_db.add(assistant)
        return SimpleNamespace(assistant_message=assistant)

    monkeypatch.setattr(chat_routes, "require_session_owner", fake_require_session_owner)
    monkeypatch.setattr(chat_routes, "run_chat_turn", fake_run_chat_turn)

    app = create_app()
    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_current_user] = override_current_user

    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/chat/sessions/{session_record.id}/message",
            json={"message": "hello"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
