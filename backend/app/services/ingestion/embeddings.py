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


@dataclass
class DeterministicEmbeddingProvider:
    model_name: str
    dimension: int = 64

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            digest = sha256(text.encode("utf-8")).digest()
            buffer = digest
            while len(buffer) < self.dimension:
                buffer += sha256(buffer).digest()
            vectors.append([((value / 255.0) * 2.0) - 1.0 for value in buffer[: self.dimension]])
        await asyncio.sleep(0)
        return vectors


@dataclass
class OpenAIEmbeddingProvider:
    model_name: str
    api_key: str
    base_url: str = "https://api.openai.com/v1"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not self.api_key:
            raise ExternalDependencyError(stage="embed", message="OPENAI_API_KEY is required", retriable=False)

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.base_url}/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
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


@dataclass
class RouterEmbeddingProvider:
    model_name: str

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            digest = sha256(text.encode("utf-8")).digest()
            encoded = base64.b64encode(digest).decode("utf-8")
            numbers = [ord(ch) for ch in encoded[:64]]
            vectors.append([((value / 127.0) * 2.0) - 1.0 for value in numbers])
        await asyncio.sleep(0)
        return vectors


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    provider_name = settings.embedding_provider.lower()
    if provider_name == "openai":
        return OpenAIEmbeddingProvider(
            model_name=settings.embedding_model,
            api_key=settings.openai_api_key,
        )

    if provider_name == "router":
        return RouterEmbeddingProvider(model_name=settings.embedding_model)

    return DeterministicEmbeddingProvider(model_name=settings.embedding_model)
