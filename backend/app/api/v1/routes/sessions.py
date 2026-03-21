from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.db.session import get_db_session
from app.models.chat_session import ChatSession
from app.models.user import User
from app.services.authorization import require_session_owner

router = APIRouter(prefix="/sessions", tags=["sessions"])


class SessionCreateRequest(BaseModel):
    title: str
    initial_bin_ids: list[UUID] = Field(default_factory=list)
    last_active_bin_ids: list[UUID] = Field(default_factory=list)
    user_id: UUID | None = None


class SessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    title: str
    initial_bin_ids: list[UUID]
    last_active_bin_ids: list[UUID]


class SessionBinsUpdateRequest(BaseModel):
    bin_ids: list[UUID] = Field(default_factory=list)


@router.get(
    "",
    response_model=list[SessionResponse],
    summary="List chat sessions",
    description="Lists chat sessions owned by the authenticated user.",
)
async def list_sessions(
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> list[SessionResponse]:
    result = await session.execute(
        select(ChatSession).where(ChatSession.user_id == current_user.id).order_by(ChatSession.created_at.desc())
    )
    records = result.scalars().all()
    return [SessionResponse.model_validate(item) for item in records]


@router.post(
    "",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create chat session",
    description="Creates a chat session owned by the authenticated user.",
)
async def create_session(
    payload: SessionCreateRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> SessionResponse:
    session_record = ChatSession(
        user_id=current_user.id,
        title=payload.title,
        initial_bin_ids=payload.initial_bin_ids,
        last_active_bin_ids=payload.last_active_bin_ids,
    )
    session.add(session_record)
    await session.commit()
    await session.refresh(session_record)
    return SessionResponse.model_validate(session_record)


@router.get(
    "/{session_id}",
    response_model=SessionResponse,
    summary="Get chat session",
    description="Returns a chat session owned by the authenticated user.",
)
async def get_session(
    session_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> SessionResponse:
    session_record = await require_session_owner(session, session_id=session_id, owner_user_id=current_user.id)
    return SessionResponse.model_validate(session_record)


@router.delete(
    "/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete chat session",
    description="Deletes a chat session owned by the authenticated user.",
)
async def delete_session(
    session_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> None:
    session_record = await require_session_owner(session, session_id=session_id, owner_user_id=current_user.id)
    await session.delete(session_record)
    await session.commit()


@router.patch(
    "/{session_id}/bins",
    response_model=SessionResponse,
    summary="Update active session bins",
    description="Updates `last_active_bin_ids` for an owned session. Changes apply from the next message.",
)
async def update_session_bins(
    session_id: UUID,
    payload: SessionBinsUpdateRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> SessionResponse:
    session_record = await require_session_owner(session, session_id=session_id, owner_user_id=current_user.id)
    session_record.last_active_bin_ids = payload.bin_ids
    session.add(session_record)
    await session.commit()
    await session.refresh(session_record)
    return SessionResponse.model_validate(session_record)
