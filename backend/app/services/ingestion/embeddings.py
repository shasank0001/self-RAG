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
    dimensions: int

    async def embed(self, texts: list[str]) -> list[list[float]]:
        ...


@dataclass
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


@dataclass
class OpenAIEmbeddingProvider:
    model_name: str
    api_key: str
    dimensions: int
    base_url: str = "https://api.openai.com/v1"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not self.api_key:
            raise ExternalDependencyError(stage="embed", message="OPENAI_API_KEY is required", retriable=False)

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.base_url}/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model_name, "input": texts, "dimensions": self.dimensions},
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


@dataclass
class RouterEmbeddingProvider:
    model_name: str
    dimensions: int = 64

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            digest = sha256(text.encode("utf-8")).digest()
            encoded = base64.b64encode(digest).decode("utf-8")
            numbers = [ord(ch) for ch in encoded]
            expanded = numbers
            while len(expanded) < self.dimensions:
                expanded.extend(numbers)
            vectors.append([((value / 127.0) * 2.0) - 1.0 for value in expanded[: self.dimensions]])
        await asyncio.sleep(0)
        return vectors


@dataclass
class OllamaEmbeddingProvider:
    model_name: str
    dimensions: int
    base_url: str = "http://localhost:11434"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        payload: dict[str, object] = {
            "model": self.model_name,
            "input": texts,
            "truncate": True,
        }
        if self.dimensions > 0:
            payload["dimensions"] = self.dimensions

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.base_url.rstrip('/')}/api/embed",
                json=payload,
            )

        if response.status_code >= 500:
            raise ExternalDependencyError(stage="embed", message="Ollama embedding service unavailable", retriable=True)
        if response.status_code >= 400:
            raise ExternalDependencyError(stage="embed", message=response.text, retriable=False)

        body = response.json()
        embeddings = body.get("embeddings")
        if not isinstance(embeddings, list):
            raise ExternalDependencyError(stage="embed", message="Invalid Ollama embedding response payload", retriable=True)

        vectors: list[list[float]] = []
        for item in embeddings:
            if not isinstance(item, list):
                raise ExternalDependencyError(stage="embed", message="Malformed Ollama embedding row", retriable=True)
            vectors.append([float(value) for value in item])
        return vectors


def build_embedding_provider_from_values(
    *,
    provider_name: str,
    model_name: str,
    dimensions: int,
    settings: Settings,
) -> EmbeddingProvider:
    provider_name = provider_name.lower()
    if provider_name == "openai":
        return OpenAIEmbeddingProvider(
            model_name=model_name,
            api_key=settings.openai_api_key,
            dimensions=dimensions,
            base_url=settings.openai_base_url,
        )

    if provider_name == "router":
        return RouterEmbeddingProvider(model_name=model_name, dimensions=dimensions)

    if provider_name == "ollama":
        return OllamaEmbeddingProvider(
            model_name=model_name,
            dimensions=dimensions,
            base_url=settings.ollama_base_url,
        )

    return DeterministicEmbeddingProvider(model_name=model_name, dimensions=dimensions)


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    return build_embedding_provider_from_values(
        provider_name=settings.embedding_provider,
        model_name=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
        settings=settings,
    )


def validate_embedding_dimensions(*, vectors: list[list[float]], expected_dimensions: int, stage: str) -> None:
    for vector in vectors:
        if len(vector) != expected_dimensions:
            raise ExternalDependencyError(
                stage=stage,
                message=(
                    f"Embedding dimension mismatch: expected {expected_dimensions}, "
                    f"received {len(vector)}"
                ),
                retriable=False,
            )
