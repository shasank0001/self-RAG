import enum
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.bin import Bin
    from app.models.ingestion_job import IngestionJob
    from app.models.user import User


class ItemSourceType(str, enum.Enum):
    UPLOAD = "upload"
    TEXT = "text"


class Item(Base):
    __tablename__ = "items"
    __table_args__ = (Index("ix_items_created_at", "created_at"),)

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    bin_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("bins.id", ondelete="CASCADE"), index=True)
    source_type: Mapped[ItemSourceType] = mapped_column(
        Enum(
            ItemSourceType,
            name="item_source_type",
            values_callable=lambda enum_cls: [item.value for item in enum_cls],
        ),
        nullable=False,
    )
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    storage_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    embedding_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_ingested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="items")
    bin: Mapped["Bin"] = relationship(back_populates="items")
    ingestion_jobs: Mapped[list["IngestionJob"]] = relationship(back_populates="item", cascade="all, delete-orphan")