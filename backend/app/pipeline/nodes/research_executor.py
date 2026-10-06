from __future__ import annotations

import asyncio
import logging

from app.core.pipeline_config import SelfRagConfig
from app.core.tracing import start_span
from app.observability.events import LogEvent
from app.observability.metrics import get_metrics_registry
from app.pipeline.nodes.retrieval import _round_robin_merge, _to_retrieved_document
from app.pipeline.prompts.registry import PromptRegistry
from app.pipeline.state import GraphState, NodeOutcome, ProviderMessage, QueryRewritePayload, RetrievedDocument, RetrievalMode, SelectedBin
from app.pipeline.utils import parse_model_from_text
from app.router.embedding_router import EmbeddingRouter
from app.router.llm_router import LLMRouter
from app.services.ingestion.vector_index import VectorIndex

logger = logging.getLogger(__name__)
metrics = get_metrics_registry()


async def _retrieve_for_question(
    *,
    question: str,
    selected_bins: list[SelectedBin],
    embedding_router: EmbeddingRouter,
    vector_index: VectorIndex,
    top_k: int,
    merged_top_n: int,
) -> tuple[list[RetrievedDocument], object | None]:
    query_vector, resolution = await embedding_router.embed_query(query=question, selected_bins=selected_bins)
    bin_lookup = {str(item.id): item for item in selected_bins}

    async def _query_namespace(bin_item: SelectedBin) -> tuple[str, list[RetrievedDocument]]:
        matches = await vector_index.query(namespace=bin_item.vector_namespace, vector=query_vector, top_k=top_k)
        normalized: list[RetrievedDocument] = []
        for match in matches:
            document = _to_retrieved_document(match, bin_lookup)
            if document is None:
                continue
            normalized.append(document)
        normalized.sort(key=lambda item: item.score, reverse=True)
        return (f"{bin_item.vector_namespace}:{bin_item.id}", normalized)

    queried = await asyncio.gather(*[_query_namespace(item) for item in selected_bins])
    grouped = {group_key: normalized for group_key, normalized in queried}
    merged = _round_robin_merge(grouped_matches=grouped, merged_top_n=merged_top_n)
    return merged, resolution


async def research_executor_node(
    state: GraphState,
    *,
    vector_index: VectorIndex,
    embedding_router: EmbeddingRouter,
    pipeline_config: SelfRagConfig,
    router: LLMRouter,
    prompt_registry: PromptRegistry,
) -> dict:
    research = pipeline_config.pipeline.research
    top_k = pipeline_config.pipeline.retrieval.top_k_per_namespace
    merged_top_n = pipeline_config.pipeline.retrieval.merged_top_n

    logger.info(LogEvent.RETRIEVAL_STARTED, extra={"sub_questions": len(state.research_plan)})
    hops_used = 0
    hops_lock = asyncio.Lock()
    rewrite_spec = prompt_registry.get("query_rewriter")

    async def _run_sub_question(question: str) -> list[RetrievedDocument]:
        nonlocal hops_used
        merged, _ = await _retrieve_for_question(
            question=question,
            selected_bins=state.selected_bins,
            embedding_router=embedding_router,
            vector_index=vector_index,
            top_k=top_k,
            merged_top_n=merged_top_n,
        )
        if merged:
            return merged
        async with hops_lock:
            if hops_used >= research.max_hops * max(len(state.research_plan), 1):
                return []
        rewrite_prompt = rewrite_spec.template.format(query=question)
        rewrite_response = await router.call_node(
            node_name="research_executor",
            messages=[
                ProviderMessage(role="system", content=rewrite_prompt),
                ProviderMessage(role="user", content=question),
            ],
        )
        rewrite_payload = parse_model_from_text(rewrite_response.content, QueryRewritePayload)
        rewritten = rewrite_payload.rewritten_query.strip() or question
        async with hops_lock:
            hops_used += 1
        retried, _ = await _retrieve_for_question(
            question=rewritten,
            selected_bins=state.selected_bins,
            embedding_router=embedding_router,
            vector_index=vector_index,
            top_k=top_k,
            merged_top_n=merged_top_n,
        )
        return retried

    with start_span(
        "graph.research_executor",
        attributes={"sub_questions": len(state.research_plan), "bin_count": len(state.selected_bins)},
    ):
        per_question = await asyncio.gather(*[_run_sub_question(item) for item in state.research_plan])

    seen_chunk_ids: set[str] = set()
    findings: list[RetrievedDocument] = []
    for docs in per_question:
        for item in docs:
            if item.chunk_id in seen_chunk_ids:
                continue
            seen_chunk_ids.add(item.chunk_id)
            findings.append(item)
    findings.sort(key=lambda item: item.score, reverse=True)
    capped = findings[: max(research.synthesizer_max_docs, 1)]

    used_bins = [bin_item.id for bin_item in state.selected_bins]
    metrics.observe("selfrag_research_docs_count", float(len(capped)), mode=RetrievalMode.GROUNDED.value)
    logger.info(LogEvent.RETRIEVAL_COMPLETED, extra={"document_count": len(capped)})

    if not capped:
        return {
            "research_mode": False,
            "research_hops_used": hops_used,
            "retrieved_documents": [],
            "relevant_documents": [],
            "relevance_scores": {},
            "bin_ids_used": used_bins,
            "next_step": NodeOutcome.GENERATE,
            "edge_transitions": [*state.edge_transitions, "research_executor:empty_fallback"],
        }

    relevance_scores = {item.chunk_id: item.score for item in capped}
    return {
        "research_hops_used": hops_used,
        "retrieved_documents": capped,
        "relevant_documents": capped,
        "relevance_scores": relevance_scores,
        "bin_ids_used": used_bins,
        "retrieval_mode": RetrievalMode.GROUNDED,
        "next_step": NodeOutcome.GENERATE,
        "edge_transitions": [*state.edge_transitions, "research_executor:findings_ready"],
    }
