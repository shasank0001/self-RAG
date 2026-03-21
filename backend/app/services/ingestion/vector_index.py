from __future__ import annotations

import asyncio
from dataclasses import dataclass
from math import sqrt
from typing import Any, cast
from uuid import UUID

from app.core.config import Settings
from app.services.ingestion.errors import ExternalDependencyError


@dataclass(slots=True)
class VectorPayload:
    id: str
    values: list[float]
    metadata: dict[str, str | int | float | bool]


@dataclass(slots=True)
class VectorSearchMatch:
    id: str
    score: float
    metadata: dict[str, str | int | float | bool]


class VectorIndex:
    async def upsert(self, *, namespace: str, vectors: list[VectorPayload]) -> None:
        raise NotImplementedError

    async def delete_by_item_id(self, *, namespace: str, item_id: UUID) -> None:
        raise NotImplementedError

    async def query(self, *, namespace: str, vector: list[float], top_k: int) -> list[VectorSearchMatch]:
        raise NotImplementedError


class InMemoryVectorIndex(VectorIndex):
    def __init__(self) -> None:
        self._store: dict[str, dict[str, VectorPayload]] = {}

    async def upsert(self, *, namespace: str, vectors: list[VectorPayload]) -> None:
        bucket = self._store.setdefault(namespace, {})
        for vector in vectors:
            bucket[vector.id] = vector
        await asyncio.sleep(0)

    async def delete_by_item_id(self, *, namespace: str, item_id: UUID) -> None:
        bucket = self._store.get(namespace, {})
        target = str(item_id)
        to_delete = [key for key, vector in bucket.items() if str(vector.metadata.get("item_id")) == target]
        for key in to_delete:
            bucket.pop(key, None)
        await asyncio.sleep(0)

    async def query(self, *, namespace: str, vector: list[float], top_k: int) -> list[VectorSearchMatch]:
        bucket = self._store.get(namespace, {})
        if not bucket:
            await asyncio.sleep(0)
            return []

        scored: list[VectorSearchMatch] = []
        query_norm = sqrt(sum(value * value for value in vector))
        for record in bucket.values():
            dot = sum(a * b for a, b in zip(vector, record.values))
            value_norm = sqrt(sum(value * value for value in record.values))
            denominator = query_norm * value_norm
            score = (dot / denominator) if denominator else 0.0
            scored.append(VectorSearchMatch(id=record.id, score=score, metadata=record.metadata))

        scored.sort(key=lambda item: item.score, reverse=True)
        await asyncio.sleep(0)
        return scored[: max(top_k, 0)]


class PineconeVectorIndex(VectorIndex):
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = None

    def _index(self):
        if not self._settings.pinecone_api_key:
            raise ExternalDependencyError(stage="upsert", message="PINECONE_API_KEY is required", retriable=False)
        if not self._settings.pinecone_host and not self._settings.pinecone_index_name:
            raise ExternalDependencyError(
                stage="upsert",
                message="Set PINECONE_HOST or PINECONE_INDEX_NAME",
                retriable=False,
            )

        if self._client is None:
            try:
                from pinecone import Pinecone  # type: ignore[import-not-found]
            except ImportError as exc:
                raise ExternalDependencyError(
                    stage="upsert", message="pinecone package is not installed", retriable=False
                ) from exc
            self._client = Pinecone(api_key=self._settings.pinecone_api_key)
        if self._settings.pinecone_host:
            host = self._settings.pinecone_host.replace("https://", "").replace("http://", "")
            return self._client.Index(host=host)
        return self._client.Index(self._settings.pinecone_index_name)

    async def upsert(self, *, namespace: str, vectors: list[VectorPayload]) -> None:
        payload: list[tuple[str, list[float], dict[str, str | int | float | bool]]] = [
            (vector.id, vector.values, vector.metadata) for vector in vectors
        ]
        index = cast(Any, self._index())
        try:
            await asyncio.to_thread(index.upsert, vectors=payload, namespace=namespace)
        except Exception as exc:
            raise ExternalDependencyError(stage="upsert", message="Failed to upsert vectors to Pinecone") from exc

    async def delete_by_item_id(self, *, namespace: str, item_id: UUID) -> None:
        index = cast(Any, self._index())
        try:
            await asyncio.to_thread(index.delete, namespace=namespace, filter={"item_id": str(item_id)})
        except Exception as exc:
            raise ExternalDependencyError(
                stage="delete", message="Failed to delete stale vectors by item_id", retriable=True
            ) from exc

    async def query(self, *, namespace: str, vector: list[float], top_k: int) -> list[VectorSearchMatch]:
        index = cast(Any, self._index())
        try:
            response = await asyncio.to_thread(
                index.query,
                namespace=namespace,
                vector=vector,
                top_k=top_k,
                include_metadata=True,
                include_values=False,
            )
        except Exception as exc:
            message = str(exc).lower()
            retriable = "dimension" not in message
            raise ExternalDependencyError(
                stage="query",
                message="Failed to query vectors from Pinecone",
                retriable=retriable,
            ) from exc

        if isinstance(response, dict):
            matches_payload = response["matches"] if "matches" in response else None
        else:
            matches_payload = getattr(response, "matches", None)
        if not isinstance(matches_payload, list):
            return []

        records: list[VectorSearchMatch] = []
        for match in matches_payload:
            if isinstance(match, dict):
                match_id = str(match["id"]) if "id" in match else ""
                match_score = float(match["score"]) if "score" in match else 0.0
                metadata = match["metadata"] if "metadata" in match else {}
            else:
                match_id = str(getattr(match, "id", ""))
                match_score = float(getattr(match, "score", 0.0))
                metadata = getattr(match, "metadata", {})

            if not match_id:
                continue
            if not isinstance(metadata, dict):
                metadata = {}

            normalized: dict[str, str | int | float | bool] = {}
            for key, value in metadata.items():
                if isinstance(value, (str, int, float, bool)):
                    normalized[str(key)] = value
                else:
                    normalized[str(key)] = str(value)

            records.append(VectorSearchMatch(id=match_id, score=match_score, metadata=normalized))

        return records


def build_vector_index(settings: Settings) -> VectorIndex:
    if settings.vector_index_backend.lower() == "memory":
        return InMemoryVectorIndex()
    return PineconeVectorIndex(settings)
