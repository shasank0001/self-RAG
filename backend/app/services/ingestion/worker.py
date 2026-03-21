from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from app.core.config import get_settings
from app.observability.events import LogEvent
from app.db.session import SessionLocal
from app.services.ingestion.service import (
    claim_next_queued_job,
    get_seconds_until_next_queued_job,
    handle_job_failure,
    process_running_job,
    recover_running_jobs_after_restart,
)

logger = logging.getLogger(__name__)
_worker_lock = asyncio.Lock()
_worker_task: asyncio.Task[None] | None = None
_wakeup_task: asyncio.Task[None] | None = None
_next_wakeup_at: float | None = None


async def _set_wakeup(delay_seconds: int) -> None:
    global _wakeup_task, _next_wakeup_at

    loop = asyncio.get_running_loop()
    schedule_for = loop.time() + max(0, delay_seconds)

    async with _worker_lock:
        if (
            _wakeup_task is not None
            and not _wakeup_task.done()
            and _next_wakeup_at is not None
            and _next_wakeup_at <= schedule_for
        ):
            return

        if _wakeup_task is not None and not _wakeup_task.done():
            _wakeup_task.cancel()

        _next_wakeup_at = schedule_for
        _wakeup_task = asyncio.create_task(_schedule_after(delay_seconds), name="ingestion-worker-wakeup")


async def schedule_ingestion_worker() -> None:
    global _next_wakeup_at, _wakeup_task, _worker_task

    async with _worker_lock:
        if _wakeup_task is not None and not _wakeup_task.done():
            _wakeup_task.cancel()
        _wakeup_task = None
        _next_wakeup_at = None

        if _worker_task is None or _worker_task.done():
            _worker_task = asyncio.create_task(_drain_queue_once(), name="ingestion-worker")


async def _schedule_after(delay_seconds: int) -> None:
    global _next_wakeup_at, _wakeup_task

    await asyncio.sleep(delay_seconds)
    async with _worker_lock:
        _wakeup_task = None
        _next_wakeup_at = None
    await schedule_ingestion_worker()


async def shutdown_ingestion_worker() -> None:
    global _next_wakeup_at, _wakeup_task, _worker_task

    tasks_to_cancel: list[asyncio.Task[None]] = []

    async with _worker_lock:
        if _wakeup_task is not None and not _wakeup_task.done():
            _wakeup_task.cancel()
            tasks_to_cancel.append(_wakeup_task)
        if _worker_task is not None and not _worker_task.done():
            _worker_task.cancel()
            tasks_to_cancel.append(_worker_task)

        _wakeup_task = None
        _worker_task = None
        _next_wakeup_at = None

    for task in tasks_to_cancel:
        with suppress(asyncio.CancelledError):
            await task


async def recover_ingestion_worker() -> None:
    recovered_count = 0
    try:
        async with SessionLocal() as session:
            recovered_count = await recover_running_jobs_after_restart(session)
    except Exception:  # noqa: BLE001
        logger.exception(LogEvent.INGESTION_FAILED)
    else:
        if recovered_count > 0:
            logger.info(LogEvent.INGESTION_RETRY_SCHEDULED, extra={"recovered_jobs": recovered_count})


async def _drain_queue_once() -> None:
    settings = get_settings()

    while True:
        try:
            async with SessionLocal() as claim_session:
                job_id = await claim_next_queued_job(claim_session)
        except Exception:  # noqa: BLE001
            logger.exception(LogEvent.INGESTION_FAILED)
            await _set_wakeup(settings.ingestion_retry_backoff_seconds)
            return

        if job_id is None:
            try:
                async with SessionLocal() as inspect_session:
                    next_delay = await get_seconds_until_next_queued_job(inspect_session)
            except Exception:  # noqa: BLE001
                logger.exception(LogEvent.INGESTION_FAILED)
                next_delay = settings.ingestion_retry_backoff_seconds

            if next_delay is not None:
                await _set_wakeup(next_delay)
            return

        retry_delay: int | None = None
        try:
            async with SessionLocal() as process_session:
                await process_running_job(process_session, job_id=job_id, settings=settings)
        except Exception as exc:  # noqa: BLE001
            logger.exception(LogEvent.INGESTION_FAILED, extra={"job_id": str(job_id)})
            async with SessionLocal() as failure_session:
                action = await handle_job_failure(
                    failure_session,
                    job_id=job_id,
                    error=exc,
                    settings=settings,
                )
                retry_delay = action.retry_delay_seconds if action.requeued else None

        if retry_delay is not None:
            await _set_wakeup(retry_delay)
