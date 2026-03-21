from __future__ import annotations

from app.core.pipeline_config import SelfRagConfig
from app.pipeline.prompts.registry import PromptRegistry
from app.pipeline.state import GraphState, NodeOutcome, ProviderMessage, QueryRewritePayload
from app.pipeline.utils import parse_model_from_text
from app.router.llm_router import LLMRouter


async def query_rewriter_node(
    state: GraphState,
    *,
    router: LLMRouter,
    prompt_registry: PromptRegistry,
    pipeline_config: SelfRagConfig,
) -> dict:
    max_attempts = pipeline_config.pipeline.rewrite_max_attempts
    current_attempt = state.rewrite_attempts + 1

    if current_attempt > max_attempts:
        return {
            "next_step": NodeOutcome.GENERATE,
            "edge_transitions": [*state.edge_transitions, "query_rewriter:cap_reached_generate"],
        }

    prompt_spec = prompt_registry.get("query_rewriter")
    prompt = prompt_spec.template.format(query=state.active_query)
    response = await router.call_node(
        node_name="query_rewriter",
        messages=[
            ProviderMessage(role="system", content=prompt),
            ProviderMessage(role="user", content=state.active_query),
        ],
    )
    payload = parse_model_from_text(response.content, QueryRewritePayload)

    rewritten_query = payload.rewritten_query.strip()
    if not rewritten_query:
        rewritten_query = state.active_query

    if current_attempt >= max_attempts:
        return {
            "rewrite_attempts": current_attempt,
            "rewritten_query": rewritten_query,
            "next_step": NodeOutcome.GENERATE,
            "provider_trace": [*state.provider_trace, *response.trace],
            "prompt_versions": {
                **state.prompt_versions,
                prompt_spec.id: prompt_spec.version,
            },
            "edge_transitions": [*state.edge_transitions, "query_rewriter:cap_reached_generate"],
        }

    return {
        "rewrite_attempts": current_attempt,
        "rewritten_query": rewritten_query,
        "next_step": NodeOutcome.RETRIEVE,
        "provider_trace": [*state.provider_trace, *response.trace],
        "prompt_versions": {
            **state.prompt_versions,
            prompt_spec.id: prompt_spec.version,
        },
        "edge_transitions": [*state.edge_transitions, "query_rewriter:retrieve"],
    }
