from __future__ import annotations

from app.core.pipeline_config import SelfRagConfig
from app.pipeline.prompts.registry import PromptRegistry
from app.pipeline.state import GraphState, NodeOutcome, ProviderMessage, ResearchPlanPayload
from app.pipeline.utils import parse_model_from_text
from app.router.llm_router import LLMRouter


def _normalize_plan(raw_questions: list[str], *, original_query: str, max_questions: int) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    original = " ".join(original_query.split()).casefold()
    for item in raw_questions:
        if not isinstance(item, str):
            continue
        cleaned = " ".join(item.split()).strip()
        if not cleaned:
            continue
        folded = cleaned.casefold()
        if folded == original or folded in seen:
            continue
        seen.add(folded)
        normalized.append(cleaned)
        if len(normalized) >= max_questions:
            break
    return normalized


async def research_planner_node(
    state: GraphState,
    *,
    router: LLMRouter,
    prompt_registry: PromptRegistry,
    pipeline_config: SelfRagConfig,
) -> dict:
    research = pipeline_config.pipeline.research
    if not research.enabled or not state.selected_bins:
        return {
            "research_mode": False,
            "next_step": NodeOutcome.GENERATE,
            "edge_transitions": [*state.edge_transitions, "research_planner:fallback_standard"],
        }

    prompt_spec = prompt_registry.get("research_planner")
    bin_titles = ", ".join(item.title for item in state.selected_bins)
    prompt = prompt_spec.template.format(
        query=state.user_query,
        bin_titles=bin_titles or "(no bins)",
        max_questions=research.max_sub_questions,
    )
    response = await router.call_node(
        node_name="research_planner",
        messages=[
            ProviderMessage(role="system", content=prompt),
            ProviderMessage(role="user", content=state.user_query),
        ],
    )
    payload = parse_model_from_text(response.content, ResearchPlanPayload)
    plan = _normalize_plan(
        payload.sub_questions,
        original_query=state.user_query,
        max_questions=research.max_sub_questions,
    )

    if not plan:
        return {
            "research_mode": False,
            "research_plan": [],
            "next_step": NodeOutcome.GENERATE,
            "provider_trace": [*state.provider_trace, *response.trace],
            "prompt_versions": {
                **state.prompt_versions,
                prompt_spec.id: prompt_spec.version,
            },
            "edge_transitions": [*state.edge_transitions, "research_planner:empty_fallback"],
        }

    return {
        "research_plan": plan,
        "next_step": NodeOutcome.RETRIEVE,
        "provider_trace": [*state.provider_trace, *response.trace],
        "prompt_versions": {
            **state.prompt_versions,
            prompt_spec.id: prompt_spec.version,
        },
        "edge_transitions": [*state.edge_transitions, "research_planner:plan_ready"],
    }
