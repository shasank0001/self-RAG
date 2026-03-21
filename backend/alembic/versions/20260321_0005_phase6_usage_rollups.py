"""phase6 usage rollups

Revision ID: 20260321_0005
Revises: 20260320_0004
Create Date: 2026-03-21 00:05:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260321_0005"
down_revision: Union[str, Sequence[str], None] = "20260320_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "usage_rollups",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("requests", sa.Integer(), server_default="0", nullable=False),
        sa.Column("tokens_in", sa.Integer(), server_default="0", nullable=False),
        sa.Column("tokens_out", sa.Integer(), server_default="0", nullable=False),
        sa.Column("provider_reported_cost_usd", sa.Float(), server_default="0", nullable=False),
        sa.Column("estimated_cost_usd", sa.Float(), server_default="0", nullable=False),
        sa.Column("fallback_events", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage_rollups")),
    )
    op.create_index("ix_usage_rollups_day", "usage_rollups", ["day"], unique=False)
    op.create_index(
        "ix_usage_rollups_provider_model_day",
        "usage_rollups",
        ["provider", "model", "day"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_usage_rollups_provider_model_day", table_name="usage_rollups")
    op.drop_index("ix_usage_rollups_day", table_name="usage_rollups")
    op.drop_table("usage_rollups")
