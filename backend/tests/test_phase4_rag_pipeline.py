from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.core.pipeline_config import SelfRagConfig
from app.pipeline.graph import build_graph_runtime, run_self_rag_graph
from app.pipeline.prompts.registry import PromptRegistry
from app.pipeline.state import ProviderMessage, ProviderTraceEntry, RouterErrorType, SelectedBin
from app.router.embedding_router import EmbeddingAlignmentError, EmbeddingRouter
from app.router.llm_router import LLMRouter, RouterCallError, RouterCallResult
from app.services.ingestion.vector_index import VectorIndex, VectorPayload, VectorSearchMatch


def _make_bin(*, title: str, provider: str = "deterministic", model: str = "deterministic-v1", dimensions: int = 64):
    return SelectedBin(
        id=uuid4(),
        title=title,
        vector_namespace=f"ns-{title.lower()}",
        embedding_provider=provider,
        embedding_model=model,
        embedding_dimensions=dimensions,
    )


@dataclass
class FakeRouterResponse:
    content: str
    provider: str = "fake"
    model: str = "fake-model"


class FakeLLMRouter(LLMRouter):
    def __init__(self, outputs: dict[str, list[str]] | None = None, errors: dict[str, Exception] | None = None) -> None:
        self.outputs = outputs or {}
        self.errors = errors or {}
        self.calls: list[str] = []

    async def call_node(
        self,
        *,
        node_name: str,
        messages: list[ProviderMessage],
        temperature: float = 0.0,
        timeout_ms: int | None = None,
    ) -> RouterCallResult:
        self.calls.append(node_name)
        if node_name in self.errors:
            raise self.errors[node_name]
        outputs = self.outputs.get(node_name)
        if not outputs:
            raise AssertionError(f"No fake output configured for node {node_name}")
        content = outputs.pop(0)
        return RouterCallResult(
            provider="fake",
            model="fake-model",
            content=content,
            raw_payload={"content": content},
            trace=[
                ProviderTraceEntry(
                    node_name=node_name,
                    provider="fake",
                    model="fake-model",
                    attempt=1,
                    success=True,
                    duration_ms=1.0,
                )
            ],
        )


class FakeEmbeddingRouter(EmbeddingRouter):
    def __init__(self, vector: list[float], *, raises: EmbeddingAlignmentError | None = None) -> None:
        self.vector = vector
        self.raises = raises

    async def embed_query(self, *, query: str, selected_bins: list[SelectedBin]):
        if self.raises is not None:
            raise self.raises
        return self.vector, None


class FakeVectorIndex(VectorIndex):
    def __init__(self, rows: dict[str, list[VectorSearchMatch]]) -> None:
        self.rows = rows
        self.calls: list[str] = []

    async def upsert(self, *, namespace: str, vectors: list[VectorPayload]) -> None:
        raise NotImplementedError

    async def delete_by_item_id(self, *, namespace: str, item_id) -> None:
        raise NotImplementedError

    async def query(self, *, namespace: str, vector: list[float], top_k: int) -> list[VectorSearchMatch]:
        self.calls.append(namespace)
        return self.rows.get(namespace, [])[:top_k]


def _match(*, chunk_id: str, bin_id, source_name: str, chunk_text: str, score: float) -> VectorSearchMatch:
    return VectorSearchMatch(
        id=chunk_id,
        score=score,
        metadata={
            "bin_id": str(bin_id),
            "item_id": str(uuid4()),
            "source_name": source_name,
            "chunk_id": chunk_id,
            "chunk_text": chunk_text,
        },
    )


def _runtime(*, router: FakeLLMRouter, vector_index: FakeVectorIndex) -> tuple[SelfRagConfig, object]:
    config = SelfRagConfig()
    runtime = build_graph_runtime(
        settings=Settings(VECTOR_INDEX_BACKEND="memory"),
        pipeline_config=config,
        router=router,
        vector_index=vector_index,
    )
    return config, runtime


@pytest.mark.asyncio
async def test_no_bin_path_bypasses_retrieval_and_parametric_mode() -> None:
    router = FakeLLMRouter(
        outputs={
            "answer_generator": ["parametric answer"],
        }
    )
    vector_index = FakeVectorIndex(rows={})
    _, runtime = _runtime(router=router, vector_index=vector_index)

    result = await run_self_rag_graph(runtime=runtime, user_query="hello", selected_bins=[])

    assert result.state.retrieval_mode.value == "parametric"
    assert result.state.citations == []
    assert "retrieval" not in router.calls
    assert "retrieval_decision" not in router.calls


@pytest.mark.asyncio
async def test_bins_selected_force_retrieval_even_for_small_talk() -> None:
    selected_bin = _make_bin(title="A")
    router = FakeLLMRouter(
        outputs={
            "relevance_grader": ['{"relevant":true,"score":0.9}'],
            "answer_generator": ["grounded via selected bin"],
            "hallucination_grader": ['{"grounded":true,"reason":"supported"}'],
        }
    )
    vector_index = FakeVectorIndex(
        rows={
            selected_bin.vector_namespace: [
                _match(
                    chunk_id="a-1",
                    bin_id=selected_bin.id,
                    source_name="resume.pdf",
                    chunk_text="Name: Shasank. Skills: Python, FastAPI, React.",
                    score=0.95,
                )
            ]
        }
    )
    _, runtime = _runtime(router=router, vector_index=vector_index)

    result = await run_self_rag_graph(runtime=runtime, user_query="how are you", selected_bins=[selected_bin])

    assert result.state.retrieval_mode.value == "grounded"
    assert vector_index.calls == [selected_bin.vector_namespace]
    assert result.state.citations


@pytest.mark.asyncio
async def test_multi_bin_retrieval_round_robin_and_dedup() -> None:
    bin_a = _make_bin(title="A")
    bin_b = _make_bin(title="B")
    duplicate_chunk_id = "duplicate-1"
    rows = {
        bin_a.vector_namespace: [
            _match(chunk_id=duplicate_chunk_id, bin_id=bin_a.id, source_name="doc-a", chunk_text="A1", score=0.91),
            _match(chunk_id="a-2", bin_id=bin_a.id, source_name="doc-a", chunk_text="A2", score=0.80),
        ],
        bin_b.vector_namespace: [
            _match(chunk_id=duplicate_chunk_id, bin_id=bin_b.id, source_name="doc-b", chunk_text="B1", score=0.89),
            _match(chunk_id="b-2", bin_id=bin_b.id, source_name="doc-b", chunk_text="B2", score=0.79),
        ],
    }
    vector_index = FakeVectorIndex(rows=rows)
    router = FakeLLMRouter(
        outputs={
            "retrieval_decision": ['{"decision":"retrieve","reason":"needs docs"}'],
            "relevance_grader": [
                '{"relevant":true,"score":0.9}',
                '{"relevant":true,"score":0.8}',
                '{"relevant":true,"score":0.7}',
            ],
            "answer_generator": ["grounded answer"],
            "hallucination_grader": ['{"grounded":true,"reason":"supported"}'],
        }
    )
    _, runtime = _runtime(router=router, vector_index=vector_index)

    result = await run_self_rag_graph(runtime=runtime, user_query="question", selected_bins=[bin_a, bin_b])

    assert set(vector_index.calls) == {bin_a.vector_namespace, bin_b.vector_namespace}
    assert len({doc.chunk_id for doc in result.state.retrieved_documents}) == len(result.state.retrieved_documents)
    assert [doc.chunk_id for doc in result.state.retrieved_documents] == [duplicate_chunk_id, "b-2", "a-2"]
    assert result.state.retrieval_mode.value == "grounded"


@pytest.mark.asyncio
async def test_relevance_threshold_routes_to_rewrite_and_caps() -> None:
    selected_bin = _make_bin(title="A")
    rows = {
        selected_bin.vector_namespace: [
            _match(chunk_id="a-1", bin_id=selected_bin.id, source_name="doc-a", chunk_text="A1", score=0.9),
            _match(chunk_id="a-2", bin_id=selected_bin.id, source_name="doc-a", chunk_text="A2", score=0.8),
        ]
    }
    vector_index = FakeVectorIndex(rows=rows)
    router = FakeLLMRouter(
        outputs={
            "retrieval_decision": ['{"decision":"retrieve","reason":"needs docs"}'],
            "relevance_grader": [
                '{"relevant":false,"score":0.2}',
                '{"relevant":false,"score":0.1}',
                '{"relevant":false,"score":0.2}',
                '{"relevant":false,"score":0.1}',
                '{"relevant":false,"score":0.2}',
                '{"relevant":false,"score":0.1}',
            ],
            "query_rewriter": [
                '{"rewritten_query":"r1"}',
                '{"rewritten_query":"r2"}',
                '{"rewritten_query":"r3"}',
            ],
            "answer_generator": ["fallback answer"],
            "hallucination_grader": ['{"grounded":true,"reason":"supported"}'],
        }
    )
    _, runtime = _runtime(router=router, vector_index=vector_index)

    result = await run_self_rag_graph(runtime=runtime, user_query="hard question", selected_bins=[selected_bin])

    assert result.state.rewrite_attempts == 3
    assert result.state.final_answer == "fallback answer"
    assert result.state.edge_transitions.count("relevance_grader:rewrite") == 3


@pytest.mark.asyncio
async def test_hallucination_retries_cap_at_two() -> None:
    selected_bin = _make_bin(title="A")
    rows = {
        selected_bin.vector_namespace: [
            _match(chunk_id="a-1", bin_id=selected_bin.id, source_name="doc-a", chunk_text="A1", score=0.9),
        ]
    }
    vector_index = FakeVectorIndex(rows=rows)
    router = FakeLLMRouter(
        outputs={
            "retrieval_decision": ['{"decision":"retrieve","reason":"needs docs"}'],
            "relevance_grader": ['{"relevant":true,"score":0.9}'],
            "answer_generator": ["first", "second", "third"],
            "hallucination_grader": [
                '{"grounded":false,"reason":"unsupported"}',
                '{"grounded":false,"reason":"unsupported"}',
                '{"grounded":false,"reason":"unsupported"}',
            ],
        }
    )
    _, runtime = _runtime(router=router, vector_index=vector_index)

    result = await run_self_rag_graph(runtime=runtime, user_query="question", selected_bins=[selected_bin])

    assert result.state.hallucination_retries == 2
    assert result.state.generation_attempts == 3
    assert result.state.final_answer == "third"


@pytest.mark.asyncio
async def test_router_error_typing_for_non_retryable_auth() -> None:
    selected_bin = _make_bin(title="A")
    vector_index = FakeVectorIndex(
        rows={
            selected_bin.vector_namespace: [
                _match(chunk_id="a-1", bin_id=selected_bin.id, source_name="doc-a", chunk_text="A1", score=0.9),
            ]
        }
    )
    router = FakeLLMRouter(
        outputs={
            "relevance_grader": ['{"relevant":true,"score":0.9}'],
        },
        errors={
            "answer_generator": RouterCallError(
                error_type=RouterErrorType.AUTH,
                message="bad auth",
                retryable=False,
            )
        },
    )
    _, runtime = _runtime(router=router, vector_index=vector_index)

    result = await run_self_rag_graph(runtime=runtime, user_query="question", selected_bins=[selected_bin])

    assert result.state.error is not None
    assert result.state.error.error_type == RouterErrorType.AUTH
    assert result.state.next_step == "abort"


@pytest.mark.asyncio
async def test_embedding_router_alignment_rejects_mismatch() -> None:
    bin_a = _make_bin(title="A", provider="deterministic", model="m1", dimensions=64)
    bin_b = _make_bin(title="B", provider="openai", model="m2", dimensions=1536)
    router = EmbeddingRouter(settings=Settings(), pipeline_config=SelfRagConfig())

    with pytest.raises(EmbeddingAlignmentError):
        router.resolve([bin_a, bin_b])
