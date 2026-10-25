"""Initial PitchPilot schema: users, scripts, coaching_sessions, session_events.

Revision ID: 0001_initial_tables
Revises:
Create Date: 2026-10-12

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, UUID

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=False)

    op.create_table(
        "scripts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("content_outline", sa.Text(), nullable=False),
        sa.Column("target_wpm", sa.Integer(), nullable=False),
        sa.Column("keywords", ARRAY(sa.String()), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_scripts_user_id_users"), ondelete="CASCADE"
        ),
    )
    op.create_index(op.f("ix_scripts_user_id"), "scripts", ["user_id"], unique=False)

    op.create_table(
        "coaching_sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("script_id", UUID(as_uuid=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_coaching_sessions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["script_id"],
            ["scripts.id"],
            name=op.f("fk_coaching_sessions_script_id_scripts"),
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        op.f("ix_coaching_sessions_user_id"), "coaching_sessions", ["user_id"], unique=False
    )
    op.create_index(
        op.f("ix_coaching_sessions_script_id"),
        "coaching_sessions",
        ["script_id"],
        unique=False,
    )

    op.create_table(
        "session_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("session_id", UUID(as_uuid=True), nullable=False),
        sa.Column("timestamp", sa.Float(), nullable=False),
        sa.Column("event_type", sa.String(length=24), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("raw_transcript_snippet", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["coaching_sessions.id"],
            name=op.f("fk_session_events_session_id_coaching_sessions"),
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        op.f("ix_session_events_session_id"),
        "session_events",
        ["session_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("session_events")
    op.drop_table("coaching_sessions")
    op.drop_table("scripts")
    op.drop_table("users")
