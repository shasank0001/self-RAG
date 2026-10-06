from __future__ import annotations

from app.core.pipeline_config import SelfRagConfig
from app.pipeline.nodes.answer_generator import _build_citations, _build_context
from app.pipeline.prompts.registry import PromptRegistry
from app.pipeline.state import GraphState, NodeOutcome, ProviderMessage, RetrievalMode
from app.router.llm_router import LLMRouter


async def research_synthesizer_node(
    state: GraphState,
    *,
    router: LLMRouter,
    prompt_registry: PromptRegistry,
    pipeline_config: SelfRagConfig,
) -> dict:
    grounded_docs = state.relevant_documents
    retrieval_mode = RetrievalMode.GROUNDED if grounded_docs else RetrievalMode.PARAMETRIC
    prompt_spec = prompt_registry.get("research_synthesizer")

    prompt = prompt_spec.template.format(
        query=state.user_query,
        context=_build_context(grounded_docs),
        feedback=state.hallucination_feedback or "",
    )

    response = await router.call_node(
        node_name="research_synthesizer",
        messages=[
            ProviderMessage(role="system", content=prompt),
            ProviderMessage(role="user", content=state.user_query),
        ],
        timeout_ms=pipeline_config.llm.timeouts_ms.generation,
    )

    answer = response.content.strip()
    next_step = NodeOutcome.GENERATE if retrieval_mode == RetrievalMode.PARAMETRIC else NodeOutcome.REGENERATE
    edge_label = (
        "research_synthesizer:parametric" if retrieval_mode == RetrievalMode.PARAMETRIC else "research_synthesizer:grounded"
    )

    return {
        "generation_attempts": state.generation_attempts + 1,
        "final_answer": answer,
        "citations": _build_citations(grounded_docs, relevance_scores=state.relevance_scores),
        "retrieval_mode": retrieval_mode,
        "chosen_provider": response.provider,
        "chosen_model": response.model,
        "provider_trace": [*state.provider_trace, *response.trace],
        "next_step": next_step,
        "prompt_versions": {
            **state.prompt_versions,
            prompt_spec.id: prompt_spec.version,
        },
        "edge_transitions": [*state.edge_transitions, edge_label],
    }
