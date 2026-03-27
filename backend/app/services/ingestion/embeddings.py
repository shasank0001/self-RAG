from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol

import httpx

from app.core.config import Settings
from app.services.ingestion.errors import ExternalDependencyError


class EmbeddingProvider(Protocol):
    model_name: str

    async def embed(self, texts: list[str]) -> list[list[float]]:
        ...


@dataclass(slots=True)
class DeterministicEmbeddingProvider:
    model_name: str
    dimensions: int = 64

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            digest = sha256(text.encode("utf-8")).digest()
            buffer = digest
            while len(buffer) < self.dimensions:
                buffer += sha256(buffer).digest()
            vectors.append([((value / 255.0) * 2.0) - 1.0 for value in buffer[: self.dimensions]])
        await asyncio.sleep(0)
        return vectors


@dataclass(slots=True)
class OpenAIEmbeddingProvider:
    model_name: str
    provider_name: str
    api_key: str
    base_url: str = "https://api.openai.com/v1"
    extra_headers: dict[str, str] | None = None

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not self.api_key:
            raise ExternalDependencyError(
                stage="embed",
                message=f"{self.provider_name.upper()}_API_KEY is required",
                retriable=False,
            )

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.base_url.rstrip('/')}/embeddings",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    **(self.extra_headers or {}),
                },
                json={"model": self.model_name, "input": texts},
            )

        if response.status_code >= 500:
            raise ExternalDependencyError(stage="embed", message="Embedding provider unavailable", retriable=True)
        if response.status_code >= 400:
            raise ExternalDependencyError(stage="embed", message=response.text, retriable=False)

        payload = response.json()
        data = payload.get("data")
        if not isinstance(data, list):
            raise ExternalDependencyError(stage="embed", message="Invalid embedding response payload", retriable=True)

        vectors: list[list[float]] = []
        for row in data:
            if not isinstance(row, dict) or not isinstance(row.get("embedding"), list):
                raise ExternalDependencyError(stage="embed", message="Malformed embedding row", retriable=True)
            vectors.append([float(value) for value in row["embedding"]])
        return vectors


@dataclass(slots=True)
class OllamaEmbeddingProvider:
    model_name: str
    dimensions: int
    base_url: str = "http://localhost:11434"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.base_url.rstrip('/')}/api/embed",
                json={
                    "model": self.model_name,
                    "input": texts,
                    "truncate": True,
                    "dimensions": self.dimensions,
                },
            )

        if response.status_code >= 500:
            raise ExternalDependencyError(stage="embed", message="Ollama unavailable", retriable=True)
        if response.status_code >= 400:
            raise ExternalDependencyError(stage="embed", message=response.text, retriable=False)

        payload = response.json()
        embeddings = payload.get("embeddings")
        if not isinstance(embeddings, list):
            raise ExternalDependencyError(stage="embed", message="Invalid Ollama embedding payload", retriable=True)

        vectors: list[list[float]] = []
        for row in embeddings:
            if not isinstance(row, list):
                raise ExternalDependencyError(stage="embed", message="Malformed Ollama embedding row", retriable=True)
            vectors.append([float(value) for value in row])
        return vectors


@dataclass(slots=True)
class RouterEmbeddingProvider:
    model_name: str
    dimensions: int = 64

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            digest = sha256(text.encode("utf-8")).digest()
            encoded = base64.b64encode(digest).decode("utf-8")
            buffer = encoded
            while len(buffer) < self.dimensions:
                buffer += base64.b64encode(sha256(buffer.encode("utf-8")).digest()).decode("utf-8")
            numbers = [ord(ch) for ch in buffer[: self.dimensions]]
            vectors.append([((value / 127.0) * 2.0) - 1.0 for value in numbers])
        await asyncio.sleep(0)
        return vectors


def validate_embedding_dimensions(
    *,
    vectors: list[list[float]],
    expected_dimensions: int,
    stage: str,
) -> None:
    if expected_dimensions <= 0:
        raise ExternalDependencyError(stage=stage, message="Expected embedding dimensions must be positive", retriable=False)

    for index, vector in enumerate(vectors):
        if len(vector) != expected_dimensions:
            raise ExternalDependencyError(
                stage=stage,
                message=(
                    f"Embedding vector at index {index} has {len(vector)} dimensions; "
                    f"expected {expected_dimensions}"
                ),
                retriable=False,
            )


def build_embedding_provider_from_values(
    *,
    provider_name: str,
    model_name: str,
    dimensions: int,
    settings: Settings,
) -> EmbeddingProvider:
    normalized_provider = provider_name.lower()

    if normalized_provider == "openai":
        return OpenAIEmbeddingProvider(
            model_name=model_name,
            provider_name="openai",
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
        )

    if normalized_provider == "openrouter":
        return OpenAIEmbeddingProvider(
            model_name=model_name,
            provider_name="openrouter",
            api_key=settings.openrouter_api_key,
            base_url=settings.openrouter_base_url,
            extra_headers={
                "HTTP-Referer": settings.api_base_url,
                "X-Title": settings.app_name,
            },
        )

    if normalized_provider == "ollama":
        return OllamaEmbeddingProvider(
            model_name=model_name,
            dimensions=dimensions,
            base_url=settings.ollama_base_url,
        )

    if normalized_provider == "router":
        return RouterEmbeddingProvider(model_name=model_name, dimensions=dimensions)

    return DeterministicEmbeddingProvider(model_name=model_name, dimensions=dimensions)


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    return build_embedding_provider_from_values(
        provider_name=settings.embedding_provider,
        model_name=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
        settings=settings,
    )
