from __future__ import annotations

import json
from pathlib import Path

from app.pipeline.prompts.registry import PromptRegistry


def test_prompt_fixture_reproducibility() -> None:
    fixture_path = Path(__file__).resolve().parent / "fixtures" / "phase4_prompt_paths.json"
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    registry = PromptRegistry()

    retrieval_template = registry.get("retrieval_decision").template.format(query=payload["retrieval_decision"]["query"])
    assert payload["retrieval_decision"]["expected_phrase"] in retrieval_template

    grounded_template = registry.get("answer_generator").template.format(
        query=payload["answer_generator"]["query"],
        context=payload["answer_generator"]["context"],
        feedback="",
    )
    assert payload["answer_generator"]["expected_phrase"] in grounded_template

    parametric_template = registry.get("answer_generator_parametric").template.format(
        query=payload["answer_generator_parametric"]["query"]
    )
    assert payload["answer_generator_parametric"]["expected_phrase"] in parametric_template
