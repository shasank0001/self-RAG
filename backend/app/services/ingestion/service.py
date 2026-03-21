from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from math import ceil
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.api.middleware.request_context import bind_request_context
from app.core.config import Settings, get_settings
from app.core.tracing import start_span
from app.core.errors import BadRequestError, NotFoundError
from app.observability.events import LogEvent
from app.observability.metrics import get_metrics_registry
from app.models.bin import Bin
from app.models.ingestion_job import IngestionJob, IngestionStatus
from app.models.item import Item, ItemSourceType
from app.models.user import User
from app.services.ingestion.chunking import build_chunks
from app.services.ingestion.embeddings import EmbeddingProvider, build_embedding_provider, validate_embedding_dimensions
from app.services.ingestion.errors import IngestionError, ParserExecutionError
from app.services.ingestion.metrics import get_ingestion_metrics_registry
from app.services.ingestion.parsers import ParsePayload, ParserFactory
from app.services.ingestion.vector_index import VectorIndex, VectorPayload, build_vector_index

logger = logging.getLogger(__name__)
metrics = get_ingestion_metrics_registry()
obs_metrics = get_metrics_registry()

_ALLOWED_TRANSITIONS: dict[IngestionStatus, set[IngestionStatus]] = {
    IngestionStatus.QUEUED: {IngestionStatus.RUNNING},
    IngestionStatus.RUNNING: {IngestionStatus.QUEUED, IngestionStatus.SUCCEEDED, IngestionStatus.FAILED},
    IngestionStatus.SUCCEEDED: set(),
    IngestionStatus.FAILED: set(),
}


@dataclass(slots=True)
class QueuedIngestionResult:
    item: Item
    job: IngestionJob


@dataclass(slots=True)
class FailureAction:
    requeued: bool
    retry_delay_seconds: int | None


def _apply_transition(
    job: IngestionJob,
    *,
    to_status: IngestionStatus,
    now: datetime,
    last_error: str | None = None,
    next_attempt_at: datetime | None = None,
) -> None:
    if to_status != job.status and to_status not in _ALLOWED_TRANSITIONS[job.status]:
        raise ValueError(f"Invalid transition from {job.status.value} to {to_status.value}")

    job.status = to_status
    job.last_error = last_error

    if to_status == IngestionStatus.RUNNING:
        if job.started_at is None:
            job.started_at = now
        job.completed_at = None

    if to_status == IngestionStatus.QUEUED:
        job.next_attempt_at = next_attempt_at or now
        job.completed_at = None

    if to_status in {IngestionStatus.SUCCEEDED, IngestionStatus.FAILED}:
        job.completed_at = now


def _create_job(
    *,
    user_id: UUID,
    bin_id: UUID,
    item_id: UUID,
    source_name: str,
    max_attempts: int,
) -> IngestionJob:
    now = datetime.now(UTC)
    return IngestionJob(
        user_id=user_id,
        bin_id=bin_id,
        item_id=item_id,
        source_name=source_name,
        status=IngestionStatus.QUEUED,
        attempt_count=0,
        max_attempts=max_attempts,
        queued_at=now,
        next_attempt_at=now,
    )


async def queue_text_ingestion(
    session: AsyncSession,
    *,
    user: User,
    bin_record: Bin,
    text: str,
    source_name: str | None,
    settings: Settings | None = None,
) -> QueuedIngestionResult:
    settings = settings or get_settings()
    if not text.strip():
        raise BadRequestError(code="invalid_text", message="Text payload must not be empty")

    normalized_name = source_name.strip() if source_name else f"text-{uuid4().hex}.txt"
    item = Item(
        user_id=user.id,
        bin_id=bin_record.id,
        source_type=ItemSourceType.TEXT,
        source_name=normalized_name,
        media_type="text/plain",
        raw_text=text,
    )
    session.add(item)
    await session.flush()

    job = _create_job(
        user_id=user.id,
        bin_id=bin_record.id,
        item_id=item.id,
        source_name=item.source_name,
        max_attempts=settings.ingestion_max_attempts,
    )
    session.add(job)

    await session.commit()
    await session.refresh(item)
    await session.refresh(job)
    metrics.incr("queued", user_id=user.id)
    return QueuedIngestionResult(item=item, job=job)


async def queue_upload_ingestion(
    session: AsyncSession,
    *,
    user: User,
    bin_record: Bin,
    source_name: str,
    media_type: str,
    storage_path: str,
    settings: Settings | None = None,
) -> QueuedIngestionResult:
    settings = settings or get_settings()

    item = Item(
        user_id=user.id,
        bin_id=bin_record.id,
        source_type=ItemSourceType.UPLOAD,
        source_name=source_name,
        media_type=media_type,
        storage_path=storage_path,
    )
    session.add(item)
    await session.flush()

    job = _create_job(
        user_id=user.id,
        bin_id=bin_record.id,
        item_id=item.id,
        source_name=item.source_name,
        max_attempts=settings.ingestion_max_attempts,
    )
    session.add(job)

    await session.commit()
    await session.refresh(item)
    await session.refresh(job)
    metrics.incr("queued", user_id=user.id)
    return QueuedIngestionResult(item=item, job=job)


def persist_uploaded_file(*, source_name: str, content: bytes, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    storage_root = Path(settings.ingestion_storage_dir)
    if not storage_root.is_absolute():
        storage_root = (Path.cwd() / storage_root).resolve()

    storage_root.mkdir(parents=True, exist_ok=True)
    extension = Path(source_name).suffix.lower()
    target_path = storage_root / f"{uuid4()}{extension}"
    target_path.write_bytes(content)
    return str(target_path)


async def claim_next_queued_job(session: AsyncSession) -> UUID | None:
    now = datetime.now(UTC)
    result = await session.execute(
        select(IngestionJob)
        .where(
            IngestionJob.status == IngestionStatus.QUEUED,
            IngestionJob.next_attempt_at <= now,
        )
        .order_by(IngestionJob.queued_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job = result.scalar_one_or_none()
    if job is None:
        return None

    _apply_transition(job, to_status=IngestionStatus.RUNNING, now=now)
    job.attempt_count += 1
    await session.commit()
    metrics.incr("running", user_id=job.user_id)
    return job.id


async def get_seconds_until_next_queued_job(session: AsyncSession) -> int | None:
    now = datetime.now(UTC)
    result = await session.execute(
        select(func.min(IngestionJob.next_attempt_at)).where(IngestionJob.status == IngestionStatus.QUEUED)
    )
    next_attempt = result.scalar_one_or_none()
    if next_attempt is None:
        return None
    if next_attempt <= now:
        return 0
    return max(1, ceil((next_attempt - now).total_seconds()))


async def recover_running_jobs_after_restart(session: AsyncSession) -> int:
    now = datetime.now(UTC)
    result = await session.execute(
        select(IngestionJob)
        .where(IngestionJob.status == IngestionStatus.RUNNING)
        .with_for_update(skip_locked=True)
    )
    running_jobs = list(result.scalars().all())
    if not running_jobs:
        return 0

    for job in running_jobs:
        _apply_transition(
            job,
            to_status=IngestionStatus.QUEUED,
            now=now,
            last_error=job.last_error,
            next_attempt_at=now,
        )
        metrics.incr("recovered", user_id=job.user_id)

    await session.commit()
    return len(running_jobs)


def _log_stage(job: IngestionJob, stage: str, duration_ms: float) -> None:
    logger.info(
        LogEvent.INGESTION_STAGE_COMPLETED,
        extra={
            "job_id": str(job.id),
            "item_id": str(job.item_id) if job.item_id else None,
            "bin_id": str(job.bin_id),
            "stage": stage,
            "duration_ms": round(duration_ms, 2),
        },
    )
    obs_metrics.observe(
        "selfrag_ingestion_stage_latency_ms",
        duration_ms,
        stage=stage,
    )
    metrics.observe_stage(stage, duration_ms, user_id=job.user_id)


def _load_file_bytes(item: Item) -> bytes | None:
    if item.source_type == ItemSourceType.TEXT:
        return None
    if not item.storage_path:
        raise ParserExecutionError(message="Upload item is missing storage_path")

    path = Path(item.storage_path)
    if not path.exists():
        raise ParserExecutionError(message="Uploaded source file is missing from storage")
    return path.read_bytes()


async def process_running_job(
    session: AsyncSession,
    *,
    job_id: UUID,
    settings: Settings | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    vector_index: VectorIndex | None = None,
) -> None:
    settings = settings or get_settings()
    embedding_provider = embedding_provider or build_embedding_provider(settings)
    vector_index = vector_index or build_vector_index(settings)

    result = await session.execute(
        select(IngestionJob)
        .where(IngestionJob.id == job_id)
        .options(joinedload(IngestionJob.item), joinedload(IngestionJob.bin))
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise NotFoundError(message="Ingestion job not found")
    if job.status != IngestionStatus.RUNNING:
        raise IngestionError(
            code="invalid_job_status",
            message="Only running jobs can be processed",
            stage="orchestrator",
            retriable=False,
        )
    if job.item is None:
        raise IngestionError(code="missing_item", message="Ingestion job is missing linked item", stage="orchestrator")
    if job.bin is None:
        raise IngestionError(code="missing_bin", message="Ingestion job is missing linked bin", stage="orchestrator")

    with bind_request_context(job_id=str(job.id), bin_id=str(job.bin_id)):
        logger.info(
            LogEvent.INGESTION_JOB_STARTED,
            extra={"job_id": str(job.id), "bin_id": str(job.bin_id), "source_name": job.source_name},
        )
        obs_metrics.inc("selfrag_ingestion_jobs_total", status="running")

        parse_start = time.perf_counter()
        with start_span("ingestion.parse", attributes={"job_id": str(job.id), "source_type": job.item.source_type.value}):
            payload = ParsePayload(
                source_name=job.item.source_name,
                source_type=job.item.source_type,
                media_type=job.item.media_type,
                raw_text=job.item.raw_text,
                file_bytes=_load_file_bytes(job.item),
            )
            parser = ParserFactory.create_parser(payload)
            parsed = parser.parse(payload)
        _log_stage(job, "parse", (time.perf_counter() - parse_start) * 1000)

    content_hash = sha256(parsed.normalized_text.encode("utf-8")).hexdigest()
    job.content_hash = content_hash

    if job.item.content_hash == content_hash and job.item.chunk_count > 0:
        _apply_transition(job, to_status=IngestionStatus.SUCCEEDED, now=datetime.now(UTC))
        job.last_error = None
        await session.commit()
        metrics.incr("succeeded", user_id=job.user_id)
        obs_metrics.inc("selfrag_ingestion_jobs_total", status="succeeded")
        logger.info(LogEvent.INGESTION_SUCCEEDED, extra={"job_id": str(job.id), "idempotency_skip": True})
        _log_stage(job, "idempotency_skip", 0.0)
        return

    chunk_start = time.perf_counter()
    chunk_records = build_chunks(
        text=parsed.normalized_text,
        item_id=job.item.id,
        bin_id=job.item.bin_id,
        source_name=job.item.source_name,
        source_type=job.item.source_type,
        content_hash=content_hash,
        settings=settings,
        parser_metadata=parsed.metadata,
    )
    if not chunk_records:
        raise IngestionError(
            code="no_chunks_generated",
            message="Chunker produced zero chunks",
            stage="chunk",
            retriable=False,
        )
    _log_stage(job, "chunk", (time.perf_counter() - chunk_start) * 1000)

    if job.item.content_hash and job.item.content_hash != content_hash and job.item.chunk_count > 0:
        delete_start = time.perf_counter()
        await vector_index.delete_by_item_id(namespace=job.bin.vector_namespace, item_id=job.item.id)
        _log_stage(job, "delete_old_vectors", (time.perf_counter() - delete_start) * 1000)

    embed_start = time.perf_counter()
    vector_payloads: list[VectorPayload] = []
    batch_size = max(1, settings.ingestion_batch_size)
    with start_span("ingestion.embed", attributes={"job_id": str(job.id), "batch_size": batch_size}) as span:
        for offset in range(0, len(chunk_records), batch_size):
            batch = chunk_records[offset : offset + batch_size]
            embeddings = await embedding_provider.embed([chunk.text for chunk in batch])
            if len(embeddings) != len(batch):
                raise IngestionError(
                    code="embedding_count_mismatch",
                    message="Embedding provider returned mismatched vector count",
                    stage="embed",
                    retriable=True,
                )
            provider_dimensions = getattr(embedding_provider, "dimensions", None)
            if isinstance(provider_dimensions, int) and provider_dimensions > 0:
                validate_embedding_dimensions(
                    vectors=embeddings,
                    expected_dimensions=provider_dimensions,
                    stage="embed",
                )

            for chunk, embedding in zip(batch, embeddings):
                vector_payloads.append(
                    VectorPayload(
                        id=chunk.chunk_id,
                        values=embedding,
                        metadata=chunk.metadata,
                    )
                )
        span.set_attribute("vector_count", len(vector_payloads))
    _log_stage(job, "embed", (time.perf_counter() - embed_start) * 1000)

    upsert_start = time.perf_counter()
    for offset in range(0, len(vector_payloads), batch_size):
        await vector_index.upsert(
            namespace=job.bin.vector_namespace,
            vectors=vector_payloads[offset : offset + batch_size],
        )
    _log_stage(job, "upsert", (time.perf_counter() - upsert_start) * 1000)

    now = datetime.now(UTC)
    job.item.content_hash = content_hash
    job.item.chunk_count = len(chunk_records)
    job.item.embedding_provider = settings.embedding_provider
    job.item.embedding_model = embedding_provider.model_name
    job.item.embedding_dimensions = len(vector_payloads[0].values) if vector_payloads else None
    job.item.last_ingested_at = now

    if job.bin.embedding_provider is None:
        job.bin.embedding_provider = settings.embedding_provider
    if job.bin.embedding_model is None:
        job.bin.embedding_model = embedding_provider.model_name
    if job.bin.embedding_dimensions is None and vector_payloads:
        job.bin.embedding_dimensions = len(vector_payloads[0].values)

    if (
        job.bin.embedding_provider not in {None, settings.embedding_provider}
        or job.bin.embedding_model not in {None, embedding_provider.model_name}
        or (
            job.bin.embedding_dimensions is not None
            and vector_payloads
            and job.bin.embedding_dimensions != len(vector_payloads[0].values)
        )
    ):
        raise IngestionError(
            code="embedding_alignment_mismatch",
            message="Bin embedding configuration does not match current ingestion embedding settings",
            stage="embed",
            retriable=False,
        )

    _apply_transition(job, to_status=IngestionStatus.SUCCEEDED, now=now, last_error=None)

    await session.commit()
    metrics.incr("succeeded", user_id=job.user_id)
    obs_metrics.inc("selfrag_ingestion_jobs_total", status="succeeded")
    obs_metrics.observe("selfrag_ingestion_chunks_per_job", float(len(chunk_records)))
    logger.info(LogEvent.INGESTION_SUCCEEDED, extra={"job_id": str(job.id), "chunk_count": len(chunk_records)})


async def handle_job_failure(
    session: AsyncSession,
    *,
    job_id: UUID,
    error: Exception,
    settings: Settings | None = None,
) -> FailureAction:
    settings = settings or get_settings()

    result = await session.execute(select(IngestionJob).where(IngestionJob.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        return FailureAction(requeued=False, retry_delay_seconds=None)

    now = datetime.now(UTC)
    message = str(error)[:2000]
    job.last_error = message

    retriable = True
    if isinstance(error, IngestionError):
        retriable = error.retriable

    if retriable and job.attempt_count < job.max_attempts:
        retry_delay = settings.ingestion_retry_backoff_seconds * (2 ** max(job.attempt_count - 1, 0))
        next_attempt = now + timedelta(seconds=retry_delay)
        _apply_transition(
            job,
            to_status=IngestionStatus.QUEUED,
            now=now,
            last_error=message,
            next_attempt_at=next_attempt,
        )
        await session.commit()
        metrics.incr("retries", user_id=job.user_id)
        logger.warning(
            LogEvent.INGESTION_RETRY_SCHEDULED,
            extra={
                "job_id": str(job.id),
                "item_id": str(job.item_id) if job.item_id else None,
                "bin_id": str(job.bin_id),
                "retry_delay_seconds": retry_delay,
                "attempt_count": job.attempt_count,
            },
        )
        obs_metrics.inc("selfrag_ingestion_jobs_total", status="requeued")
        return FailureAction(requeued=True, retry_delay_seconds=retry_delay)

    _apply_transition(job, to_status=IngestionStatus.FAILED, now=now, last_error=message)
    await session.commit()
    metrics.incr("failed", user_id=job.user_id)
    metrics.add_failure(
        {
            "job_id": str(job.id),
            "item_id": str(job.item_id) if job.item_id else None,
            "bin_id": str(job.bin_id),
            "last_error": message,
            "attempt_count": job.attempt_count,
            "max_attempts": job.max_attempts,
            "failed_at": now.isoformat(),
        },
        user_id=job.user_id,
    )

    logger.error(
        LogEvent.INGESTION_FAILED,
        extra={
            "job_id": str(job.id),
            "item_id": str(job.item_id) if job.item_id else None,
            "bin_id": str(job.bin_id),
            "attempt_count": job.attempt_count,
            "max_attempts": job.max_attempts,
        },
    )
    obs_metrics.inc("selfrag_ingestion_jobs_total", status="failed")
    return FailureAction(requeued=False, retry_delay_seconds=None)


async def get_latest_job_for_item(
    session: AsyncSession,
    *,
    user_id: UUID,
    bin_id: UUID,
    item_id: UUID,
) -> IngestionJob:
    result = await session.execute(
        select(IngestionJob)
        .join(Item, IngestionJob.item_id == Item.id)
        .where(
            IngestionJob.item_id == item_id,
            IngestionJob.bin_id == bin_id,
            Item.user_id == user_id,
            Item.bin_id == bin_id,
        )
        .order_by(IngestionJob.created_at.desc())
        .limit(1)
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise NotFoundError(message="Ingestion status not found for item")
    return job


async def get_job_for_user(session: AsyncSession, *, user_id: UUID, job_id: UUID) -> IngestionJob:
    result = await session.execute(select(IngestionJob).where(IngestionJob.id == job_id, IngestionJob.user_id == user_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise NotFoundError(message="Ingestion job not found")
    return job


async def list_recent_failures(
    session: AsyncSession,
    *,
    user_id: UUID,
    limit: int,
    bin_id: UUID | None = None,
) -> list[IngestionJob]:
    conditions = [
        IngestionJob.user_id == user_id,
        IngestionJob.status == IngestionStatus.FAILED,
    ]
    if bin_id is not None:
        conditions.append(IngestionJob.bin_id == bin_id)

    result = await session.execute(
        select(IngestionJob)
        .where(*conditions)
        .order_by(IngestionJob.updated_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())
