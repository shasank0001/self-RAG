from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UsageRollup(Base):
    __tablename__ = "usage_rollups"
    __table_args__ = (
        Index("ix_usage_rollups_day", "day"),
        Index("ix_usage_rollups_provider_model_day", "provider", "model", "day", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)

    requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    provider_reported_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    estimated_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    fallback_events: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
