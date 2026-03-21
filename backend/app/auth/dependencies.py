import logging
from dataclasses import dataclass

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.clerk import ClerkClaims, verify_clerk_token
from app.core.errors import UnauthorizedError
from app.db.session import get_db_session
from app.models.user import User
from app.services.user_sync import get_or_create_user_from_claims

logger = logging.getLogger(__name__)
bearer_scheme = HTTPBearer(auto_error=False)


@dataclass
class AuthContext:
    claims: ClerkClaims
    user: User


async def get_current_claims(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> ClerkClaims:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise UnauthorizedError(code="missing_token", message="Authentication token missing")

    token = credentials.credentials
    try:
        return verify_clerk_token(token)
    except UnauthorizedError:
        logger.warning("token verification failed")
        raise


async def get_current_user(
    claims: ClerkClaims = Depends(get_current_claims),
    session: AsyncSession = Depends(get_db_session),
) -> User:
    sync_result = await get_or_create_user_from_claims(session, claims)
    await session.commit()
    return sync_result.user


async def get_auth_context(
    claims: ClerkClaims = Depends(get_current_claims),
    session: AsyncSession = Depends(get_db_session),
) -> AuthContext:
    sync_result = await get_or_create_user_from_claims(session, claims)
    await session.commit()
    return AuthContext(claims=claims, user=sync_result.user)
