from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID

from app.core.config import Settings
from app.services.ingestion.errors import ExternalDependencyError


@dataclass(slots=True)
class VectorPayload:
    id: str
    values: list[float]
    metadata: dict[str, str | int | float | bool]


class VectorIndex:
    async def upsert(self, *, namespace: str, vectors: list[VectorPayload]) -> None:
        raise NotImplementedError

    async def delete_by_item_id(self, *, namespace: str, item_id: UUID) -> None:
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


class PineconeVectorIndex(VectorIndex):
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = None

    def _index(self):
        if not self._settings.pinecone_api_key:
            raise ExternalDependencyError(stage="upsert", message="PINECONE_API_KEY is required", retriable=False)
        if not self._settings.pinecone_index_name:
            raise ExternalDependencyError(stage="upsert", message="PINECONE_INDEX_NAME is required", retriable=False)

        if self._client is None:
            try:
                from pinecone import Pinecone  # type: ignore[import-not-found]
            except ImportError as exc:
                raise ExternalDependencyError(
                    stage="upsert", message="pinecone package is not installed", retriable=False
                ) from exc
            self._client = Pinecone(api_key=self._settings.pinecone_api_key)
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
        index = self._index()
        try:
            await asyncio.to_thread(index.upsert, vectors=payload, namespace=namespace)
        except Exception as exc:
            raise ExternalDependencyError(stage="upsert", message="Failed to upsert vectors to Pinecone") from exc

    async def delete_by_item_id(self, *, namespace: str, item_id: UUID) -> None:
        index = self._index()
        try:
            await asyncio.to_thread(index.delete, namespace=namespace, filter={"item_id": str(item_id)})
        except Exception as exc:
            raise ExternalDependencyError(
                stage="delete", message="Failed to delete stale vectors by item_id", retriable=True
            ) from exc


def build_vector_index(settings: Settings) -> VectorIndex:
    if settings.vector_index_backend.lower() == "memory":
        return InMemoryVectorIndex()
    return PineconeVectorIndex(settings)
