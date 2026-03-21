import enum
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.chat_session import ChatSession
    from app.models.user import User


class MessageRole(str, enum.Enum):
    USER = "user"
    ASSISTANT = "assistant"


class RetrievalMode(str, enum.Enum):
    GROUNDED = "grounded"
    PARAMETRIC = "parametric"


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        Index("ix_chat_messages_created_at", "created_at"),
        Index("ix_chat_messages_session_created", "session_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    session_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[MessageRole] = mapped_column(
        Enum(
            MessageRole,
            name="message_role",
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    bin_ids_used: Mapped[list[UUID]] = mapped_column(ARRAY(PGUUID(as_uuid=True)), nullable=False, default=list)
    retrieval_mode: Mapped[RetrievalMode | None] = mapped_column(
        Enum(
            RetrievalMode,
            name="retrieval_mode",
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        nullable=True,
    )
    citations: Mapped[dict[str, Any] | list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    provider_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    prompt_versions: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="chat_messages")
    session: Mapped["ChatSession"] = relationship(back_populates="messages")
