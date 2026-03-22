from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.core.config import get_settings
from app.core.errors import BadRequestError
from app.db.session import get_db_session
from app.models.ingestion_job import IngestionJob, IngestionStatus
from app.models.user import User
from app.services.authorization import require_bin_owner
from app.services.ingestion.metrics import get_ingestion_metrics_registry
from app.services.ingestion.errors import ParserUnsupportedTypeError
from app.services.ingestion.parsers import ParserFactory
from app.services.ingestion.service import (
    get_job_for_user,
    get_latest_job_for_item,
    list_recent_failures,
    persist_uploaded_file,
    queue_text_ingestion,
    queue_upload_ingestion,
)
from app.services.ingestion.worker import schedule_ingestion_worker

router = APIRouter(tags=["ingestion"])


class TextIngestionRequest(BaseModel):
    text: str = Field(min_length=1)
    source_name: str | None = None


class IngestionAcceptedResponse(BaseModel):
    job_id: UUID
    item_id: UUID
    bin_id: UUID
    status: IngestionStatus
    attempt_count: int
    max_attempts: int


class IngestionStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    job_id: UUID
    item_id: UUID | None
    bin_id: UUID
    status: IngestionStatus
    content_hash: str | None
    attempt_count: int
    max_attempts: int
    last_error: str | None
    queued_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    updated_at: datetime


class IngestionMetricsResponse(BaseModel):
    counters: dict[str, int]
    timers_ms: dict[str, dict[str, float | int]]
    recent_failures: list[dict]



def _to_status_response(job: IngestionJob) -> IngestionStatusResponse:
    return IngestionStatusResponse(
        job_id=job.id,
        item_id=job.item_id,
        bin_id=job.bin_id,
        status=job.status,
        content_hash=job.content_hash,
        attempt_count=job.attempt_count,
        max_attempts=job.max_attempts,
        last_error=job.last_error,
        queued_at=job.queued_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        updated_at=job.updated_at,
    )


@router.post(
    "/bins/{bin_id}/items/text",
    response_model=IngestionAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue text ingestion",
    description="Creates a text item and queues an asynchronous ingestion job.",
)
async def queue_text_item_ingestion(
    bin_id: UUID,
    payload: TextIngestionRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> IngestionAcceptedResponse:
    bin_record = await require_bin_owner(session, bin_id=bin_id, owner_user_id=current_user.id)
    queued = await queue_text_ingestion(
        session,
        user=current_user,
        bin_record=bin_record,
        text=payload.text,
        source_name=payload.source_name,
    )
    await schedule_ingestion_worker()
    return IngestionAcceptedResponse(
        job_id=queued.job.id,
        item_id=queued.item.id,
        bin_id=queued.job.bin_id,
        status=queued.job.status,
        attempt_count=queued.job.attempt_count,
        max_attempts=queued.job.max_attempts,
    )


@router.post(
    "/bins/{bin_id}/items/upload",
    response_model=IngestionAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue file upload ingestion",
    description="Accepts a file upload and queues asynchronous ingestion.",
)
async def queue_upload_item_ingestion(
    bin_id: UUID,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> IngestionAcceptedResponse:
    settings = get_settings()
    max_bytes = settings.upload_max_mb * 1024 * 1024

    source_name = file.filename or f"upload-{uuid4().hex}.txt"
    try:
        media_type = ParserFactory.resolve_upload_media_type(source_name=source_name, media_type=file.content_type)
    except ParserUnsupportedTypeError as exc:
        raise BadRequestError(code=exc.code, message=exc.message) from exc

    content = await file.read(max_bytes + 1)
    if not content:
        raise BadRequestError(code="empty_upload", message="Uploaded file is empty")
    if len(content) > max_bytes:
        raise BadRequestError(
            code="upload_too_large",
            message=f"Uploaded file exceeds {settings.upload_max_mb}MB limit",
        )

    bin_record = await require_bin_owner(session, bin_id=bin_id, owner_user_id=current_user.id)
    storage_path = persist_uploaded_file(source_name=source_name, content=content, settings=settings)

    try:
        queued = await queue_upload_ingestion(
            session,
            user=current_user,
            bin_record=bin_record,
            source_name=source_name,
            media_type=media_type,
            storage_path=storage_path,
            settings=settings,
        )
    except Exception:
        Path(storage_path).unlink(missing_ok=True)
        raise

    await schedule_ingestion_worker()
    return IngestionAcceptedResponse(
        job_id=queued.job.id,
        item_id=queued.item.id,
        bin_id=queued.job.bin_id,
        status=queued.job.status,
        attempt_count=queued.job.attempt_count,
        max_attempts=queued.job.max_attempts,
    )


@router.get(
    "/bins/{bin_id}/items/{item_id}/ingestion-status",
    response_model=IngestionStatusResponse,
    summary="Get ingestion status by item",
    description="Returns latest ingestion status for an item in a bin.",
)
async def get_item_ingestion_status(
    bin_id: UUID,
    item_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> IngestionStatusResponse:
    await require_bin_owner(session, bin_id=bin_id, owner_user_id=current_user.id)
    job = await get_latest_job_for_item(session, user_id=current_user.id, bin_id=bin_id, item_id=item_id)
    return _to_status_response(job)


@router.get(
    "/ingestion/jobs/{job_id}",
    response_model=IngestionStatusResponse,
    summary="Get ingestion status by job",
    description="Returns ingestion status for a specific job.",
)
async def get_job_ingestion_status(
    job_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> IngestionStatusResponse:
    job = await get_job_for_user(session, user_id=current_user.id, job_id=job_id)
    return _to_status_response(job)


@router.get(
    "/ingestion/failures",
    response_model=list[IngestionStatusResponse],
    summary="List recent ingestion failures",
    description="Returns recently failed ingestion jobs for the authenticated user.",
)
async def get_recent_ingestion_failures(
    bin_id: UUID | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> list[IngestionStatusResponse]:
    records = await list_recent_failures(
        session,
        user_id=current_user.id,
        bin_id=bin_id,
        limit=limit,
    )
    return [_to_status_response(item) for item in records]


@router.get(
    "/ingestion/metrics",
    response_model=IngestionMetricsResponse,
    summary="Get ingestion operational metrics",
    description="Returns ingestion counters, stage timers, and recent failures.",
)
async def get_ingestion_metrics(
    _: User = Depends(get_current_user),
) -> IngestionMetricsResponse:
    snapshot = get_ingestion_metrics_registry().snapshot()
    return IngestionMetricsResponse.model_validate(snapshot)
