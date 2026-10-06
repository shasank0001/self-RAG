from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import yaml

from app.core.pipeline_config import SelfRagConfig
from app.pipeline.graph import build_graph_runtime, run_self_rag_graph
from app.pipeline.nodes.research_executor import research_executor_node
from app.pipeline.nodes.research_planner import _normalize_plan, research_planner_node
from app.pipeline.nodes.research_synthesizer import research_synthesizer_node
from app.pipeline.prompts.registry import PromptRegistry
from app.pipeline.state import GraphState, ProviderMessage, ProviderTraceEntry, RetrievalMode, SelectedBin
from app.router.embedding_router import EmbeddingRouter
from app.router.llm_router import LLMRouter, RouterCallResult
from app.services.ingestion.vector_index import VectorIndex, VectorSearchMatch


def _make_bin(*, title: str) -> SelectedBin:
    return SelectedBin(
        id=uuid4(),
        title=title,
        vector_namespace=f"ns-{title.lower()}",
        embedding_provider="deterministic",
        embedding_model="deterministic-v1",
        embedding_dimensions=64,
    )


def _make_state(*, bins: list[SelectedBin], query: str = "How do renewals work?") -> GraphState:
    return GraphState(user_query=query, selected_bins=bins, research_mode=True)


class FakeLLMRouter(LLMRouter):
    def __init__(self, outputs: dict[str, list[str]] | None = None) -> None:
        self.outputs = {key: list(value) for key, value in (outputs or {}).items()}
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
    async def embed_query(self, *, query: str, selected_bins: list[SelectedBin]):
        return [0.1, 0.2], None


def _match(*, bin_id, chunk_id: str, text: str, score: float = 0.9) -> VectorSearchMatch:
    return VectorSearchMatch(
        id=chunk_id,
        score=score,
        metadata={
            "bin_id": str(bin_id),
            "chunk_id": chunk_id,
            "chunk_text": text,
            "source_name": "notes.txt",
        },
    )


class FakeVectorIndex(VectorIndex):
    def __init__(self, rows: dict[str, list[VectorSearchMatch]]) -> None:
        self.rows = rows
        self.calls: list[str] = []

    async def upsert(self, *, namespace: str, vectors) -> None:
        raise NotImplementedError

    async def delete_by_item_id(self, *, namespace: str, item_id) -> None:
        raise NotImplementedError

    async def query(self, *, namespace: str, vector: list[float], top_k: int) -> list[VectorSearchMatch]:
        self.calls.append(namespace)
        return self.rows.get(namespace, [])[:top_k]


class EmptyFirstVectorIndex(FakeVectorIndex):
    async def query(self, *, namespace: str, vector: list[float], top_k: int) -> list[VectorSearchMatch]:
        self.calls.append(namespace)
        if len(self.calls) <= 1:
            return []
        return self.rows.get(namespace, [])[:top_k]


def _runtime(*, router: FakeLLMRouter, index: FakeVectorIndex):
    config = SelfRagConfig()
    return build_graph_runtime(
        pipeline_config=config,
        router=router,  # type: ignore[arg-type]
        embedding_router=FakeEmbeddingRouter(),  # type: ignore[arg-type]
        vector_index=index,  # type: ignore[arg-type]
    )


def _registry() -> PromptRegistry:
    return PromptRegistry(pipeline_config=SelfRagConfig())


# --- planner unit tests -----------------------------------------------------


def test_normalize_plan_caps_dedupes_and_drops_echo() -> None:
    plan = _normalize_plan(
        ["  ", "What is X?", "WHAT IS X?", "How do renewals work?", "Renewal terms?", "Notice periods?", "Fees?"],
        original_query="How do renewals work?",
        max_questions=3,
    )
    assert plan == ["What is X?", "Renewal terms?", "Notice periods?"]


async def test_planner_empty_plan_falls_back_to_standard() -> None:
    bins = [_make_bin(title="policy")]
    router = FakeLLMRouter(outputs={"research_planner": [json.dumps({"sub_questions": []})]})
    result = await research_planner_node(
        _make_state(bins=bins),
        router=router,  # type: ignore[arg-type]
        prompt_registry=_registry(),
        pipeline_config=SelfRagConfig(),
    )
    assert result["research_mode"] is False
    assert result["research_plan"] == []
    assert "research_planner:empty_fallback" in result["edge_transitions"]


# --- executor unit tests ----------------------------------------------------


async def test_executor_fans_out_and_dedupes() -> None:
    bin_a = _make_bin(title="alpha")
    bin_b = _make_bin(title="beta")
    shared = _match(bin_id=bin_a.id, chunk_id="shared-1", text="shared evidence")
    index = FakeVectorIndex(
        rows={
            bin_a.vector_namespace: [shared, _match(bin_id=bin_a.id, chunk_id="a-2", text="alpha only", score=0.5)],
            bin_b.vector_namespace: [_match(bin_id=bin_b.id, chunk_id="b-1", text="beta only", score=0.7)],
        }
    )
    state = _make_state(bins=[bin_a, bin_b])
    state.research_plan = ["q1", "q2"]
    result = await research_executor_node(
        state,
        vector_index=index,  # type: ignore[arg-type]
        embedding_router=FakeEmbeddingRouter(),  # type: ignore[arg-type]
        pipeline_config=SelfRagConfig(),
        router=FakeLLMRouter(),  # type: ignore[arg-type]
        prompt_registry=_registry(),
    )
    chunk_ids = [item.chunk_id for item in result["relevant_documents"]]
    assert sorted(chunk_ids) == ["a-2", "b-1", "shared-1"]
    assert result["retrieval_mode"] == RetrievalMode.GROUNDED
    assert "research_executor:findings_ready" in result["edge_transitions"]
    # each sub-question queries every namespace
    assert len(index.calls) == 2 * len(state.research_plan)


async def test_executor_retry_hop_recovers_empty_question() -> None:
    bin_a = _make_bin(title="alpha")
    index = EmptyFirstVectorIndex(
        rows={bin_a.vector_namespace: [_match(bin_id=bin_a.id, chunk_id="a-1", text="late evidence")]}
    )
    router = FakeLLMRouter(outputs={"research_executor": [json.dumps({"rewritten_query": "renewal terms"})]})
    state = _make_state(bins=[bin_a])
    state.research_plan = ["q1"]
    result = await research_executor_node(
        state,
        vector_index=index,  # type: ignore[arg-type]
        embedding_router=FakeEmbeddingRouter(),  # type: ignore[arg-type]
        pipeline_config=SelfRagConfig(),
        router=router,  # type: ignore[arg-type]
        prompt_registry=_registry(),
    )
    assert [item.chunk_id for item in result["relevant_documents"]] == ["a-1"]
    assert result["research_hops_used"] == 1
    assert router.calls == ["research_executor"]


async def test_executor_empty_everywhere_falls_back() -> None:
    bins = [_make_bin(title="alpha")]
    state = _make_state(bins=bins)
    state.research_plan = ["q1"]
    result = await research_executor_node(
        state,
        vector_index=FakeVectorIndex(rows={}),  # type: ignore[arg-type]
        embedding_router=FakeEmbeddingRouter(),  # type: ignore[arg-type]
        pipeline_config=SelfRagConfig(),
        router=FakeLLMRouter(
            outputs={"research_executor": [json.dumps({"rewritten_query": "same"})]}
        ),  # type: ignore[arg-type]
        prompt_registry=_registry(),
    )
    assert result["research_mode"] is False
    assert result["relevant_documents"] == []
    assert "research_executor:empty_fallback" in result["edge_transitions"]


# --- synthesizer unit test --------------------------------------------------


async def test_synthesizer_builds_grounded_report() -> None:
    bins = [_make_bin(title="alpha")]
    router = FakeLLMRouter(outputs={"research_synthesizer": ["## Report\n\nClaim [1]."]})
    state = _make_state(bins=bins)
    state.research_plan = ["q1"]
    from app.pipeline.state import RetrievedDocument

    state.relevant_documents = [
        RetrievedDocument(
            chunk_id="a-1",
            chunk_text="renewals need 30 days notice",
            item_name="notes.txt",
            bin_id=bins[0].id,
            bin_title="alpha",
            score=0.9,
        )
    ]
    result = await research_synthesizer_node(
        state,
        router=router,  # type: ignore[arg-type]
        prompt_registry=_registry(),
        pipeline_config=SelfRagConfig(),
    )
    assert result["retrieval_mode"] == RetrievalMode.GROUNDED
    assert result["final_answer"].startswith("## Report")
    assert result["citations"][0].chunk_id == "a-1"
    assert result["prompt_versions"]["research_synthesizer"] == "v1"


# --- full-graph tests -------------------------------------------------------


async def test_full_graph_research_happy_path() -> None:
    bin_a = _make_bin(title="alpha")
    router = FakeLLMRouter(
        outputs={
            "research_planner": [json.dumps({"sub_questions": ["renewal terms?", "notice periods?"]})],
            "research_synthesizer": ["## Report\n\nTerms [1] and notice [2]."],
            "hallucination_grader": [json.dumps({"grounded": True})],
        }
    )
    index = FakeVectorIndex(
        rows={
            bin_a.vector_namespace: [
                _match(bin_id=bin_a.id, chunk_id="a-1", text="terms evidence", score=0.9),
                _match(bin_id=bin_a.id, chunk_id="a-2", text="notice evidence", score=0.8),
            ]
        }
    )
    result = await run_self_rag_graph(
        runtime=_runtime(router=router, index=index),
        user_query="How do renewals work?",
        selected_bins=[bin_a],
        research_mode=True,
    )
    state = result.state
    assert state.retrieval_mode == RetrievalMode.GROUNDED
    assert state.research_plan == ["renewal terms?", "notice periods?"]
    assert len(state.citations) == 2
    assert "research_planner:plan_ready" in state.edge_transitions
    assert "research_executor:findings_ready" in state.edge_transitions
    assert "research_synthesizer:grounded" in state.edge_transitions
    assert "answer_generator" not in router.calls


async def test_full_graph_research_regenerate_returns_to_synthesizer() -> None:
    bin_a = _make_bin(title="alpha")
    router = FakeLLMRouter(
        outputs={
            "research_planner": [json.dumps({"sub_questions": ["renewal terms?"]})],
            "research_synthesizer": ["First draft.", "Second draft."],
            "hallucination_grader": [
                json.dumps({"grounded": False, "reason": "unsupported claim"}),
                json.dumps({"grounded": True}),
            ],
        }
    )
    index = FakeVectorIndex(
        rows={bin_a.vector_namespace: [_match(bin_id=bin_a.id, chunk_id="a-1", text="terms evidence")]}
    )
    result = await run_self_rag_graph(
        runtime=_runtime(router=router, index=index),
        user_query="How do renewals work?",
        selected_bins=[bin_a],
        research_mode=True,
    )
    assert result.state.final_answer == "Second draft."
    assert router.calls.count("research_synthesizer") == 2
    assert "answer_generator" not in router.calls


async def test_full_graph_empty_plan_falls_back_to_standard() -> None:
    bin_a = _make_bin(title="alpha")
    router = FakeLLMRouter(
        outputs={
            "research_planner": [json.dumps({"sub_questions": []})],
            "retrieval_decision": [json.dumps({"decision": "skip"})],
            "answer_generator": ["Direct answer."],
        }
    )
    result = await run_self_rag_graph(
        runtime=_runtime(router=router, index=FakeVectorIndex(rows={})),
        user_query="How do renewals work?",
        selected_bins=[bin_a],
        research_mode=True,
    )
    assert result.state.research_mode is False
    assert result.state.retrieval_mode == RetrievalMode.PARAMETRIC
    assert result.state.final_answer == "Direct answer."


async def test_research_flag_defaults_off_and_config_bounds() -> None:
    state = GraphState(user_query="hi", selected_bins=[])
    assert state.research_mode is False
    assert state.research_plan == []
    config = SelfRagConfig()
    assert config.pipeline.research.max_sub_questions == 3
    assert config.pipeline.research.max_hops == 1


def test_config_yaml_parses_with_research_section() -> None:
    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config = SelfRagConfig.model_validate(payload)
    assert config.pipeline.research.max_sub_questions == 3
    assert config.llm.nodes["research_synthesizer"].provider == "openrouter"
    assert config.prompts.nodes["research_planner"].version == "v1"


def test_research_prompt_templates_render() -> None:
    registry = PromptRegistry(pipeline_config=SelfRagConfig())
    planner = registry.get("research_planner").template.format(
        query="q", bin_titles="alpha", max_questions=3
    )
    assert "sub_questions" in planner
    synthesizer = registry.get("research_synthesizer").template.format(
        query="q", context="[1] alpha | notes.txt | a-1\ntext", feedback=""
    )
    assert "[n]" in synthesizer or "citation" in synthesizer.lower()
