from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.session import get_db_session
from app.models.usage_rollup import UsageRollup
from app.models.user import User

router = APIRouter(prefix="/observability", tags=["observability"])


class UsageRollupResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    provider: str
    model: str
    day: date
    requests: int
    tokens_in: int
    tokens_out: int
    provider_reported_cost_usd: float
    estimated_cost_usd: float
    fallback_events: int


@router.get(
    "/usage-rollups",
    response_model=list[UsageRollupResponse],
    summary="List usage rollups",
    description="Returns provider/model/day usage and cost rollups.",
)
async def list_usage_rollups(
    provider: str | None = Query(default=None),
    model: str | None = Query(default=None),
    day: date | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_db_session),
    _: User = Depends(get_current_user),
) -> list[UsageRollupResponse]:
    query = select(UsageRollup)
    if provider:
        query = query.where(UsageRollup.provider == provider)
    if model:
        query = query.where(UsageRollup.model == model)
    if day:
        query = query.where(UsageRollup.day == day)

    result = await session.execute(query.order_by(UsageRollup.day.desc()).limit(limit))
    records = result.scalars().all()
    return [UsageRollupResponse.model_validate(item) for item in records]
