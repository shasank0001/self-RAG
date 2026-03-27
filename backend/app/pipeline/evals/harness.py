from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from uuid import NAMESPACE_DNS, UUID, uuid4, uuid5

from app.core.config import Settings
from app.pipeline.graph import GraphExecutionResult, build_graph_runtime, run_self_rag_graph
from app.pipeline.state import Citation, GraphState, ProviderTraceEntry, RetrievalMode, SelectedBin
from app.pipeline.state import ProviderMessage
from app.router.llm_router import LLMRouter, RouterCallResult
from app.services.ingestion.vector_index import VectorIndex, VectorPayload, VectorSearchMatch


@dataclass(slots=True)
class EvalCase:
    id: str
    query: str
    bins: list[str]
    expected_retrieval_mode: str
    expected_citation_chunk_ids: list[str]
    expected_min_grounding_score: float
    expected_min_relevance_precision: float
    expected_min_retrieval_hit_rate: float


@dataclass(slots=True)
class EvalResult:
    case_id: str
    retrieval_mode: str
    citation_chunk_ids: list[str]
    retrieval_hit_rate: float
    relevance_precision: float
    grounding_score: float
    passed: bool
    degraded_node_paths: list[str]
    degraded_provider_paths: list[str]
    prompt_versions: dict[str, str]


@dataclass(slots=True)
class Phase4EvalReport:
    totals: dict[str, int]
    results: list[dict]

    def to_json(self) -> str:
        return json.dumps(
            {
                "totals": self.totals,
                "results": self.results,
            },
            indent=2,
        )


def load_eval_cases(dataset_path: Path) -> list[EvalCase]:
    payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    return [EvalCase(**item) for item in payload]


def _set_overlap_score(*, expected_ids: set[str], actual_ids: set[str]) -> tuple[float, float, float]:
    if not expected_ids and not actual_ids:
        return (1.0, 1.0, 1.0)
    if not actual_ids:
        return (0.0, 0.0, 0.0)
    if not expected_ids:
        return (0.0, 0.0, 0.0)

    overlap = len(expected_ids.intersection(actual_ids))
    recall = overlap / len(expected_ids)
    precision = overlap / len(actual_ids)
    if precision + recall == 0.0:
        f1 = 0.0
    else:
        f1 = (2 * precision * recall) / (precision + recall)
    return (recall, precision, f1)


def score_eval_case(case: EvalCase, result: GraphExecutionResult) -> EvalResult:
    state = result.state
    actual_mode = state.retrieval_mode.value if state.retrieval_mode is not None else RetrievalMode.PARAMETRIC.value
    chunk_ids = [item.chunk_id for item in state.citations]

    expected_ids = set(case.expected_citation_chunk_ids)
    actual_ids = set(chunk_ids)
    retrieval_hit_rate, relevance_precision, grounding_score = _set_overlap_score(
        expected_ids=expected_ids,
        actual_ids=actual_ids,
    )

    passed = (
        actual_mode == case.expected_retrieval_mode
        and
        retrieval_hit_rate >= case.expected_min_retrieval_hit_rate
        and relevance_precision >= case.expected_min_relevance_precision
        and grounding_score >= case.expected_min_grounding_score
    )

    degraded_node_paths: list[str] = []
    degraded_provider_paths: list[str] = []
    if not passed:
        degraded_node_paths = [item for item in state.edge_transitions]
        degraded_provider_paths = [f"{item.node_name}:{item.provider}:{item.model}" for item in state.provider_trace]

    return EvalResult(
        case_id=case.id,
        retrieval_mode=actual_mode,
        citation_chunk_ids=chunk_ids,
        retrieval_hit_rate=retrieval_hit_rate,
        relevance_precision=relevance_precision,
        grounding_score=grounding_score,
        passed=passed,
        degraded_node_paths=degraded_node_paths,
        degraded_provider_paths=degraded_provider_paths,
        prompt_versions=dict(state.prompt_versions),
    )


def build_report(results: list[EvalResult]) -> Phase4EvalReport:
    totals = {
        "total": len(results),
        "passed": sum(1 for item in results if item.passed),
        "failed": sum(1 for item in results if not item.passed),
    }
    details = [
        {
            "case_id": item.case_id,
            "retrieval_mode": item.retrieval_mode,
            "citation_chunk_ids": item.citation_chunk_ids,
            "retrieval_hit_rate": item.retrieval_hit_rate,
            "relevance_precision": item.relevance_precision,
            "grounding_score": item.grounding_score,
            "passed": item.passed,
            "degraded_node_paths": item.degraded_node_paths,
            "degraded_provider_paths": item.degraded_provider_paths,
            "prompt_versions": item.prompt_versions,
        }
        for item in results
    ]
    return Phase4EvalReport(totals=totals, results=details)


def run_phase4_evaluation(dataset_path: Path, runner) -> Phase4EvalReport:
    cases = load_eval_cases(dataset_path)
    results = [score_eval_case(case, runner(case)) for case in cases]
    return build_report(results)


class OfflineEvalVectorIndex(VectorIndex):
    def __init__(self, rows: dict[str, list[VectorSearchMatch]]) -> None:
        self._rows = rows

    async def upsert(self, *, namespace: str, vectors: list[VectorPayload]) -> None:
        return None

    async def delete_by_item_id(self, *, namespace: str, item_id: UUID) -> None:
        return None

    async def query(self, *, namespace: str, vector: list[float], top_k: int) -> list[VectorSearchMatch]:
        return list(self._rows.get(namespace, []))[:top_k]


class OfflineEvalRouter(LLMRouter):
    def __init__(self, case: EvalCase) -> None:
        self._case = case

    async def call_node(
        self,
        *,
        node_name: str,
        messages: list[ProviderMessage],
        temperature: float = 0.0,
        timeout_ms: int | None = None,
    ) -> RouterCallResult:
        del temperature
        del timeout_ms
        del messages

        if node_name == "retrieval_decision":
            if self._case.expected_retrieval_mode == RetrievalMode.PARAMETRIC.value:
                content = '{"decision":"skip","reason":"parametric query"}'
            else:
                content = '{"decision":"retrieve","reason":"requires document evidence"}'
        elif node_name == "relevance_grader":
            content = '{"relevant":true,"score":1.0}'
        elif node_name == "query_rewriter":
            content = json.dumps({"rewritten_query": self._case.query})
        elif node_name == "answer_generator":
            if self._case.expected_retrieval_mode == RetrievalMode.GROUNDED.value:
                content = "The uploaded policy lists obligations in section 3."
            else:
                content = "Paris is the capital of France."
        elif node_name == "hallucination_grader":
            content = '{"grounded":true,"reason":"answer is supported"}'
        else:
            content = "{}"

        return RouterCallResult(
            provider="offline-eval",
            model="offline-eval-v1",
            content=content,
            raw_payload={"content": content, "node": node_name},
            trace=[
                ProviderTraceEntry(
                    node_name=node_name,
                    provider="offline-eval",
                    model="offline-eval-v1",
                    attempt=1,
                    success=True,
                    duration_ms=1.0,
                )
            ],
        )


def _build_eval_bins(case: EvalCase) -> list[SelectedBin]:
    bins: list[SelectedBin] = []
    for item in case.bins:
        bin_id = uuid5(NAMESPACE_DNS, f"phase4-eval-bin:{item}")
        bins.append(
            SelectedBin(
                id=bin_id,
                title=item,
                vector_namespace=f"eval-{item}",
                embedding_provider="deterministic",
                embedding_model="deterministic-v1",
                embedding_dimensions=64,
            )
        )
    return bins


def _build_eval_matches(case: EvalCase, bins: list[SelectedBin]) -> dict[str, list[VectorSearchMatch]]:
    rows: dict[str, list[VectorSearchMatch]] = {}
    if case.expected_retrieval_mode != RetrievalMode.GROUNDED.value:
        return rows

    chunk_ids = case.expected_citation_chunk_ids or ["eval-chunk-1"]
    for bin_item in bins:
        rows[bin_item.vector_namespace] = [
            VectorSearchMatch(
                id=chunk_id,
                score=1.0 - (index * 0.05),
                metadata={
                    "bin_id": str(bin_item.id),
                    "item_id": str(uuid4()),
                    "source_name": f"{bin_item.title}.txt",
                    "chunk_id": chunk_id,
                    "chunk_text": "Policy obligations include compliance, notice, and renewal steps.",
                },
            )
            for index, chunk_id in enumerate(chunk_ids)
        ]
    return rows


def build_offline_graph_result(case: EvalCase) -> GraphExecutionResult:
    async def _run() -> GraphExecutionResult:
        selected_bins = _build_eval_bins(case)
        vector_index = OfflineEvalVectorIndex(_build_eval_matches(case, selected_bins))
        runtime = build_graph_runtime(
            settings=Settings(VECTOR_INDEX_BACKEND="memory"),
            router=OfflineEvalRouter(case),
            vector_index=vector_index,
        )
        return await run_self_rag_graph(
            runtime=runtime,
            user_query=case.query,
            selected_bins=selected_bins,
        )

    return asyncio.run(_run())


def build_stub_graph_result(case: EvalCase) -> GraphExecutionResult:
    mode = RetrievalMode(case.expected_retrieval_mode)
    citations = [
        Citation(
            item_name="doc.txt",
            chunk_excerpt="excerpt",
            bin_title="policy",
            chunk_id=item,
            score=0.95,
        )
        for item in case.expected_citation_chunk_ids
    ]

    prompt_versions = {
        "retrieval_decision": "v1",
        "answer_generator" if mode == RetrievalMode.GROUNDED else "answer_generator_parametric": "v1",
    }

    state = GraphState(
        user_query=case.query,
        selected_bins=[
            SelectedBin(
                id=uuid4(),
                title=bin_name,
                vector_namespace=f"ns-{bin_name}",
                embedding_provider="deterministic",
                embedding_model="deterministic-v1",
                embedding_dimensions=64,
            )
            for bin_name in case.bins
        ],
        selected_bin_ids=[],
        retrieval_mode=mode,
        citations=citations,
        edge_transitions=["retrieval_decision:retrieve" if mode == RetrievalMode.GROUNDED else "answer_generator:parametric"],
        provider_trace=[
            ProviderTraceEntry(
                node_name="answer_generator",
                provider="deterministic",
                model="deterministic-model",
                attempt=1,
                success=True,
                duration_ms=1.0,
            )
        ],
        prompt_versions=prompt_versions,
        final_answer="answer",
    )
    return GraphExecutionResult(state=state)
