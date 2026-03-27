from __future__ import annotations

import asyncio
import logging
from collections import deque

from app.core.tracing import start_span
from app.core.pipeline_config import SelfRagConfig
from app.observability.events import LogEvent
from app.observability.metrics import get_metrics_registry
from app.pipeline.state import GraphState, NodeOutcome, RetrievedDocument, RetrievalMode, SelectedBin
from app.router.embedding_router import EmbeddingRouter
from app.services.ingestion.vector_index import VectorIndex, VectorSearchMatch

logger = logging.getLogger(__name__)
metrics = get_metrics_registry()


def _coerce_uuid(value: object):
    from uuid import UUID

    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError:
            return None
    return None


def _to_retrieved_document(match: VectorSearchMatch, bin_lookup: dict[str, SelectedBin]) -> RetrievedDocument | None:
    metadata = match.metadata
    bin_id_raw = metadata.get("bin_id")
    chunk_id_raw = metadata.get("chunk_id")
    chunk_text_raw = metadata.get("chunk_text") or metadata.get("text")

    if not isinstance(bin_id_raw, str):
        return None
    if not isinstance(chunk_id_raw, str):
        return None
    if not isinstance(chunk_text_raw, str):
        return None

    bin_obj = bin_lookup.get(bin_id_raw)
    if bin_obj is None:
        return None

    item_id = _coerce_uuid(metadata.get("item_id"))
    item_name = metadata.get("source_name")
    if not isinstance(item_name, str):
        item_name = "unknown-item"

    return RetrievedDocument(
        chunk_id=chunk_id_raw,
        chunk_text=chunk_text_raw,
        item_id=item_id,
        item_name=item_name,
        bin_id=bin_obj.id,
        bin_title=bin_obj.title,
        score=match.score,
        metadata={str(key): value for key, value in metadata.items()},
    )


def _round_robin_merge(
    *,
    grouped_matches: dict[str, list[RetrievedDocument]],
    merged_top_n: int,
) -> list[RetrievedDocument]:
    queues: dict[str, deque[RetrievedDocument]] = {
        namespace: deque(items)
        for namespace, items in grouped_matches.items()
    }
    ordered_namespaces = list(queues.keys())
    merged: list[RetrievedDocument] = []
    seen_chunk_ids: set[str] = set()

    while ordered_namespaces and len(merged) < merged_top_n:
        next_round: list[str] = []
        for namespace in ordered_namespaces:
            queue = queues[namespace]
            while queue:
                candidate = queue.popleft()
                if candidate.chunk_id in seen_chunk_ids:
                    continue
                merged.append(candidate)
                seen_chunk_ids.add(candidate.chunk_id)
                break
            if queue:
                next_round.append(namespace)
            if len(merged) >= merged_top_n:
                break
        ordered_namespaces = next_round

    return merged


async def retrieval_node(
    state: GraphState,
    *,
    vector_index: VectorIndex,
    embedding_router: EmbeddingRouter,
    pipeline_config: SelfRagConfig,
) -> dict:
    if state.retrieval_mode == RetrievalMode.PARAMETRIC:
        return {
            "next_step": NodeOutcome.GENERATE,
            "edge_transitions": [*state.edge_transitions, "retrieval:skip_parametric"],
            "retrieved_documents": [],
            "relevant_documents": [],
            "relevance_scores": {},
        }

    top_k = pipeline_config.pipeline.retrieval.top_k_per_namespace
    merged_top_n = pipeline_config.pipeline.retrieval.merged_top_n

    logger.info(LogEvent.RETRIEVAL_STARTED, extra={"bin_count": len(state.selected_bins), "top_k": top_k})
    with start_span(
        "graph.retrieval",
        attributes={
            "bin_count": len(state.selected_bins),
            "top_k_per_namespace": top_k,
            "merged_top_n": merged_top_n,
        },
    ) as span:
        query_vector, resolution = await embedding_router.embed_query(query=state.active_query, selected_bins=state.selected_bins)

        bin_lookup = {str(item.id): item for item in state.selected_bins}
        grouped: dict[str, list[RetrievedDocument]] = {}

        async def _query_namespace(bin_item: SelectedBin) -> tuple[str, list[RetrievedDocument]]:
            namespace = bin_item.vector_namespace
            matches = await vector_index.query(namespace=namespace, vector=query_vector, top_k=top_k)
            normalized: list[RetrievedDocument] = []
            for match in matches:
                document = _to_retrieved_document(match, bin_lookup)
                if document is None:
                    continue
                normalized.append(document)

            normalized.sort(key=lambda item: item.score, reverse=True)
            return (f"{namespace}:{bin_item.id}", normalized)

        queried = await asyncio.gather(*[_query_namespace(item) for item in state.selected_bins])
        for group_key, normalized in queried:
            grouped[group_key] = normalized

        merged = _round_robin_merge(grouped_matches=grouped, merged_top_n=merged_top_n)
        relevance_scores = {item.chunk_id: item.score for item in merged}

        next_step = NodeOutcome.REWRITE if not merged else NodeOutcome.GENERATE
        edge_label = "retrieval:empty" if not merged else "retrieval:docs"

        used_bins = [bin_item.id for bin_item in state.selected_bins]
        retrieval_mode = state.retrieval_mode or RetrievalMode.GROUNDED
        span.set_attribute("retrieved_docs_count", len(merged))

    metrics.observe("selfrag_retrieval_docs_count", float(len(merged)), mode=retrieval_mode.value)
    if not merged:
        logger.info(LogEvent.RETRIEVAL_EMPTY, extra={"retrieval_mode": retrieval_mode.value})
    else:
        logger.info(
            LogEvent.RETRIEVAL_COMPLETED,
            extra={"retrieval_mode": retrieval_mode.value, "document_count": len(merged)},
        )

    return {
        "next_step": next_step,
        "retrieval_mode": retrieval_mode,
        "retrieved_documents": merged if merged else [],
        "relevant_documents": merged if merged else [],
        "relevance_scores": relevance_scores if merged else {},
        "bin_ids_used": used_bins,
        "edge_transitions": [*state.edge_transitions, edge_label],
        "prompt_versions": {
            **state.prompt_versions,
            "embedding_resolution": f"{resolution.provider}:{resolution.model}:{resolution.dimensions}",
        },
    }
