"""phase4 rag graph and routing metadata

Revision ID: 20260320_0004
Revises: 20260320_0003
Create Date: 2026-03-20 03:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "20260320_0004"
down_revision: Union[str, Sequence[str], None] = "20260320_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE TYPE retrieval_mode AS ENUM ('grounded', 'parametric')")

    op.add_column("bins", sa.Column("embedding_provider", sa.String(length=100), nullable=True))
    op.add_column("bins", sa.Column("embedding_model", sa.String(length=255), nullable=True))
    op.add_column("bins", sa.Column("embedding_dimensions", sa.Integer(), nullable=True))

    op.add_column("items", sa.Column("embedding_provider", sa.String(length=100), nullable=True))
    op.add_column("items", sa.Column("embedding_dimensions", sa.Integer(), nullable=True))

    op.add_column(
        "chat_messages",
        sa.Column("retrieval_mode", sa.Enum("grounded", "parametric", name="retrieval_mode"), nullable=True),
    )
    op.add_column(
        "chat_messages",
        sa.Column("provider_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "chat_messages",
        sa.Column("prompt_versions", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    op.execute("UPDATE chat_messages SET provider_metadata = '{}'::jsonb WHERE provider_metadata IS NULL")
    op.execute("UPDATE chat_messages SET prompt_versions = '{}'::jsonb WHERE prompt_versions IS NULL")

    op.alter_column("chat_messages", "provider_metadata", nullable=False)
    op.alter_column("chat_messages", "prompt_versions", nullable=False)


def downgrade() -> None:
    op.drop_column("chat_messages", "prompt_versions")
    op.drop_column("chat_messages", "provider_metadata")
    op.drop_column("chat_messages", "retrieval_mode")

    op.drop_column("items", "embedding_dimensions")
    op.drop_column("items", "embedding_provider")

    op.drop_column("bins", "embedding_dimensions")
    op.drop_column("bins", "embedding_model")
    op.drop_column("bins", "embedding_provider")

    op.execute("DROP TYPE IF EXISTS retrieval_mode")
