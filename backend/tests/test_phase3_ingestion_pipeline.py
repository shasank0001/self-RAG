from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app.api.v1.routes.ingestion as ingestion_routes
import app.services.ingestion.service as ingestion_service
from app.core.config import Settings
from app.db.session import get_db_session
from app.main import create_app
from app.models.bin import Bin
from app.models.ingestion_job import IngestionJob, IngestionStatus
from app.models.item import Item, ItemSourceType
from app.models.user import User
from app.services.ingestion.chunking import ChunkRecord
from app.services.ingestion.errors import IngestionError
from app.services.ingestion.parsers import ParsedText
from app.services.ingestion.service import _apply_transition, handle_job_failure, process_running_job


class FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeSingleJobSession:
    def __init__(self, job: IngestionJob) -> None:
        self.job = job
        self.commits = 0

    async def execute(self, _statement):
        return FakeResult(self.job)

    async def commit(self) -> None:
        self.commits += 1


class FakeRouteSession:
    async def execute(self, _statement):
        return FakeResult(None)

    async def commit(self) -> None:
        return None

    async def refresh(self, _obj) -> None:
        return None

    async def flush(self) -> None:
        return None

    def add(self, _obj) -> None:
        return None


def _make_client(app_user: User) -> TestClient:
    app = create_app()
    fake_session = FakeRouteSession()

    async def override_db_session():
        yield fake_session

    async def override_current_user() -> User:
        return app_user

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[ingestion_routes.get_current_user] = override_current_user
    return TestClient(app)


def test_upload_endpoint_rejects_unsupported_file_type(monkeypatch) -> None:
    app_user = User(id=uuid4(), clerk_user_id="user_phase3", email="phase3@example.com")

    async def fake_bin_owner(*_args, **_kwargs):
        return SimpleNamespace(id=uuid4(), vector_namespace="ns")

    monkeypatch.setattr(ingestion_routes, "require_bin_owner", fake_bin_owner)

    with _make_client(app_user) as client:
        response = client.post(
            f"/api/v1/bins/{uuid4()}/items/upload",
            files={"file": ("image.png", b"fake", "image/png")},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unsupported_source_type"


def test_upload_endpoint_accepts_supported_file_type_and_returns_202(monkeypatch, tmp_path) -> None:
    app_user = User(id=uuid4(), clerk_user_id="user_phase3", email="phase3@example.com")
    bin_id = uuid4()

    queued_item = SimpleNamespace(id=uuid4())
    queued_job = SimpleNamespace(
        id=uuid4(),
        bin_id=bin_id,
        status=IngestionStatus.QUEUED,
        attempt_count=0,
        max_attempts=3,
    )

    async def fake_bin_owner(*_args, **_kwargs):
        return SimpleNamespace(id=bin_id, vector_namespace="ns")

    async def fake_queue_upload(*_args, **_kwargs):
        return SimpleNamespace(item=queued_item, job=queued_job)

    async def fake_schedule_worker() -> None:
        return None

    def fake_persist(*_args, **_kwargs) -> str:
        return str(tmp_path / "stored.txt")

    monkeypatch.setattr(ingestion_routes, "require_bin_owner", fake_bin_owner)
    monkeypatch.setattr(ingestion_routes, "queue_upload_ingestion", fake_queue_upload)
    monkeypatch.setattr(ingestion_routes, "schedule_ingestion_worker", fake_schedule_worker)
    monkeypatch.setattr(ingestion_routes, "persist_uploaded_file", fake_persist)

    with _make_client(app_user) as client:
        response = client.post(
            f"/api/v1/bins/{bin_id}/items/upload",
            files={"file": ("valid.txt", b"hello", "text/plain")},
        )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert body["item_id"] == str(queued_item.id)
    assert body["job_id"] == str(queued_job.id)


def test_upload_endpoint_rejects_oversized_file(monkeypatch) -> None:
    app_user = User(id=uuid4(), clerk_user_id="user_phase3", email="phase3@example.com")

    def fake_settings() -> Settings:
        return Settings(UPLOAD_MAX_MB=1)

    monkeypatch.setattr(ingestion_routes, "get_settings", fake_settings)

    with _make_client(app_user) as client:
        response = client.post(
            f"/api/v1/bins/{uuid4()}/items/upload",
            files={"file": ("large.txt", b"a" * (1024 * 1024 + 1), "text/plain")},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "upload_too_large"


def test_text_endpoint_queues_job_and_returns_202(monkeypatch) -> None:
    app_user = User(id=uuid4(), clerk_user_id="user_phase3", email="phase3@example.com")
    bin_id = uuid4()

    queued_item = SimpleNamespace(id=uuid4())
    queued_job = SimpleNamespace(
        id=uuid4(),
        bin_id=bin_id,
        status=IngestionStatus.QUEUED,
        attempt_count=0,
        max_attempts=3,
    )

    async def fake_bin_owner(*_args, **_kwargs):
        return SimpleNamespace(id=bin_id, vector_namespace="ns")

    async def fake_queue_text(*_args, **_kwargs):
        return SimpleNamespace(item=queued_item, job=queued_job)

    async def fake_schedule_worker() -> None:
        return None

    monkeypatch.setattr(ingestion_routes, "require_bin_owner", fake_bin_owner)
    monkeypatch.setattr(ingestion_routes, "queue_text_ingestion", fake_queue_text)
    monkeypatch.setattr(ingestion_routes, "schedule_ingestion_worker", fake_schedule_worker)

    with _make_client(app_user) as client:
        response = client.post(
            f"/api/v1/bins/{bin_id}/items/text",
            json={"text": "hello phase 3", "source_name": "note.txt"},
        )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert body["item_id"] == str(queued_item.id)
    assert body["job_id"] == str(queued_job.id)


def test_item_status_endpoint_returns_ingestion_payload(monkeypatch) -> None:
    app_user = User(id=uuid4(), clerk_user_id="user_phase3", email="phase3@example.com")
    now = datetime.now(UTC)
    bin_id = uuid4()
    item_id = uuid4()
    job_id = uuid4()

    job = IngestionJob(
        id=job_id,
        user_id=app_user.id,
        bin_id=bin_id,
        item_id=item_id,
        source_name="status.txt",
        status=IngestionStatus.RUNNING,
        content_hash="abc123",
        attempt_count=1,
        max_attempts=3,
        last_error=None,
        queued_at=now,
        next_attempt_at=now,
        started_at=now,
        completed_at=None,
        updated_at=now,
    )

    async def fake_bin_owner(*_args, **_kwargs):
        return SimpleNamespace(id=bin_id, vector_namespace="ns")

    async def fake_latest_job(*_args, **_kwargs):
        return job

    monkeypatch.setattr(ingestion_routes, "require_bin_owner", fake_bin_owner)
    monkeypatch.setattr(ingestion_routes, "get_latest_job_for_item", fake_latest_job)

    with _make_client(app_user) as client:
        response = client.get(f"/api/v1/bins/{bin_id}/items/{item_id}/ingestion-status")

    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == str(job_id)
    assert body["item_id"] == str(item_id)
    assert body["status"] == "running"
    assert body["attempt_count"] == 1


def test_job_status_endpoint_returns_ingestion_payload(monkeypatch) -> None:
    app_user = User(id=uuid4(), clerk_user_id="user_phase3", email="phase3@example.com")
    now = datetime.now(UTC)
    job_id = uuid4()

    job = IngestionJob(
        id=job_id,
        user_id=app_user.id,
        bin_id=uuid4(),
        item_id=uuid4(),
        source_name="job-status.txt",
        status=IngestionStatus.QUEUED,
        attempt_count=0,
        max_attempts=3,
        queued_at=now,
        next_attempt_at=now,
        updated_at=now,
    )

    async def fake_get_job(*_args, **_kwargs):
        return job

    monkeypatch.setattr(ingestion_routes, "get_job_for_user", fake_get_job)

    with _make_client(app_user) as client:
        response = client.get(f"/api/v1/ingestion/jobs/{job_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == str(job_id)
    assert body["status"] == "queued"


def test_recent_failures_endpoint_returns_records(monkeypatch) -> None:
    app_user = User(id=uuid4(), clerk_user_id="user_phase3", email="phase3@example.com")
    now = datetime.now(UTC)

    failed_job = IngestionJob(
        id=uuid4(),
        user_id=app_user.id,
        bin_id=uuid4(),
        item_id=uuid4(),
        source_name="failed.txt",
        status=IngestionStatus.FAILED,
        attempt_count=3,
        max_attempts=3,
        last_error="boom",
        queued_at=now,
        next_attempt_at=now,
        completed_at=now,
        updated_at=now,
    )

    async def fake_list_failures(*_args, **_kwargs):
        return [failed_job]

    monkeypatch.setattr(ingestion_routes, "list_recent_failures", fake_list_failures)

    with _make_client(app_user) as client:
        response = client.get("/api/v1/ingestion/failures?limit=10")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["status"] == "failed"
    assert body[0]["last_error"] == "boom"


def test_ingestion_metrics_endpoint_returns_snapshot(monkeypatch) -> None:
    app_user = User(id=uuid4(), clerk_user_id="user_phase3", email="phase3@example.com")

    class FakeRegistry:
        @staticmethod
        def snapshot() -> dict:
            return {
                "counters": {
                    "queued": 1,
                    "running": 1,
                    "succeeded": 1,
                    "failed": 1,
                    "retries": 1,
                },
                "timers_ms": {
                    "parse": {"count": 1, "avg": 12.5, "max": 12.5},
                },
                "recent_failures": [],
            }

    monkeypatch.setattr(ingestion_routes, "get_ingestion_metrics_registry", lambda: FakeRegistry())

    with _make_client(app_user) as client:
        response = client.get("/api/v1/ingestion/metrics")

    assert response.status_code == 200
    body = response.json()
    assert body["counters"]["queued"] == 1
    assert body["timers_ms"]["parse"]["count"] == 1


def test_transition_guard_blocks_invalid_state_changes() -> None:
    job = IngestionJob(
        id=uuid4(),
        user_id=uuid4(),
        bin_id=uuid4(),
        item_id=uuid4(),
        source_name="demo.txt",
        status=IngestionStatus.SUCCEEDED,
        attempt_count=1,
        max_attempts=3,
        queued_at=datetime.now(UTC),
        next_attempt_at=datetime.now(UTC),
    )

    with pytest.raises(ValueError):
        _apply_transition(job, to_status=IngestionStatus.RUNNING, now=datetime.now(UTC))


@pytest.mark.asyncio
async def test_process_pipeline_runs_parse_chunk_embed_upsert_order(monkeypatch) -> None:
    user_id = uuid4()
    bin_id = uuid4()
    item_id = uuid4()
    job_id = uuid4()

    item = Item(
        id=item_id,
        user_id=user_id,
        bin_id=bin_id,
        source_type=ItemSourceType.TEXT,
        source_name="text-input.txt",
        media_type="text/plain",
        raw_text="hello world",
        chunk_count=0,
    )
    bin_record = Bin(id=bin_id, user_id=user_id, title="Bin", description=None, vector_namespace="bin-ns")
    job = IngestionJob(
        id=job_id,
        user_id=user_id,
        bin_id=bin_id,
        item_id=item_id,
        source_name="text-input.txt",
        status=IngestionStatus.RUNNING,
        attempt_count=1,
        max_attempts=3,
        queued_at=datetime.now(UTC),
        next_attempt_at=datetime.now(UTC),
    )
    job.item = item
    job.bin = bin_record

    events: list[str] = []

    class FakeParser:
        def parse(self, _payload):
            events.append("parse")
            return ParsedText(normalized_text="normalized content", metadata={"parser": "fake"})

    class FakeFactory:
        @staticmethod
        def create_parser(_payload):
            return FakeParser()

    def fake_build_chunks(**_kwargs):
        events.append("chunk")
        return [
            ChunkRecord(
                chunk_id="chunk-1",
                chunk_index=0,
                text="chunk text",
                metadata={"item_id": str(item_id), "bin_id": str(bin_id), "chunk_index": 0},
            )
        ]

    @dataclass
    class FakeEmbeddingProvider:
        model_name: str = "fake-model"

        async def embed(self, _texts):
            events.append("embed")
            return [[0.1, 0.2, 0.3]]

    class FakeVectorIndex:
        def __init__(self) -> None:
            self.metadata_item_id: str | None = None

        async def delete_by_item_id(self, *, namespace: str, item_id):
            events.append(f"delete:{namespace}:{item_id}")

        async def upsert(self, *, namespace: str, vectors):
            events.append("upsert")
            self.metadata_item_id = vectors[0].metadata.get("item_id")

    vector_index = FakeVectorIndex()

    monkeypatch.setattr(ingestion_service, "ParserFactory", FakeFactory)
    monkeypatch.setattr(ingestion_service, "build_chunks", fake_build_chunks)

    session = FakeSingleJobSession(job)
    await process_running_job(
        session,
        job_id=job_id,
        settings=Settings(INGESTION_BATCH_SIZE=16),
        embedding_provider=FakeEmbeddingProvider(),
        vector_index=vector_index,
    )

    assert events[:4] == ["parse", "chunk", "embed", "upsert"]
    assert vector_index.metadata_item_id == str(item_id)
    assert job.status == IngestionStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_idempotency_skip_avoids_embed_and_upsert(monkeypatch) -> None:
    user_id = uuid4()
    bin_id = uuid4()
    item_id = uuid4()
    job_id = uuid4()
    normalized = "same deterministic content"
    content_hash = ingestion_service.sha256(normalized.encode("utf-8")).hexdigest()

    item = Item(
        id=item_id,
        user_id=user_id,
        bin_id=bin_id,
        source_type=ItemSourceType.TEXT,
        source_name="same.txt",
        media_type="text/plain",
        raw_text=normalized,
        content_hash=content_hash,
        chunk_count=3,
    )
    bin_record = Bin(id=bin_id, user_id=user_id, title="Bin", description=None, vector_namespace="bin-ns")
    job = IngestionJob(
        id=job_id,
        user_id=user_id,
        bin_id=bin_id,
        item_id=item_id,
        source_name="same.txt",
        status=IngestionStatus.RUNNING,
        attempt_count=1,
        max_attempts=3,
        queued_at=datetime.now(UTC),
        next_attempt_at=datetime.now(UTC),
    )
    job.item = item
    job.bin = bin_record

    class FakeParser:
        def parse(self, _payload):
            return ParsedText(normalized_text=normalized, metadata={"parser": "fake"})

    class FakeFactory:
        @staticmethod
        def create_parser(_payload):
            return FakeParser()

    class NeverCalledEmbedder:
        model_name = "never"

        async def embed(self, _texts):
            raise AssertionError("embed should not be called for idempotent content")

    class NeverCalledVectorIndex:
        async def delete_by_item_id(self, *, namespace: str, item_id):
            raise AssertionError("delete should not be called for idempotent content")

        async def upsert(self, *, namespace: str, vectors):
            raise AssertionError("upsert should not be called for idempotent content")

    monkeypatch.setattr(ingestion_service, "ParserFactory", FakeFactory)

    session = FakeSingleJobSession(job)
    await process_running_job(
        session,
        job_id=job_id,
        settings=Settings(),
        embedding_provider=NeverCalledEmbedder(),
        vector_index=NeverCalledVectorIndex(),
    )

    assert job.status == IngestionStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_changed_content_deletes_by_item_then_upserts(monkeypatch) -> None:
    user_id = uuid4()
    bin_id = uuid4()
    item_id = uuid4()
    job_id = uuid4()

    item = Item(
        id=item_id,
        user_id=user_id,
        bin_id=bin_id,
        source_type=ItemSourceType.TEXT,
        source_name="changed.txt",
        media_type="text/plain",
        raw_text="new content",
        content_hash="old-hash",
        chunk_count=2,
    )
    bin_record = Bin(id=bin_id, user_id=user_id, title="Bin", description=None, vector_namespace="bin-ns")
    job = IngestionJob(
        id=job_id,
        user_id=user_id,
        bin_id=bin_id,
        item_id=item_id,
        source_name="changed.txt",
        status=IngestionStatus.RUNNING,
        attempt_count=1,
        max_attempts=3,
        queued_at=datetime.now(UTC),
        next_attempt_at=datetime.now(UTC),
    )
    job.item = item
    job.bin = bin_record

    events: list[str] = []

    class FakeParser:
        def parse(self, _payload):
            return ParsedText(normalized_text="new content", metadata={"parser": "fake"})

    class FakeFactory:
        @staticmethod
        def create_parser(_payload):
            return FakeParser()

    def fake_build_chunks(**_kwargs):
        return [
            ChunkRecord(
                chunk_id="chunk-1",
                chunk_index=0,
                text="chunk text",
                metadata={"item_id": str(item_id), "bin_id": str(bin_id), "chunk_index": 0},
            )
        ]

    class FakeEmbedder:
        model_name = "fake"

        async def embed(self, _texts):
            events.append("embed")
            return [[0.1, 0.2, 0.3]]

    class FakeVectorIndex:
        async def delete_by_item_id(self, *, namespace: str, item_id):
            events.append("delete")

        async def upsert(self, *, namespace: str, vectors):
            events.append("upsert")

    monkeypatch.setattr(ingestion_service, "ParserFactory", FakeFactory)
    monkeypatch.setattr(ingestion_service, "build_chunks", fake_build_chunks)

    session = FakeSingleJobSession(job)
    await process_running_job(
        session,
        job_id=job_id,
        settings=Settings(INGESTION_BATCH_SIZE=16),
        embedding_provider=FakeEmbedder(),
        vector_index=FakeVectorIndex(),
    )

    assert events == ["delete", "embed", "upsert"]
    assert job.status == IngestionStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_retry_policy_requeues_then_fails_on_exhaustion() -> None:
    job = IngestionJob(
        id=uuid4(),
        user_id=uuid4(),
        bin_id=uuid4(),
        item_id=uuid4(),
        source_name="retry.txt",
        status=IngestionStatus.RUNNING,
        attempt_count=1,
        max_attempts=2,
        queued_at=datetime.now(UTC),
        next_attempt_at=datetime.now(UTC),
    )
    session = FakeSingleJobSession(job)

    action = await handle_job_failure(
        session,
        job_id=job.id,
        error=RuntimeError("temporary outage"),
        settings=Settings(INGESTION_RETRY_BACKOFF_SECONDS=1),
    )
    assert action.requeued is True
    assert action.retry_delay_seconds == 1
    assert job.status == IngestionStatus.QUEUED

    job.status = IngestionStatus.RUNNING
    job.attempt_count = 2

    action = await handle_job_failure(
        session,
        job_id=job.id,
        error=IngestionError(
            code="terminal",
            message="bad input",
            stage="parse",
            retriable=False,
        ),
        settings=Settings(INGESTION_RETRY_BACKOFF_SECONDS=1),
    )
    assert action.requeued is False
    assert job.status == IngestionStatus.FAILED
