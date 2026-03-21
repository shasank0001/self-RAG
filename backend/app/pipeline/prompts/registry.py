from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.core.pipeline_config import PromptNodeConfig, SelfRagConfig, get_pipeline_config


@dataclass(frozen=True, slots=True)
class PromptTemplateSpec:
    id: str
    version: str
    template: str
    path: Path


class PromptRegistry:
    def __init__(self, *, pipeline_config: SelfRagConfig | None = None) -> None:
        self._pipeline_config = pipeline_config or get_pipeline_config()
        self._cache: dict[str, PromptTemplateSpec] = {}

    def _resolve_path(self, relative_path: str) -> Path:
        base_path = Path(__file__).resolve().parent
        return (base_path / relative_path).resolve()

    def _load_spec(self, node_name: str, prompt_config: PromptNodeConfig) -> PromptTemplateSpec:
        path = self._resolve_path(prompt_config.path)
        template = path.read_text(encoding="utf-8")
        return PromptTemplateSpec(
            id=prompt_config.id,
            version=prompt_config.version,
            template=template,
            path=path,
        )

    def get(self, node_name: str) -> PromptTemplateSpec:
        if node_name in self._cache:
            return self._cache[node_name]

        node_prompts = self._pipeline_config.prompts.nodes
        if node_name not in node_prompts:
            raise KeyError(f"Prompt configuration missing for node '{node_name}'")

        spec = self._load_spec(node_name, node_prompts[node_name])
        self._cache[node_name] = spec
        return spec

    def versions_for_nodes(self, node_names: list[str]) -> dict[str, str]:
        return {name: self.get(name).version for name in node_names}
