from __future__ import annotations

from app.core.pipeline_config import SelfRagConfig
from app.pipeline.prompts.registry import PromptRegistry
from app.pipeline.state import GraphState, HallucinationGradePayload, NodeOutcome, ProviderMessage
from app.pipeline.utils import parse_model_from_text
from app.router.llm_router import LLMRouter


def _build_context(documents) -> str:
    lines = []
    for index, item in enumerate(documents, start=1):
        lines.append(f"[{index}] {item.bin_title} | {item.item_name} | {item.chunk_id}")
        lines.append(item.chunk_text)
    return "\n\n".join(lines)


async def hallucination_grader_node(
    state: GraphState,
    *,
    router: LLMRouter,
    prompt_registry: PromptRegistry,
    pipeline_config: SelfRagConfig,
) -> dict:
    if not state.relevant_documents:
        return {
            "next_step": NodeOutcome.GENERATE,
            "edge_transitions": [*state.edge_transitions, "hallucination_grader:skip_parametric"],
        }

    prompt_spec = prompt_registry.get("hallucination_grader")
    prompt = prompt_spec.template.format(
        query=state.active_query,
        answer=state.final_answer or "",
        context=_build_context(state.relevant_documents),
    )
    response = await router.call_node(
        node_name="hallucination_grader",
        messages=[
            ProviderMessage(role="system", content=prompt),
            ProviderMessage(role="user", content=state.final_answer or ""),
        ],
    )

    payload = parse_model_from_text(response.content, HallucinationGradePayload)
    max_retries = pipeline_config.pipeline.hallucination_max_retries

    if payload.grounded:
        return {
            "next_step": NodeOutcome.GENERATE,
            "provider_trace": [*state.provider_trace, *response.trace],
            "prompt_versions": {
                **state.prompt_versions,
                prompt_spec.id: prompt_spec.version,
            },
            "edge_transitions": [*state.edge_transitions, "hallucination_grader:grounded"],
            "hallucination_feedback": None,
        }

    retries = state.hallucination_retries + 1
    if retries > max_retries:
        return {
            "next_step": NodeOutcome.GENERATE,
            "provider_trace": [*state.provider_trace, *response.trace],
            "prompt_versions": {
                **state.prompt_versions,
                prompt_spec.id: prompt_spec.version,
            },
            "edge_transitions": [*state.edge_transitions, "hallucination_grader:retry_cap_reached"],
            "hallucination_feedback": payload.reason or "Previous answer was not fully grounded.",
        }

    return {
        "next_step": NodeOutcome.REGENERATE,
        "hallucination_retries": retries,
        "provider_trace": [*state.provider_trace, *response.trace],
        "prompt_versions": {
            **state.prompt_versions,
            prompt_spec.id: prompt_spec.version,
        },
        "edge_transitions": [*state.edge_transitions, "hallucination_grader:regenerate"],
        "hallucination_feedback": payload.reason or "Previous answer introduced unsupported claims.",
    }
