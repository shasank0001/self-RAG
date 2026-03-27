from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.core.pipeline_config import SelfRagConfig
from app.pipeline.graph import build_graph_runtime, run_self_rag_graph
from app.pipeline.nodes.relevance_grader import relevance_grader_node
from app.pipeline.nodes.retrieval import retrieval_node
from app.pipeline.nodes.retrieval_decision import retrieval_decision_node
from app.pipeline.prompts.registry import PromptRegistry
from app.pipeline.state import GraphState, ProviderMessage, ProviderTraceEntry, RetrievedDocument, RetrievalMode, RouterErrorType, SelectedBin
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


class EmptyThenHitVectorIndex(VectorIndex):
    def __init__(self, *, first_rows: dict[str, list[VectorSearchMatch]], second_rows: dict[str, list[VectorSearchMatch]]) -> None:
        self.first_rows = first_rows
        self.second_rows = second_rows
        self.calls: list[str] = []

    async def upsert(self, *, namespace: str, vectors: list[VectorPayload]) -> None:
        raise NotImplementedError

    async def delete_by_item_id(self, *, namespace: str, item_id) -> None:
        raise NotImplementedError

    async def query(self, *, namespace: str, vector: list[float], top_k: int) -> list[VectorSearchMatch]:
        self.calls.append(namespace)
        rows = self.first_rows if len(self.calls) == 1 else self.second_rows
        return rows.get(namespace, [])[:top_k]


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


def _doc(*, chunk_id: str, chunk_text: str, score: float) -> RetrievedDocument:
    return RetrievedDocument(
        chunk_id=chunk_id,
        chunk_text=chunk_text,
        item_id=uuid4(),
        item_name=f"{chunk_id}.txt",
        bin_id=uuid4(),
        bin_title="Bin",
        score=score,
        metadata={},
    )


def _runtime(*, router: FakeLLMRouter, vector_index: VectorIndex) -> tuple[SelfRagConfig, object]:
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
            "retrieval_decision": ['{"decision":"retrieve","reason":"use selected bin"}'],
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
async def test_relevance_grader_reranks_by_score_and_citations_use_grader_scores() -> None:
    selected_bin = _make_bin(title="A")
    vector_index = FakeVectorIndex(
        rows={
            selected_bin.vector_namespace: [
                _match(chunk_id="a-low", bin_id=selected_bin.id, source_name="doc-a", chunk_text="chunk-low", score=0.95),
                _match(chunk_id="a-high", bin_id=selected_bin.id, source_name="doc-a", chunk_text="chunk-high", score=0.60),
            ]
        }
    )
    router = FakeLLMRouter(
        outputs={
            "retrieval_decision": ['{"decision":"retrieve","reason":"needs docs"}'],
            "relevance_grader": [
                '{"relevant":true,"score":0.2}',
                '{"relevant":true,"score":0.9}',
            ],
            "answer_generator": ["grounded answer"],
            "hallucination_grader": ['{"grounded":true,"reason":"supported"}'],
        }
    )
    _, runtime = _runtime(router=router, vector_index=vector_index)

    result = await run_self_rag_graph(runtime=runtime, user_query="question", selected_bins=[selected_bin])

    assert [doc.chunk_id for doc in result.state.relevant_documents] == ["a-high", "a-low"]
    assert [citation.chunk_id for citation in result.state.citations] == ["a-high", "a-low"]
    assert [citation.score for citation in result.state.citations] == [0.9, 0.2]


@pytest.mark.asyncio
async def test_retrieval_decision_uses_llm_and_records_skip_trace() -> None:
    selected_bin = _make_bin(title="A")
    state = PromptRegistry(pipeline_config=SelfRagConfig())
    router = FakeLLMRouter(outputs={"retrieval_decision": ['{"decision":"skip","reason":"no docs needed"}']})

    result = await retrieval_decision_node(
        type(
            "State",
            (),
            {
                "selected_bins": [selected_bin],
                "active_query": "What is 2+2?",
                "provider_trace": [],
                "prompt_versions": {},
                "edge_transitions": [],
            },
        )(),
        router=router,
        prompt_registry=state,
    )

    assert result["next_step"].value == "skip"
    assert result["retrieval_mode"].value == "parametric"
    assert result["edge_transitions"] == ["retrieval_decision:skip"]
    assert result["provider_trace"][0].node_name == "retrieval_decision"
    assert result["prompt_versions"]["retrieval_decision"] == "v1"


@pytest.mark.asyncio
async def test_relevance_grader_routes_by_average_score_threshold() -> None:
    documents = [
        _doc(chunk_id="doc-1", chunk_text="chunk-one", score=0.95),
        _doc(chunk_id="doc-2", chunk_text="chunk-two", score=0.75),
    ]
    router = FakeLLMRouter(
        outputs={
            "relevance_grader": [
                '{"relevant":true,"score":0.2}',
                '{"relevant":true,"score":0.9}',
            ],
        }
    )
    prompt_registry = PromptRegistry(pipeline_config=SelfRagConfig())
    pipeline_config = SelfRagConfig()
    state = GraphState(user_query="question", retrieved_documents=documents)

    result = await relevance_grader_node(
        state,
        router=router,
        prompt_registry=prompt_registry,
        pipeline_config=pipeline_config,
    )

    assert result["next_step"] == "generate"
    assert [doc.chunk_id for doc in result["relevant_documents"]] == ["doc-2", "doc-1"]
    assert result["relevance_scores"] == {"doc-1": 0.2, "doc-2": 0.9}


@pytest.mark.asyncio
async def test_relevance_grader_rewrites_when_average_score_is_low() -> None:
    documents = [
        _doc(chunk_id="doc-1", chunk_text="chunk-one", score=0.95),
        _doc(chunk_id="doc-2", chunk_text="chunk-two", score=0.75),
        _doc(chunk_id="doc-3", chunk_text="chunk-three", score=0.55),
    ]
    router = FakeLLMRouter(
        outputs={
            "relevance_grader": [
                '{"relevant":true,"score":0.9}',
                '{"relevant":true,"score":0.2}',
                '{"relevant":true,"score":0.1}',
            ],
        }
    )
    prompt_registry = PromptRegistry(pipeline_config=SelfRagConfig())
    pipeline_config = SelfRagConfig()
    state = GraphState(user_query="question", retrieved_documents=documents)

    result = await relevance_grader_node(
        state,
        router=router,
        prompt_registry=prompt_registry,
        pipeline_config=pipeline_config,
    )

    assert result["next_step"] == "rewrite"
    assert [doc.chunk_id for doc in result["relevant_documents"]] == ["doc-1", "doc-2", "doc-3"]


@pytest.mark.asyncio
async def test_relevance_grader_executes_document_calls_concurrently() -> None:
    documents = [
        _doc(chunk_id="doc-1", chunk_text="chunk-one", score=0.95),
        _doc(chunk_id="doc-2", chunk_text="chunk-two", score=0.75),
        _doc(chunk_id="doc-3", chunk_text="chunk-three", score=0.55),
    ]
    prompt_registry = PromptRegistry(pipeline_config=SelfRagConfig())
    pipeline_config = SelfRagConfig()
    state = GraphState(user_query="question", retrieved_documents=documents)

    class SlowRouter(LLMRouter):
        def __init__(self) -> None:
            self.current = 0
            self.max_in_flight = 0

        async def call_node(
            self,
            *,
            node_name: str,
            messages: list[ProviderMessage],
            temperature: float = 0.0,
            timeout_ms: int | None = None,
        ) -> RouterCallResult:
            del node_name
            del temperature
            del timeout_ms
            del messages
            self.current += 1
            self.max_in_flight = max(self.max_in_flight, self.current)
            await asyncio.sleep(0.01)
            self.current -= 1
            return RouterCallResult(
                provider="fake",
                model="fake-model",
                content='{"relevant":true,"score":0.8}',
                raw_payload={},
                trace=[
                    ProviderTraceEntry(
                        node_name="relevance_grader",
                        provider="fake",
                        model="fake-model",
                        attempt=1,
                        success=True,
                        duration_ms=1.0,
                    )
                ],
            )

    router = SlowRouter()
    result = await relevance_grader_node(
        state,
        router=router,
        prompt_registry=prompt_registry,
        pipeline_config=pipeline_config,
    )

    assert router.max_in_flight > 1
    assert len(result["provider_trace"]) == 3


@pytest.mark.asyncio
async def test_bins_selected_can_skip_retrieval_and_answer_parametrically() -> None:
    selected_bin = _make_bin(title="A")
    router = FakeLLMRouter(
        outputs={
            "retrieval_decision": ['{"decision":"skip","reason":"general knowledge"}'],
            "answer_generator": ["parametric answer"],
        }
    )
    vector_index = FakeVectorIndex(rows={})
    _, runtime = _runtime(router=router, vector_index=vector_index)

    result = await run_self_rag_graph(runtime=runtime, user_query="What is the capital of France?", selected_bins=[selected_bin])

    assert result.state.retrieval_mode.value == "parametric"
    assert result.state.citations == []
    assert vector_index.calls == []
    assert router.calls == ["retrieval_decision", "answer_generator"]


@pytest.mark.asyncio
async def test_retrieval_skips_malformed_item_id_metadata_instead_of_aborting() -> None:
    selected_bin = _make_bin(title="A")
    vector_index = FakeVectorIndex(
        rows={
            selected_bin.vector_namespace: [
                VectorSearchMatch(
                    id="bad-item",
                    score=0.9,
                    metadata={
                        "bin_id": str(selected_bin.id),
                        "item_id": "not-a-uuid",
                        "source_name": "doc-a",
                        "chunk_id": "chunk-1",
                        "chunk_text": "Text",
                    },
                )
            ]
        }
    )

    class StubEmbeddingRouter:
        async def embed_query(self, *, query: str, selected_bins: list[SelectedBin]):
            del query
            del selected_bins
            return [0.1, 0.2], type("Resolution", (), {"provider": "deterministic", "model": "v1", "dimensions": 64})()

    state = GraphState(
        user_query="question",
        selected_bins=[selected_bin],
        retrieval_mode=RetrievalMode.GROUNDED,
    )

    result = await retrieval_node(
        state,
        vector_index=vector_index,
        embedding_router=StubEmbeddingRouter(),
        pipeline_config=SelfRagConfig(),
    )

    assert result["retrieved_documents"][0].item_id is None
    assert result["next_step"] == "generate"


@pytest.mark.asyncio
async def test_duplicate_selected_bin_namespaces_do_not_silently_drop_queries() -> None:
    shared_namespace = "shared-ns"
    bin_a = SelectedBin(
        id=uuid4(),
        title="A",
        vector_namespace=shared_namespace,
        embedding_provider="deterministic",
        embedding_model="deterministic-v1",
        embedding_dimensions=64,
    )
    bin_b = SelectedBin(
        id=uuid4(),
        title="B",
        vector_namespace=shared_namespace,
        embedding_provider="deterministic",
        embedding_model="deterministic-v1",
        embedding_dimensions=64,
    )

    class SharedNamespaceVectorIndex(VectorIndex):
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def upsert(self, *, namespace: str, vectors: list[VectorPayload]) -> None:
            raise NotImplementedError

        async def delete_by_item_id(self, *, namespace: str, item_id) -> None:
            raise NotImplementedError

        async def query(self, *, namespace: str, vector: list[float], top_k: int) -> list[VectorSearchMatch]:
            del vector
            del top_k
            self.calls.append(namespace)
            if len(self.calls) == 1:
                return [
                    _match(chunk_id="chunk-a", bin_id=bin_a.id, source_name="doc-a", chunk_text="A1", score=0.9),
                ]
            return [
                _match(chunk_id="chunk-b", bin_id=bin_b.id, source_name="doc-b", chunk_text="B1", score=0.8),
            ]

    class StubEmbeddingRouter:
        async def embed_query(self, *, query: str, selected_bins: list[SelectedBin]):
            del query
            del selected_bins
            return [0.1, 0.2], type("Resolution", (), {"provider": "deterministic", "model": "v1", "dimensions": 64})()

    vector_index = SharedNamespaceVectorIndex()
    state = GraphState(
        user_query="question",
        selected_bins=[bin_a, bin_b],
        retrieval_mode=RetrievalMode.GROUNDED,
    )

    result = await retrieval_node(
        state,
        vector_index=vector_index,
        embedding_router=StubEmbeddingRouter(),
        pipeline_config=SelfRagConfig(),
    )

    assert vector_index.calls == [shared_namespace, shared_namespace]
    assert [doc.chunk_id for doc in result["retrieved_documents"]] == ["chunk-a", "chunk-b"]


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
async def test_empty_retrieval_retries_and_can_recover_documents() -> None:
    selected_bin = _make_bin(title="A")
    recovered_rows = {
        selected_bin.vector_namespace: [
            _match(chunk_id="a-1", bin_id=selected_bin.id, source_name="doc-a", chunk_text="Recovered", score=0.9),
        ]
    }
    vector_index = EmptyThenHitVectorIndex(first_rows={}, second_rows=recovered_rows)
    router = FakeLLMRouter(
        outputs={
            "retrieval_decision": ['{"decision":"retrieve","reason":"needs docs"}'],
            "query_rewriter": ['{"rewritten_query":"retry query"}'],
            "relevance_grader": ['{"relevant":true,"score":0.9}'],
            "answer_generator": ["grounded answer"],
            "hallucination_grader": ['{"grounded":true,"reason":"supported"}'],
        }
    )
    _, runtime = _runtime(router=router, vector_index=vector_index)

    result = await run_self_rag_graph(runtime=runtime, user_query="question", selected_bins=[selected_bin])

    assert vector_index.calls == [selected_bin.vector_namespace, selected_bin.vector_namespace]
    assert result.state.rewrite_attempts == 1
    assert result.state.retrieval_mode.value == "grounded"
    assert [doc.chunk_id for doc in result.state.retrieved_documents] == ["a-1"]
    assert "retrieval:empty" in result.state.edge_transitions
    assert "query_rewriter:retrieve" in result.state.edge_transitions
    assert result.state.rewritten_query == "retry query"


@pytest.mark.asyncio
async def test_empty_retrieval_caps_and_falls_back_to_parametric_answer() -> None:
    selected_bin = _make_bin(title="A")
    vector_index = FakeVectorIndex(rows={})
    router = FakeLLMRouter(
        outputs={
            "retrieval_decision": ['{"decision":"retrieve","reason":"needs docs"}'],
            "query_rewriter": [
                '{"rewritten_query":"r1"}',
                '{"rewritten_query":"r2"}',
                '{"rewritten_query":"r3"}',
            ],
            "answer_generator": ["fallback answer"],
        }
    )
    _, runtime = _runtime(router=router, vector_index=vector_index)

    result = await run_self_rag_graph(runtime=runtime, user_query="question", selected_bins=[selected_bin])

    assert vector_index.calls == [
        selected_bin.vector_namespace,
        selected_bin.vector_namespace,
        selected_bin.vector_namespace,
    ]
    assert result.state.rewrite_attempts == 3
    assert result.state.retrieval_mode.value == "parametric"
    assert result.state.citations == []
    assert result.state.final_answer == "fallback answer"


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
            "retrieval_decision": ['{"decision":"retrieve","reason":"needs docs"}'],
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
