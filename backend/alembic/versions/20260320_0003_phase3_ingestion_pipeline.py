"""phase3 ingestion pipeline schema

Revision ID: 20260320_0003
Revises: 20260320_0002
Create Date: 2026-03-20 01:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260320_0003"
down_revision: Union[str, Sequence[str], None] = "20260320_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "items",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bin_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_type", sa.Enum("upload", "text", name="item_source_type"), nullable=False),
        sa.Column("source_name", sa.String(length=255), nullable=False),
        sa.Column("media_type", sa.String(length=100), nullable=True),
        sa.Column("storage_path", sa.Text(), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("chunk_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("embedding_model", sa.String(length=255), nullable=True),
        sa.Column("last_ingested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["bin_id"], ["bins.id"], name=op.f("fk_items_bin_id_bins"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_items_user_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_items")),
    )
    op.create_index(op.f("ix_items_user_id"), "items", ["user_id"], unique=False)
    op.create_index(op.f("ix_items_bin_id"), "items", ["bin_id"], unique=False)
    op.create_index("ix_items_created_at", "items", ["created_at"], unique=False)

    op.add_column("ingestion_jobs", sa.Column("item_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("ingestion_jobs", sa.Column("content_hash", sa.String(length=64), nullable=True))
    op.add_column(
        "ingestion_jobs",
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "ingestion_jobs",
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("3"), nullable=False),
    )
    op.alter_column("ingestion_jobs", "error_message", new_column_name="last_error")
    op.add_column(
        "ingestion_jobs",
        sa.Column("queued_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
    )
    op.add_column(
        "ingestion_jobs",
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
    )

    op.create_foreign_key(
        op.f("fk_ingestion_jobs_item_id_items"),
        "ingestion_jobs",
        "items",
        ["item_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(op.f("ix_ingestion_jobs_item_id"), "ingestion_jobs", ["item_id"], unique=False)
    op.create_index(
        "ix_ingestion_jobs_status_next_attempt",
        "ingestion_jobs",
        ["status", "next_attempt_at"],
        unique=False,
    )

    op.execute(
        """
        UPDATE ingestion_jobs
        SET
            queued_at = COALESCE(queued_at, created_at, now()),
            next_attempt_at = COALESCE(next_attempt_at, created_at, now())
        """
    )
    op.alter_column("ingestion_jobs", "queued_at", existing_type=sa.DateTime(timezone=True), nullable=False)
    op.alter_column("ingestion_jobs", "next_attempt_at", existing_type=sa.DateTime(timezone=True), nullable=False)


def downgrade() -> None:
    op.drop_index("ix_ingestion_jobs_status_next_attempt", table_name="ingestion_jobs")
    op.drop_index(op.f("ix_ingestion_jobs_item_id"), table_name="ingestion_jobs")
    op.drop_constraint(op.f("fk_ingestion_jobs_item_id_items"), "ingestion_jobs", type_="foreignkey")
    op.drop_column("ingestion_jobs", "next_attempt_at")
    op.drop_column("ingestion_jobs", "queued_at")
    op.alter_column("ingestion_jobs", "last_error", new_column_name="error_message")
    op.drop_column("ingestion_jobs", "max_attempts")
    op.drop_column("ingestion_jobs", "attempt_count")
    op.drop_column("ingestion_jobs", "content_hash")
    op.drop_column("ingestion_jobs", "item_id")

    op.drop_index("ix_items_created_at", table_name="items")
    op.drop_index(op.f("ix_items_bin_id"), table_name="items")
    op.drop_index(op.f("ix_items_user_id"), table_name="items")
    op.drop_table("items")
    op.execute("DROP TYPE IF EXISTS item_source_type")
