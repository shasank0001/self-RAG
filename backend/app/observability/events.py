from __future__ import annotations

from enum import StrEnum


class LogEvent(StrEnum):
    API_REQUEST_STARTED = "api.request.started"
    API_REQUEST_COMPLETED = "api.request.completed"
    API_REQUEST_FAILED = "api.request.failed"
    SSE_STREAM_STARTED = "sse.stream.started"
    SSE_STREAM_RESUMED = "sse.stream.resumed"
    SSE_STREAM_DONE = "sse.stream.done"
    SSE_STREAM_ERROR = "sse.stream.error"

    RETRIEVAL_STARTED = "retrieval.started"
    RETRIEVAL_COMPLETED = "retrieval.completed"
    RETRIEVAL_EMPTY = "retrieval.empty"
    RETRIEVAL_FAILED = "retrieval.failed"

    PROVIDER_CALL_STARTED = "provider.call.started"
    PROVIDER_CALL_COMPLETED = "provider.call.completed"
    PROVIDER_FALLBACK = "provider.fallback"
    PROVIDER_CALL_FAILED = "provider.call.failed"

    INGESTION_JOB_STARTED = "ingestion.job.started"
    INGESTION_STAGE_COMPLETED = "ingestion.stage.completed"
    INGESTION_RETRY_SCHEDULED = "ingestion.retry.scheduled"
    INGESTION_FAILED = "ingestion.failed"
    INGESTION_SUCCEEDED = "ingestion.succeeded"


CRITICAL_LOG_EVENTS: set[LogEvent] = {
    LogEvent.API_REQUEST_STARTED,
    LogEvent.API_REQUEST_COMPLETED,
    LogEvent.SSE_STREAM_STARTED,
    LogEvent.SSE_STREAM_DONE,
    LogEvent.RETRIEVAL_STARTED,
    LogEvent.RETRIEVAL_COMPLETED,
    LogEvent.PROVIDER_CALL_STARTED,
    LogEvent.PROVIDER_CALL_COMPLETED,
    LogEvent.PROVIDER_FALLBACK,
    LogEvent.INGESTION_JOB_STARTED,
    LogEvent.INGESTION_STAGE_COMPLETED,
    LogEvent.INGESTION_SUCCEEDED,
    LogEvent.INGESTION_FAILED,
}
