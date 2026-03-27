from __future__ import annotations

import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.services.ingestion.vector_index import InMemoryVectorIndex, PineconeVectorIndex, VectorPayload


@pytest.mark.asyncio
async def test_in_memory_vector_index_query_orders_by_similarity_and_truncates() -> None:
    item_id = uuid4()
    index = InMemoryVectorIndex()
    await index.upsert(
        namespace="docs",
        vectors=[
            VectorPayload(id="best", values=[1.0, 0.0], metadata={"item_id": str(item_id), "chunk_id": "best"}),
            VectorPayload(id="second", values=[0.8, 0.2], metadata={"item_id": str(item_id), "chunk_id": "second"}),
            VectorPayload(id="third", values=[0.0, 1.0], metadata={"item_id": str(item_id), "chunk_id": "third"}),
        ],
    )

    matches = await index.query(namespace="docs", vector=[1.0, 0.0], top_k=2)

    assert [item.id for item in matches] == ["best", "second"]
    assert matches[0].metadata["chunk_id"] == "best"
    assert len(matches) == 2


@pytest.mark.asyncio
async def test_pinecone_query_requests_metadata_without_values(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeIndex:
        def query(self, **kwargs):
            captured.update(kwargs)
            return {
                "matches": [
                    {
                        "id": "chunk-1",
                        "score": 0.93,
                        "metadata": {"chunk_id": "chunk-1", "source_name": "doc.txt"},
                    }
                ]
            }

    class FakeClient:
        def __init__(self, api_key: str) -> None:
            captured["api_key"] = api_key

        def Index(self, name: str | None = None, host: str | None = None):
            captured["index_name"] = name
            captured["host"] = host
            return FakeIndex()

    monkeypatch.setitem(sys.modules, "pinecone", SimpleNamespace(Pinecone=FakeClient))

    index = PineconeVectorIndex(
        Settings(
            PINECONE_API_KEY="pc-key",
            PINECONE_INDEX_NAME="docs-index",
            PINECONE_HOST="",
            VECTOR_INDEX_BACKEND="pinecone",
        )
    )

    matches = await index.query(namespace="docs", vector=[0.1, 0.2], top_k=3)

    assert captured["index_name"] == "docs-index"
    assert captured["host"] is None
    assert captured["include_metadata"] is True
    assert captured["include_values"] is False
    assert matches[0].id == "chunk-1"
    assert matches[0].metadata["source_name"] == "doc.txt"


@pytest.mark.asyncio
async def test_pinecone_prefers_host_over_index_name(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeIndex:
        def query(self, **kwargs):
            return {"matches": []}

    class FakeClient:
        def __init__(self, api_key: str) -> None:
            captured["api_key"] = api_key

        def Index(self, name: str | None = None, host: str | None = None):
            captured["index_name"] = name
            captured["host"] = host
            return FakeIndex()

    monkeypatch.setitem(sys.modules, "pinecone", SimpleNamespace(Pinecone=FakeClient))

    index = PineconeVectorIndex(
        Settings(
            PINECONE_API_KEY="pc-key",
            PINECONE_INDEX_NAME="docs-index",
            PINECONE_HOST="docs-host.pinecone.io",
            VECTOR_INDEX_BACKEND="pinecone",
        )
    )

    await index.query(namespace="docs", vector=[0.1, 0.2], top_k=1)

    assert captured["host"] == "docs-host.pinecone.io"
    assert captured["index_name"] is None
