from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.config import Settings, get_settings
from app.core.pipeline_config import SelfRagConfig, get_pipeline_config
from app.pipeline.state import SelectedBin
from app.services.ingestion.embeddings import (
    EmbeddingProvider,
    build_embedding_provider_from_values,
    validate_embedding_dimensions,
)


class EmbeddingAlignmentError(Exception):
    def __init__(self, *, code: str, message: str, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass(slots=True)
class EmbeddingResolution:
    provider: str
    model: str
    dimensions: int
    bin_ids: list[UUID]


class EmbeddingRouter:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        pipeline_config: SelfRagConfig | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._pipeline_config = pipeline_config or get_pipeline_config()
        self._provider_cache: dict[tuple[str, str, int], EmbeddingProvider] = {}

    def _default_resolution(self) -> EmbeddingResolution:
        return EmbeddingResolution(
            provider=self._pipeline_config.embedding.active_provider,
            model=self._pipeline_config.embedding.active_model,
            dimensions=self._pipeline_config.embedding.dimensions,
            bin_ids=[],
        )

    def resolve(self, selected_bins: list[SelectedBin]) -> EmbeddingResolution:
        if not selected_bins:
            return self._default_resolution()

        expected_provider = selected_bins[0].embedding_provider
        expected_model = selected_bins[0].embedding_model
        expected_dimensions = selected_bins[0].embedding_dimensions

        mismatches: list[dict[str, object]] = []
        for item in selected_bins:
            if (
                item.embedding_provider != expected_provider
                or item.embedding_model != expected_model
                or item.embedding_dimensions != expected_dimensions
            ):
                mismatches.append(
                    {
                        "bin_id": str(item.id),
                        "provider": item.embedding_provider,
                        "model": item.embedding_model,
                        "dimensions": item.embedding_dimensions,
                    }
                )

        if mismatches and self._pipeline_config.embedding.enforce_ingest_alignment:
            raise EmbeddingAlignmentError(
                code="embedding_alignment_mismatch",
                message="Selected bins use incompatible embedding configurations",
                details={
                    "expected": {
                        "provider": expected_provider,
                        "model": expected_model,
                        "dimensions": expected_dimensions,
                    },
                    "mismatches": mismatches,
                },
            )

        return EmbeddingResolution(
            provider=expected_provider,
            model=expected_model,
            dimensions=expected_dimensions,
            bin_ids=[item.id for item in selected_bins],
        )

    def _provider(self, resolution: EmbeddingResolution) -> EmbeddingProvider:
        key = (resolution.provider, resolution.model, resolution.dimensions)
        if key not in self._provider_cache:
            self._provider_cache[key] = build_embedding_provider_from_values(
                provider_name=resolution.provider,
                model_name=resolution.model,
                dimensions=resolution.dimensions,
                settings=self._settings,
            )
        return self._provider_cache[key]

    async def embed_query(self, *, query: str, selected_bins: list[SelectedBin]) -> tuple[list[float], EmbeddingResolution]:
        resolution = self.resolve(selected_bins)
        provider = self._provider(resolution)
        vectors = await provider.embed([query])

        if len(vectors) != 1:
            raise EmbeddingAlignmentError(
                code="embedding_query_count_mismatch",
                message="Embedding provider returned an invalid query vector count",
                details={"count": len(vectors)},
            )

        validate_embedding_dimensions(vectors=vectors, expected_dimensions=resolution.dimensions, stage="query_embed")
        return vectors[0], resolution
