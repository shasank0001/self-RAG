from __future__ import annotations

import pytest

from app.core.config import Settings
from app.services.ingestion.embeddings import OllamaEmbeddingProvider, build_embedding_provider


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
