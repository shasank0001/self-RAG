from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth.dependencies import AuthContext, get_auth_context

router = APIRouter(prefix="/auth", tags=["auth"])


class AuthMeResponse(BaseModel):
    user_id: UUID
    clerk_user_id: str
    email: str | None
    subject: str


@router.get(
    "/me",
    response_model=AuthMeResponse,
    summary="Get authenticated principal",
    description="Returns the authenticated Clerk subject and mapped local user.",
)
async def auth_me(context: AuthContext = Depends(get_auth_context)) -> AuthMeResponse:
    return AuthMeResponse(
        user_id=context.user.id,
        clerk_user_id=context.user.clerk_user_id,
        email=context.user.email,
        subject=context.claims.sub,
    )
