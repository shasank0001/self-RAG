from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user
from app.core.errors import NotFoundError
from app.db.session import get_db_session
from app.models.chat_message import ChatMessage, MessageRole, RetrievalMode
from app.models.chat_session import ChatSession
from app.models.user import User

router = APIRouter(prefix="/sessions/{session_id}/messages", tags=["messages"])


class MessageCreateRequest(BaseModel):
    role: MessageRole
    content: str
    bin_ids_used: list[UUID] = Field(default_factory=list)
    retrieval_mode: RetrievalMode | None = None
    citations: dict | list[dict] = Field(default_factory=list)
    provider_metadata: dict = Field(default_factory=dict)
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    user_id: UUID | None = None


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    session_id: UUID
    user_id: UUID
    role: MessageRole
    content: str
    bin_ids_used: list[UUID]
    retrieval_mode: RetrievalMode | None
    citations: dict | list[dict]
    provider_metadata: dict
    prompt_versions: dict[str, str]


async def _require_owned_session(session: AsyncSession, session_id: UUID, current_user: User) -> ChatSession:
    result = await session.execute(
        select(ChatSession).where(ChatSession.id == session_id, ChatSession.user_id == current_user.id)
    )
    session_record = result.scalar_one_or_none()
    if session_record is None:
        raise NotFoundError(message="Session not found")
    return session_record


@router.get(
    "",
    response_model=list[MessageResponse],
    summary="List chat messages",
    description="Lists messages for an owned chat session.",
)
async def list_messages(
    session_id: UUID,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> list[MessageResponse]:
    await _require_owned_session(session, session_id, current_user)

    result = await session.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id, ChatMessage.user_id == current_user.id)
        .order_by(ChatMessage.created_at.asc())
    )
    messages = result.scalars().all()
    return [MessageResponse.model_validate(item) for item in messages]


@router.post(
    "",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create chat message",
    description="Creates a message on an owned chat session.",
)
async def create_message(
    session_id: UUID,
    payload: MessageCreateRequest,
    session: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
) -> MessageResponse:
    await _require_owned_session(session, session_id, current_user)

    message = ChatMessage(
        session_id=session_id,
        user_id=current_user.id,
        role=payload.role,
        content=payload.content,
        bin_ids_used=payload.bin_ids_used,
        retrieval_mode=payload.retrieval_mode,
        citations=payload.citations,
        provider_metadata=payload.provider_metadata,
        prompt_versions=payload.prompt_versions,
    )
    session.add(message)
    await session.commit()
    await session.refresh(message)
    return MessageResponse.model_validate(message)
