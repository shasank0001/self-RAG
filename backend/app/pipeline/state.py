from __future__ import annotations

from enum import Enum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.router.error_types import RouterErrorType

SCHEMA_VERSION = 1


class NodeOutcome(str, Enum):
    RETRIEVE = "retrieve"
    SKIP = "skip"
    REWRITE = "rewrite"
    GENERATE = "generate"
    REGENERATE = "regenerate"
    ABORT = "abort"


class RetrievalMode(str, Enum):
    GROUNDED = "grounded"
    PARAMETRIC = "parametric"


class SelectedBin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    title: str
    vector_namespace: str
    embedding_provider: str
    embedding_model: str
    embedding_dimensions: int


class RetrievedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    chunk_text: str
    item_id: UUID | None = None
    item_name: str
    bin_id: UUID
    bin_title: str
    score: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_name: str
    chunk_excerpt: str
    bin_title: str
    chunk_id: str
    score: float | None = None


class ProviderMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant"]
    content: str


class ProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    node_name: str
    messages: list[ProviderMessage]
    timeout_ms: int
    temperature: float = 0.0


class ProviderResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    content: str
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    usage: dict[str, int | float] = Field(default_factory=dict)


class RetrievalDecisionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["retrieve", "skip"]
    reason: str | None = None


class RelevanceGradePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relevant: bool
    score: float = Field(ge=0.0, le=1.0)


class QueryRewritePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rewritten_query: str


class GenerationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str


class HallucinationGradePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grounded: bool
    reason: str | None = None


class ProviderTraceEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_name: str
    provider: str
    model: str
    attempt: int
    success: bool
    duration_ms: float
    error_type: RouterErrorType | None = None
    error_message: str | None = None
    fallback_from: str | None = None
    fallback_attempt: int | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    estimated_cost_usd: float | None = None
    provider_reported_cost_usd: float | None = None


class GraphError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    node_name: str | None = None
    error_type: RouterErrorType | None = None
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class GraphState(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    schema_version: int = SCHEMA_VERSION
    user_query: str
    rewritten_query: str | None = None

    selected_bin_ids: list[UUID] = Field(default_factory=list)
    selected_bins: list[SelectedBin] = Field(default_factory=list)

    retrieved_documents: list[RetrievedDocument] = Field(default_factory=list)
    relevant_documents: list[RetrievedDocument] = Field(default_factory=list)
    relevance_scores: dict[str, float] = Field(default_factory=dict)
    citations: list[Citation] = Field(default_factory=list)

    retrieval_mode: RetrievalMode | None = None
    bin_ids_used: list[UUID] = Field(default_factory=list)

    generation_attempts: int = 0
    rewrite_attempts: int = 0
    hallucination_retries: int = 0

    provider_trace: list[ProviderTraceEntry] = Field(default_factory=list)
    edge_transitions: list[str] = Field(default_factory=list)
    prompt_versions: dict[str, str] = Field(default_factory=dict)

    final_answer: str | None = None
    chosen_provider: str | None = None
    chosen_model: str | None = None
    hallucination_feedback: str | None = None

    next_step: NodeOutcome | None = None
    error: GraphError | None = None

    @model_validator(mode="after")
    def _sync_bin_ids(self) -> GraphState:
        if self.selected_bins and not self.selected_bin_ids:
            self.selected_bin_ids = [item.id for item in self.selected_bins]
        if self.selected_bin_ids and not self.bin_ids_used:
            self.bin_ids_used = list(self.selected_bin_ids)
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"Unsupported graph state schema version: {self.schema_version}")
        return self

    @property
    def active_query(self) -> str:
        return self.rewritten_query or self.user_query
