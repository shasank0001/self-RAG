from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.clerk import ClerkClaims
from app.models.user import User


@dataclass
class UserSyncResult:
    user: User
    created: bool


async def get_or_create_user_from_claims(session: AsyncSession, claims: ClerkClaims) -> UserSyncResult:
    try:
        async with session.begin_nested():
            result = await session.execute(select(User).where(User.clerk_user_id == claims.sub))
            user = result.scalar_one_or_none()

            if user is None:
                user = User(clerk_user_id=claims.sub, email=claims.email, is_active=True)
                session.add(user)
                await session.flush()
                return UserSyncResult(user=user, created=True)

            user.email = claims.email
            user.is_active = True
            user.deleted_at = None
            await session.flush()
            return UserSyncResult(user=user, created=False)
    except IntegrityError:
        result = await session.execute(select(User).where(User.clerk_user_id == claims.sub))
        user = result.scalar_one()
        user.email = claims.email
        user.is_active = True
        user.deleted_at = None
        await session.flush()
        return UserSyncResult(user=user, created=False)
