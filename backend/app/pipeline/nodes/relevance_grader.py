from __future__ import annotations

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
    relevant_docs = []
    relevance_scores: dict[str, float] = {}
    provider_trace = list(state.provider_trace)

    for document in documents:
        prompt = prompt_spec.template.format(query=state.active_query, chunk=document.chunk_text)
        response = await router.call_node(
            node_name="relevance_grader",
            messages=[
                ProviderMessage(role="system", content=prompt),
                ProviderMessage(role="user", content=state.active_query),
            ],
        )
        provider_trace.extend(response.trace)

        payload = parse_model_from_text(response.content, RelevanceGradePayload)
        relevance_scores[document.chunk_id] = payload.score
        if payload.relevant:
            relevant_docs.append(document)

    pass_ratio = len(relevant_docs) / max(len(documents), 1)
    threshold = pipeline_config.pipeline.relevance_threshold
    route_to_rewrite = pass_ratio < threshold

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
