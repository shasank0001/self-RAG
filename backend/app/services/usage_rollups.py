from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.usage_rollup import UsageRollup
from app.observability.metrics import UsageRollupRecord


async def upsert_usage_rollup(session: AsyncSession, record: UsageRollupRecord) -> UsageRollup:
    result = await session.execute(
        select(UsageRollup).where(
            UsageRollup.provider == record.provider,
            UsageRollup.model == record.model,
            UsageRollup.day == record.day,
        )
    )
    existing = result.scalar_one_or_none()
    if existing is None:
        existing = UsageRollup(
            provider=record.provider,
            model=record.model,
            day=record.day,
            requests=record.requests,
            tokens_in=record.tokens_in,
            tokens_out=record.tokens_out,
            provider_reported_cost_usd=record.provider_reported_cost_usd,
            estimated_cost_usd=record.estimated_cost_usd,
            fallback_events=record.fallback_events,
        )
        session.add(existing)
        await session.flush()
        return existing

    existing.requests += record.requests
    existing.tokens_in += record.tokens_in
    existing.tokens_out += record.tokens_out
    existing.provider_reported_cost_usd += record.provider_reported_cost_usd
    existing.estimated_cost_usd += record.estimated_cost_usd
    existing.fallback_events += record.fallback_events
    existing.updated_at = datetime.now(UTC)
    session.add(existing)
    await session.flush()
    return existing
