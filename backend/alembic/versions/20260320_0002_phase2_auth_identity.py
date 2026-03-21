"""phase2 auth and webhook identity sync

Revision ID: 20260320_0002
Revises: 20260320_0001
Create Date: 2026-03-20 00:30:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260320_0002"
down_revision: Union[str, Sequence[str], None] = "20260320_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False))
    op.add_column("users", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "webhook_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("svix_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhook_events")),
        sa.UniqueConstraint("svix_id", name=op.f("uq_webhook_events_svix_id")),
    )
    op.create_index("ix_webhook_events_event_type", "webhook_events", ["event_type"], unique=False)
    op.create_index("ix_webhook_events_created_at", "webhook_events", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_webhook_events_created_at", table_name="webhook_events")
    op.drop_index("ix_webhook_events_event_type", table_name="webhook_events")
    op.drop_table("webhook_events")

    op.drop_column("users", "deleted_at")
    op.drop_column("users", "is_active")
