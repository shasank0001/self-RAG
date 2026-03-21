from __future__ import annotations

from app.pipeline.prompts.registry import PromptRegistry


def test_prompt_registry_loads_versions_and_templates() -> None:
    registry = PromptRegistry()

    decision_prompt = registry.get("retrieval_decision")
    answer_prompt = registry.get("answer_generator")

    assert decision_prompt.version == "v1"
    assert "decision" in decision_prompt.template
    assert answer_prompt.version == "v1"
    assert "grounded answer generator" in answer_prompt.template


def test_prompt_registry_versions_for_nodes() -> None:
    registry = PromptRegistry()
    versions = registry.versions_for_nodes(["retrieval_decision", "answer_generator_parametric"])

    assert versions == {
        "retrieval_decision": "v1",
        "answer_generator_parametric": "v1",
    }
