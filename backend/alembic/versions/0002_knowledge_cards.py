"""RAG knowledge base: pgvector extension + knowledge_cards table.

Revision ID: 0002_knowledge_cards
Revises: 0001_initial
Create Date: 2026-10-14

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import ARRAY, UUID

# revision identifiers, used by Alembic.
revision: str = "0002_knowledge"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # pgvector is available on Supabase projects; harmless elsewhere if absent
    # only when the DB actually has the extension packaged.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "knowledge_cards",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("script_id", UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("keywords", ARRAY(sa.String()), nullable=False),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("embedding", Vector(768), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_knowledge_cards_user_id_users"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["script_id"], ["scripts.id"], name=op.f("fk_knowledge_cards_script_id_scripts"), ondelete="CASCADE"
        ),
    )
    op.create_index(
        op.f("ix_knowledge_cards_user_id"), "knowledge_cards", ["user_id"], unique=False
    )
    op.create_index(
        op.f("ix_knowledge_cards_script_id"), "knowledge_cards", ["script_id"], unique=False
    )
    # Approximate-nearest-neighbour index for cosine similarity search.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_knowledge_cards_embedding_hnsw "
        "ON knowledge_cards USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.drop_table("knowledge_cards")
