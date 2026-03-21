from datetime import datetime, UTC
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.models.webhook_event import WebhookEvent


def _extract_email(data: dict[str, Any]) -> str | None:
    email_addresses = data.get("email_addresses")
    if not isinstance(email_addresses, list):
        return None

    primary_email_id = data.get("primary_email_address_id")
    if primary_email_id:
        for candidate in email_addresses:
            if isinstance(candidate, dict) and candidate.get("id") == primary_email_id:
                address = candidate.get("email_address")
                if isinstance(address, str):
                    return address

    for candidate in email_addresses:
        if isinstance(candidate, dict):
            address = candidate.get("email_address")
            if isinstance(address, str):
                return address
    return None


async def _upsert_user(session: AsyncSession, clerk_user_id: str, email: str | None) -> None:
    result = await session.execute(select(User).where(User.clerk_user_id == clerk_user_id))
    user = result.scalar_one_or_none()
    if user is None:
        session.add(User(clerk_user_id=clerk_user_id, email=email, is_active=True, deleted_at=None))
        return

    user.email = email
    user.is_active = True
    user.deleted_at = None


async def _deactivate_user(session: AsyncSession, clerk_user_id: str) -> None:
    result = await session.execute(select(User).where(User.clerk_user_id == clerk_user_id))
    user = result.scalar_one_or_none()
    if user is None:
        session.add(User(clerk_user_id=clerk_user_id, email=None, is_active=False, deleted_at=datetime.now(UTC)))
        return

    user.email = None
    user.is_active = False
    user.deleted_at = datetime.now(UTC)


async def process_clerk_user_lifecycle_event(
    session: AsyncSession,
    *,
    svix_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> bool:
    try:
        async with session.begin_nested():
            existing = await session.execute(select(WebhookEvent.id).where(WebhookEvent.svix_id == svix_id))
            if existing.scalar_one_or_none() is not None:
                return False

            data = payload.get("data")
            if not isinstance(data, dict):
                data = {}

            clerk_user_id = data.get("id")
            if isinstance(clerk_user_id, str) and event_type in {"user.created", "user.updated"}:
                await _upsert_user(session, clerk_user_id=clerk_user_id, email=_extract_email(data))
            elif isinstance(clerk_user_id, str) and event_type == "user.deleted":
                await _deactivate_user(session, clerk_user_id=clerk_user_id)

            session.add(
                WebhookEvent(
                    svix_id=svix_id,
                    event_type=event_type,
                    payload=payload,
                )
            )
            await session.flush()
    except IntegrityError:
        return False

    return True
