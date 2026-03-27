from __future__ import annotations

import asyncio

from app.core.pipeline_config import SelfRagConfig
from app.pipeline.prompts.registry import PromptRegistry
from app.pipeline.state import GraphState, NodeOutcome, ProviderMessage, RelevanceGradePayload
from app.pipeline.utils import parse_model_from_text
from app.router.llm_router import LLMRouter


async def relevance_grader_node(
    state: GraphState,
    *,
    router: LLMRouter,
    prompt_registry: PromptRegistry,
    pipeline_config: SelfRagConfig,
) -> dict:
    documents = state.retrieved_documents
    if not documents:
        return {
            "next_step": NodeOutcome.REWRITE,
            "relevant_documents": [],
            "relevance_scores": {},
            "edge_transitions": [*state.edge_transitions, "relevance_grader:no_docs_rewrite"],
        }

    prompt_spec = prompt_registry.get("relevance_grader")
    relevance_scores: dict[str, float] = {}
    
    async def _grade_document(index: int, document):
        prompt = prompt_spec.template.format(query=state.active_query, chunk=document.chunk_text)
        response = await router.call_node(
            node_name="relevance_grader",
            messages=[
                ProviderMessage(role="system", content=prompt),
                ProviderMessage(role="user", content=state.active_query),
            ],
        )
        payload = parse_model_from_text(response.content, RelevanceGradePayload)
        return index, document, payload, response.trace

    graded = await asyncio.gather(*[_grade_document(index, document) for index, document in enumerate(documents)])
    provider_trace = [*state.provider_trace]
    relevant_docs_with_order = []
    for index, document, payload, trace in graded:
        provider_trace.extend(trace)
        relevance_scores[document.chunk_id] = payload.score
        if payload.relevant:
            relevant_docs_with_order.append((index, document))

    relevant_docs_with_order.sort(
        key=lambda item: (-relevance_scores[item[1].chunk_id], item[0]),
    )
    relevant_docs = [document for _, document in relevant_docs_with_order]

    average_score = sum(relevance_scores.values()) / max(len(documents), 1)
    threshold = pipeline_config.pipeline.relevance_threshold
    route_to_rewrite = average_score < threshold

    next_step = NodeOutcome.REWRITE if route_to_rewrite else NodeOutcome.GENERATE
    edge_label = "relevance_grader:rewrite" if route_to_rewrite else "relevance_grader:generate"

    return {
        "next_step": next_step,
        "relevant_documents": relevant_docs,
        "relevance_scores": relevance_scores,
        "provider_trace": provider_trace,
        "prompt_versions": {
            **state.prompt_versions,
            prompt_spec.id: prompt_spec.version,
        },
        "edge_transitions": [*state.edge_transitions, edge_label],
    }
