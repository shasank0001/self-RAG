from __future__ import annotations

from app.pipeline.prompts.registry import PromptRegistry
from app.pipeline.state import GraphState, NodeOutcome, ProviderMessage, RetrievalDecisionPayload, RetrievalMode
from app.pipeline.utils import parse_model_from_text
from app.router.llm_router import LLMRouter


async def retrieval_decision_node(
    state: GraphState,
    *,
    router: LLMRouter,
    prompt_registry: PromptRegistry,
) -> dict:
    if not state.selected_bins:
        return {
            "next_step": NodeOutcome.SKIP,
            "retrieval_mode": RetrievalMode.PARAMETRIC,
            "edge_transitions": [*state.edge_transitions, "retrieval_decision:skip_no_bins"],
        }

    return {
        "next_step": NodeOutcome.RETRIEVE,
        "retrieval_mode": RetrievalMode.GROUNDED,
        "edge_transitions": [*state.edge_transitions, "retrieval_decision:retrieve_selected_bins"],
    }

    prompt_spec = prompt_registry.get("retrieval_decision")
    prompt = prompt_spec.template.format(query=state.active_query)

    response = await router.call_node(
        node_name="retrieval_decision",
        messages=[
            ProviderMessage(role="system", content=prompt),
            ProviderMessage(role="user", content=state.active_query),
        ],
    )

    payload = parse_model_from_text(response.content, RetrievalDecisionPayload)
    next_step = NodeOutcome.RETRIEVE if payload.decision == "retrieve" else NodeOutcome.SKIP
    retrieval_mode = RetrievalMode.GROUNDED if payload.decision == "retrieve" else RetrievalMode.PARAMETRIC

    edge_label = f"retrieval_decision:{payload.decision}"
    return {
        "next_step": next_step,
        "retrieval_mode": retrieval_mode,
        "provider_trace": [*state.provider_trace, *response.trace],
        "prompt_versions": {
            **state.prompt_versions,
            prompt_spec.id: prompt_spec.version,
        },
        "edge_transitions": [*state.edge_transitions, edge_label],
    }
