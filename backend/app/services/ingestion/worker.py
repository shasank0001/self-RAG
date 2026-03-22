from __future__ import annotations

import asyncio
import logging

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.services.ingestion.service import claim_next_queued_job, handle_job_failure, process_running_job

logger = logging.getLogger(__name__)
_worker_lock = asyncio.Lock()
_worker_task: asyncio.Task[None] | None = None


async def schedule_ingestion_worker() -> None:
    global _worker_task

    async with _worker_lock:
        if _worker_task is None or _worker_task.done():
            _worker_task = asyncio.create_task(_drain_queue_once(), name="ingestion-worker")


async def _schedule_after(delay_seconds: int) -> None:
    await asyncio.sleep(delay_seconds)
    await schedule_ingestion_worker()


async def _drain_queue_once() -> None:
    settings = get_settings()

    while True:
        async with SessionLocal() as claim_session:
            job_id = await claim_next_queued_job(claim_session)

        if job_id is None:
            return

        retry_delay: int | None = None
        try:
            async with SessionLocal() as process_session:
                await process_running_job(process_session, job_id=job_id, settings=settings)
        except Exception as exc:  # noqa: BLE001
            logger.exception("ingestion worker failed while processing a job", extra={"job_id": str(job_id)})
            async with SessionLocal() as failure_session:
                action = await handle_job_failure(
                    failure_session,
                    job_id=job_id,
                    error=exc,
                    settings=settings,
                )
                retry_delay = action.retry_delay_seconds if action.requeued else None

        if retry_delay is not None:
            asyncio.create_task(_schedule_after(retry_delay), name=f"ingestion-worker-retry-{job_id}")
