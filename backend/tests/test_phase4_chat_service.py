from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.errors import BadRequestError, UpstreamError
from app.pipeline.graph import GraphExecutionResult
from app.pipeline.state import Citation, GraphError, GraphState, ProviderTraceEntry, RetrievalMode, RouterErrorType
from app.models.chat_message import MessageRole
from app.models.chat_session import ChatSession
from app.models.user import User
from app.services import chat_service


class FakeSession:
    def __init__(self, chat_session: ChatSession) -> None:
        self.chat_session = chat_session
        self.messages = []
        self.commits = 0

    def add(self, obj) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = uuid4()
        if obj.__class__.__name__ == "ChatMessage":
            self.messages.append(obj)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, _obj) -> None:
        return None


@pytest.mark.asyncio
async def test_chat_turn_persists_retrieval_mode_citations_and_provider_metadata(monkeypatch) -> None:
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
                final_answer="Grounded answer",
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

    result = await chat_service.run_chat_turn(
        fake_session,
        current_user=user,
        session_id=chat_session_record.id,
        user_message="What is in my docs?",
        active_bin_ids=[selected_bin_id],
        runtime=object(),
    )

    assert len(fake_session.messages) == 2
    user_message = fake_session.messages[0]
    assistant_message = fake_session.messages[1]

    assert user_message.role == MessageRole.USER
    assert assistant_message.role == MessageRole.ASSISTANT
    assert assistant_message.retrieval_mode == RetrievalMode.GROUNDED
    assert assistant_message.citations[0]["chunk_id"] == "chunk-1"
    assert assistant_message.bin_ids_used == [selected_bin_id]
    assert assistant_message.provider_metadata["chosen_provider"] == "openrouter"
    assert assistant_message.provider_metadata["chosen_model"] == "model-x"
    assert assistant_message.prompt_versions["answer_generator"] == "v1"
    assert result.assistant_message.id == assistant_message.id


@pytest.mark.asyncio
async def test_chat_turn_raises_bad_request_for_alignment_errors(monkeypatch) -> None:
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

    async def fake_list_owned_bins(_session, *, owner_user_id, bin_ids):
        return []

    async def fake_run_graph(*, runtime, user_query, selected_bins):
        return GraphExecutionResult(
            state=GraphState(
                user_query=user_query,
                selected_bins=selected_bins,
                selected_bin_ids=[],
                bin_ids_used=[],
                error=GraphError(
                    code="embedding_alignment_mismatch",
                    message="mismatch",
                    node_name="retrieval",
                    error_type=RouterErrorType.BAD_REQUEST,
                    retryable=False,
                ),
            )
        )

    monkeypatch.setattr(chat_service, "require_session_owner", fake_require_session_owner)
    monkeypatch.setattr(chat_service, "list_owned_bins", fake_list_owned_bins)
    monkeypatch.setattr(chat_service, "run_self_rag_graph", fake_run_graph)

    with pytest.raises(BadRequestError):
        await chat_service.run_chat_turn(
            fake_session,
            current_user=user,
            session_id=chat_session_record.id,
            user_message="hello",
            active_bin_ids=[],
            runtime=object(),
        )


@pytest.mark.asyncio
async def test_chat_turn_raises_upstream_error_for_provider_failures(monkeypatch) -> None:
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

    async def fake_list_owned_bins(_session, *, owner_user_id, bin_ids):
        return []

    async def fake_run_graph(*, runtime, user_query, selected_bins):
        return GraphExecutionResult(
            state=GraphState(
                user_query=user_query,
                selected_bins=selected_bins,
                selected_bin_ids=[],
                bin_ids_used=[],
                error=GraphError(
                    code="provider_call_failed",
                    message="provider down",
                    node_name="answer_generator",
                    error_type=RouterErrorType.UPSTREAM_5XX,
                    retryable=True,
                ),
            )
        )

    monkeypatch.setattr(chat_service, "require_session_owner", fake_require_session_owner)
    monkeypatch.setattr(chat_service, "list_owned_bins", fake_list_owned_bins)
    monkeypatch.setattr(chat_service, "run_self_rag_graph", fake_run_graph)

    with pytest.raises(UpstreamError):
        await chat_service.run_chat_turn(
            fake_session,
            current_user=user,
            session_id=chat_session_record.id,
            user_message="hello",
            active_bin_ids=[],
            runtime=object(),
        )
