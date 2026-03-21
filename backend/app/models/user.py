from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Index, String, UniqueConstraint, func, true
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.bin import Bin
    from app.models.chat_message import ChatMessage
    from app.models.chat_session import ChatSession
    from app.models.ingestion_job import IngestionJob
    from app.models.item import Item
    from app.models.telemetry_event import TelemetryEvent


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("clerk_user_id"),
        Index("ix_users_clerk_user_id", "clerk_user_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    clerk_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    bins: Mapped[list["Bin"]] = relationship(back_populates="user")
    items: Mapped[list["Item"]] = relationship(back_populates="user")
    ingestion_jobs: Mapped[list["IngestionJob"]] = relationship(back_populates="user")
    chat_sessions: Mapped[list["ChatSession"]] = relationship(back_populates="user")
    chat_messages: Mapped[list["ChatMessage"]] = relationship(back_populates="user")
    telemetry_events: Mapped[list["TelemetryEvent"]] = relationship(back_populates="user")
