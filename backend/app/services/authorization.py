from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models.bin import Bin
from app.models.chat_session import ChatSession


async def require_bin_owner(session: AsyncSession, *, bin_id: UUID, owner_user_id: UUID) -> Bin:
    result = await session.execute(select(Bin).where(Bin.id == bin_id, Bin.user_id == owner_user_id))
    bin_record = result.scalar_one_or_none()
    if bin_record is None:
        raise NotFoundError(message="Bin not found")
    return bin_record


async def require_session_owner(session: AsyncSession, *, session_id: UUID, owner_user_id: UUID) -> ChatSession:
    result = await session.execute(
        select(ChatSession).where(ChatSession.id == session_id, ChatSession.user_id == owner_user_id)
    )
    chat_session = result.scalar_one_or_none()
    if chat_session is None:
        raise NotFoundError(message="Session not found")
    return chat_session


async def list_owned_bins(session: AsyncSession, *, owner_user_id: UUID, bin_ids: list[UUID]) -> list[Bin]:
    if not bin_ids:
        return []

    result = await session.execute(
        select(Bin).where(Bin.user_id == owner_user_id, Bin.id.in_(bin_ids)).order_by(Bin.created_at.asc())
    )
    owned_bins = list(result.scalars().all())
    if len(owned_bins) != len(set(bin_ids)):
        raise NotFoundError(message="One or more bins were not found")
    return owned_bins
