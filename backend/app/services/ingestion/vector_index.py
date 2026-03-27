from __future__ import annotations

import asyncio
from dataclasses import dataclass
from math import sqrt
from typing import Any
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
    metadata: dict[str, Any]


class VectorIndex:
    async def upsert(self, *, namespace: str, vectors: list[VectorPayload]) -> None:
        raise NotImplementedError

    async def delete_by_item_id(self, *, namespace: str, item_id: UUID) -> None:
        raise NotImplementedError

    async def query(self, *, namespace: str, vector: list[float], top_k: int) -> list[VectorSearchMatch]:
        raise NotImplementedError


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0

    limit = min(len(left), len(right))
    numerator = sum(left[index] * right[index] for index in range(limit))
    left_norm = sqrt(sum(value * value for value in left[:limit]))
    right_norm = sqrt(sum(value * value for value in right[:limit]))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return numerator / (left_norm * right_norm)


def _match_attr(match: object, name: str) -> object:
    if isinstance(match, dict):
        return match.get(name)
    return getattr(match, name, None)


def _match_metadata(match: object) -> dict[str, Any]:
    metadata = _match_attr(match, "metadata")
    if isinstance(metadata, dict):
        return {str(key): value for key, value in metadata.items()}
    return {}


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
        matches = [
            VectorSearchMatch(
                id=payload.id,
                score=_cosine_similarity(vector, payload.values),
                metadata=dict(payload.metadata),
            )
            for payload in bucket.values()
        ]
        matches.sort(key=lambda item: (-item.score, item.id))
        await asyncio.sleep(0)
        return matches[:top_k]


class PineconeVectorIndex(VectorIndex):
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = None

    def _index(self, *, stage: str):
        if not self._settings.pinecone_api_key:
            raise ExternalDependencyError(stage=stage, message="PINECONE_API_KEY is required", retriable=False)
        if not self._settings.pinecone_host and not self._settings.pinecone_index_name:
            raise ExternalDependencyError(
                stage=stage,
                message="PINECONE_HOST or PINECONE_INDEX_NAME is required",
                retriable=False,
            )

        if self._client is None:
            try:
                from pinecone import Pinecone  # type: ignore[import-not-found]
            except ImportError as exc:
                raise ExternalDependencyError(
                    stage=stage,
                    message="pinecone package is not installed",
                    retriable=False,
                ) from exc
            self._client = Pinecone(api_key=self._settings.pinecone_api_key)

        if self._settings.pinecone_host:
            return self._client.Index(host=self._settings.pinecone_host)
        return self._client.Index(self._settings.pinecone_index_name)

    async def upsert(self, *, namespace: str, vectors: list[VectorPayload]) -> None:
        payload = [
            {
                "id": vector.id,
                "values": vector.values,
                "metadata": vector.metadata,
            }
            for vector in vectors
        ]
        index = self._index(stage="upsert")
        try:
            await asyncio.to_thread(index.upsert, vectors=payload, namespace=namespace)
        except Exception as exc:
            raise ExternalDependencyError(stage="upsert", message="Failed to upsert vectors to Pinecone") from exc

    async def delete_by_item_id(self, *, namespace: str, item_id: UUID) -> None:
        index = self._index(stage="delete")
        try:
            await asyncio.to_thread(index.delete, namespace=namespace, filter={"item_id": str(item_id)})
        except Exception as exc:
            raise ExternalDependencyError(
                stage="delete",
                message="Failed to delete stale vectors by item_id",
                retriable=True,
            ) from exc

    async def query(self, *, namespace: str, vector: list[float], top_k: int) -> list[VectorSearchMatch]:
        index = self._index(stage="query")
        try:
            response = await asyncio.to_thread(
                index.query,
                vector=vector,
                top_k=top_k,
                namespace=namespace,
                include_values=False,
                include_metadata=True,
            )
        except Exception as exc:
            raise ExternalDependencyError(stage="query", message="Failed to query Pinecone vectors") from exc

        raw_matches = _match_attr(response, "matches")
        if not isinstance(raw_matches, list):
            return []

        matches: list[VectorSearchMatch] = []
        for raw_match in raw_matches:
            match_id = _match_attr(raw_match, "id")
            score = _match_attr(raw_match, "score")
            if not isinstance(match_id, str) or score is None:
                continue
            matches.append(
                VectorSearchMatch(
                    id=match_id,
                    score=float(score),
                    metadata=_match_metadata(raw_match),
                )
            )
        return matches


def build_vector_index(settings: Settings) -> VectorIndex:
    if settings.vector_index_backend.lower() == "memory":
        return InMemoryVectorIndex()
    return PineconeVectorIndex(settings)
