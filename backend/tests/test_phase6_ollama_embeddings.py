from __future__ import annotations

import pytest

from app.core.config import Settings
from app.services.ingestion.embeddings import (
    DeterministicEmbeddingProvider,
    OpenAIEmbeddingProvider,
    OllamaEmbeddingProvider,
    build_embedding_provider,
    build_embedding_provider_from_values,
    validate_embedding_dimensions,
)
from app.services.ingestion.errors import ExternalDependencyError


@pytest.mark.asyncio
async def test_ollama_embedding_provider_calls_embed_endpoint(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json() -> dict[str, object]:
            return {"embeddings": [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url: str, json: dict[str, object]):
            captured["url"] = url
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr("app.services.ingestion.embeddings.httpx.AsyncClient", lambda timeout=30.0: FakeClient())

    provider = OllamaEmbeddingProvider(model_name="qwen3-embedding:4b", dimensions=2560, base_url="http://localhost:11434")
    vectors = await provider.embed(["alpha", "beta"])

    assert captured["url"] == "http://localhost:11434/api/embed"
    assert captured["json"] == {
        "model": "qwen3-embedding:4b",
        "input": ["alpha", "beta"],
        "truncate": True,
        "dimensions": 2560,
    }
    assert len(vectors) == 2


def test_build_embedding_provider_uses_ollama_when_configured() -> None:
    settings = Settings(
        EMBEDDING_PROVIDER="ollama",
        EMBEDDING_MODEL="qwen3-embedding:4b",
        EMBEDDING_DIMENSIONS=2560,
    )
    provider = build_embedding_provider(settings)
    assert isinstance(provider, OllamaEmbeddingProvider)
    assert provider.model_name == "qwen3-embedding:4b"


def test_build_embedding_provider_uses_openrouter_when_configured() -> None:
    settings = Settings(
        APP_NAME="self-rag-knowledge-chat",
        API_BASE_URL="http://localhost:8000",
        EMBEDDING_PROVIDER="openrouter",
        EMBEDDING_MODEL="openai/text-embedding-3-small",
        EMBEDDING_DIMENSIONS=1536,
        OPENROUTER_API_KEY="or-key",
    )
    provider = build_embedding_provider(settings)

    assert isinstance(provider, OpenAIEmbeddingProvider)
    assert provider.provider_name == "openrouter"
    assert provider.model_name == "openai/text-embedding-3-small"
    assert provider.base_url == "https://openrouter.ai/api/v1"
    assert provider.extra_headers == {
        "HTTP-Referer": "http://localhost:8000",
        "X-Title": "self-rag-knowledge-chat",
    }


def test_build_embedding_provider_from_values_preserves_requested_dimensions() -> None:
    provider = build_embedding_provider_from_values(
        provider_name="deterministic",
        model_name="deterministic-v1",
        dimensions=17,
        settings=Settings(),
    )

    assert isinstance(provider, DeterministicEmbeddingProvider)
    assert provider.dimensions == 17


def test_validate_embedding_dimensions_rejects_mismatch() -> None:
    with pytest.raises(ExternalDependencyError) as exc_info:
        validate_embedding_dimensions(vectors=[[0.1, 0.2]], expected_dimensions=3, stage="query_embed")

    assert exc_info.value.stage == "query_embed"
    assert "expected 3" in exc_info.value.message


@pytest.mark.asyncio
async def test_openrouter_embedding_provider_posts_compatible_request(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json() -> dict[str, object]:
            return {"data": [{"embedding": [0.1, 0.2, 0.3]}]}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url: str, headers: dict[str, object], json: dict[str, object]):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr("app.services.ingestion.embeddings.httpx.AsyncClient", lambda timeout=30.0: FakeClient())

    provider = OpenAIEmbeddingProvider(
        model_name="openai/text-embedding-3-small",
        provider_name="openrouter",
        api_key="or-key",
        base_url="https://openrouter.ai/api/v1",
        extra_headers={"HTTP-Referer": "http://localhost:8000", "X-Title": "self-rag-knowledge-chat"},
    )
    vectors = await provider.embed(["alpha"])

    assert captured["url"] == "https://openrouter.ai/api/v1/embeddings"
    assert captured["headers"] == {
        "Authorization": "Bearer or-key",
        "HTTP-Referer": "http://localhost:8000",
        "X-Title": "self-rag-knowledge-chat",
    }
    assert captured["json"] == {
        "model": "openai/text-embedding-3-small",
        "input": ["alpha"],
    }
    assert vectors == [[0.1, 0.2, 0.3]]
