from __future__ import annotations

from app.pipeline.prompts.registry import PromptRegistry
from app.pipeline.state import Citation, GraphState, NodeOutcome, ProviderMessage, RetrievalMode
from app.router.llm_router import LLMRouter


def _build_context(documents) -> str:
    if not documents:
        return ""
    lines = []
    for index, item in enumerate(documents, start=1):
        lines.append(f"[{index}] {item.bin_title} | {item.item_name} | {item.chunk_id}")
        lines.append(item.chunk_text)
    return "\n\n".join(lines)


def _build_citations(documents) -> list[Citation]:
    citations: list[Citation] = []
    for item in documents:
        excerpt = item.chunk_text[:320]
        citations.append(
            Citation(
                item_name=item.item_name,
                chunk_excerpt=excerpt,
                bin_title=item.bin_title,
                chunk_id=item.chunk_id,
                score=item.score,
            )
        )
    return citations


async def answer_generator_node(
    state: GraphState,
    *,
    router: LLMRouter,
    prompt_registry: PromptRegistry,
) -> dict:
    grounded_docs = state.relevant_documents
    retrieval_mode = RetrievalMode.GROUNDED if grounded_docs else RetrievalMode.PARAMETRIC
    prompt_key = "answer_generator" if grounded_docs else "answer_generator_parametric"
    prompt_spec = prompt_registry.get(prompt_key)

    prompt = prompt_spec.template.format(
        query=state.active_query,
        context=_build_context(grounded_docs),
        feedback=state.hallucination_feedback or "",
    )

    response = await router.call_node(
        node_name="answer_generator",
        messages=[
            ProviderMessage(role="system", content=prompt),
            ProviderMessage(role="user", content=state.active_query),
        ],
        timeout_ms=None,
    )

    answer = response.content.strip()
    next_step = NodeOutcome.GENERATE if retrieval_mode == RetrievalMode.PARAMETRIC else NodeOutcome.REGENERATE
    edge_label = "answer_generator:parametric" if retrieval_mode == RetrievalMode.PARAMETRIC else "answer_generator:grounded"

    return {
        "generation_attempts": state.generation_attempts + 1,
        "final_answer": answer,
        "citations": _build_citations(grounded_docs),
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
