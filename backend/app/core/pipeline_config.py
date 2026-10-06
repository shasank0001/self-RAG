from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.config import Settings, get_settings


class LLMNodeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str


class LLMTimeoutConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default: int = 12000
    generation: int = 25000


class LLMRetryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transient_max: int = 1


class LLMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fallback_chain: list[str] = Field(default_factory=lambda: ["openrouter"])
    fallback_models: dict[str, str] = Field(
        default_factory=lambda: {
            "openrouter": "openai/gpt-5.4-mini",
        }
    )
    nodes: dict[str, LLMNodeConfig] = Field(
        default_factory=lambda: {
            "retrieval_decision": LLMNodeConfig(provider="openrouter", model="openai/gpt-5.4-mini"),
            "relevance_grader": LLMNodeConfig(provider="openrouter", model="openai/gpt-5.4-mini"),
            "query_rewriter": LLMNodeConfig(provider="openrouter", model="openai/gpt-5.4-mini"),
            "answer_generator": LLMNodeConfig(provider="openrouter", model="openai/gpt-5.4-mini"),
            "hallucination_grader": LLMNodeConfig(provider="openrouter", model="openai/gpt-5.4-mini"),
            "research_planner": LLMNodeConfig(provider="openrouter", model="openai/gpt-5.4-mini"),
            "research_executor": LLMNodeConfig(provider="openrouter", model="openai/gpt-5.4-mini"),
            "research_synthesizer": LLMNodeConfig(provider="openrouter", model="openai/gpt-5.4-mini"),
        }
    )
    timeouts_ms: LLMTimeoutConfig = Field(default_factory=LLMTimeoutConfig)
    retries: LLMRetryConfig = Field(default_factory=LLMRetryConfig)

    @model_validator(mode="after")
    def _validate_nodes(self) -> LLMConfig:
        if not self.fallback_chain:
            raise ValueError("llm.fallback_chain must include at least one provider")

        required_nodes = {
            "retrieval_decision",
            "relevance_grader",
            "query_rewriter",
            "answer_generator",
            "hallucination_grader",
        }
        missing = required_nodes.difference(self.nodes.keys())
        if missing:
            raise ValueError(f"llm.nodes missing required entries: {', '.join(sorted(missing))}")
        return self


class RetrievalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    top_k_per_namespace: int = 5
    merged_top_n: int = 10


class ResearchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    max_sub_questions: int = 3
    max_hops: int = 1
    synthesizer_max_docs: int = 12


class PipelineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rewrite_max_attempts: int = 3
    hallucination_max_retries: int = 2
    relevance_threshold: float = 0.5
    node_timeout_ms: int = 30000
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    research: ResearchConfig = Field(default_factory=ResearchConfig)


class EmbeddingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active_provider: str = "openrouter"
    active_model: str = "openai/text-embedding-3-small"
    dimensions: int = 1536
    enforce_ingest_alignment: bool = True


class PromptNodeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    version: str
    path: str


class PromptConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodes: dict[str, PromptNodeConfig] = Field(
        default_factory=lambda: {
            "retrieval_decision": PromptNodeConfig(
                id="retrieval_decision",
                version="v1",
                path="templates/retrieval_decision/v1.txt",
            ),
            "relevance_grader": PromptNodeConfig(
                id="relevance_grader",
                version="v1",
                path="templates/relevance_grader/v1.txt",
            ),
            "query_rewriter": PromptNodeConfig(
                id="query_rewriter",
                version="v1",
                path="templates/query_rewriter/v1.txt",
            ),
            "answer_generator": PromptNodeConfig(
                id="answer_generator",
                version="v1",
                path="templates/answer_generator/v1.txt",
            ),
            "answer_generator_parametric": PromptNodeConfig(
                id="answer_generator_parametric",
                version="v1",
                path="templates/answer_generator_parametric/v1.txt",
            ),
            "hallucination_grader": PromptNodeConfig(
                id="hallucination_grader",
                version="v1",
                path="templates/hallucination_grader/v1.txt",
            ),
            "research_planner": PromptNodeConfig(
                id="research_planner",
                version="v1",
                path="templates/research_planner/v1.txt",
            ),
            "research_synthesizer": PromptNodeConfig(
                id="research_synthesizer",
                version="v1",
                path="templates/research_synthesizer/v1.txt",
            ),
        }
    )


class SelfRagConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    llm: LLMConfig = Field(default_factory=LLMConfig)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    prompts: PromptConfig = Field(default_factory=PromptConfig)


def _resolve_pipeline_config_path(settings: Settings) -> Path:
    configured_path = Path(settings.pipeline_config_path)
    if configured_path.is_absolute():
        return configured_path
    backend_root = Path(__file__).resolve().parents[2]
    return (backend_root / configured_path).resolve()


def load_pipeline_config(settings: Settings | None = None) -> SelfRagConfig:
    settings = settings or get_settings()
    config_path = _resolve_pipeline_config_path(settings)

    if not config_path.exists():
        return SelfRagConfig()

    loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if loaded is None:
        loaded = {}
    if not isinstance(loaded, dict):
        raise ValueError("config.yaml root must be a mapping")

    return SelfRagConfig.model_validate(loaded)


@lru_cache
def get_pipeline_config() -> SelfRagConfig:
    return load_pipeline_config()
