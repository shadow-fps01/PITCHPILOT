"""Shared rate limits, single-use WS tickets, and honest default seed cards.

Revision ID: 0007_shared_limits
Revises: 0006_retention_copy
Create Date: 2026-10-27

Adds:
- rate_limit_buckets: Postgres-backed fixed-window counters for per-IP limits,
  per-email login lockouts and per-user daily AI budgets.
- ws_tickets: hashed, single-use, session-bound WebSocket tickets.

Also replaces the remaining default RAG cards that asserted fictional company
facts (pricing, traction, team, market size, etc.) with prompts that ask the
user to supply their own numbers. Existing embeddings are invalidated so the
old claims cannot be surfaced semantically.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0007_shared_limits"
down_revision: Union[str, None] = "0006_retention_copy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_GENERIC_SEED_CARDS: dict[str, str] = {
    "Pricing model": (
        "What is your price per seat, your annual discount, and your trial "
        "length? State only the exact numbers you are ready to defend on the "
        "call."
    ),
    "The ask": (
        "What are you asking for, how will you use it, and what will it "
        "unlock? Keep the amount and the milestones ready."
    ),
    "Traction": (
        "Which proof points can you cite  -  pilots, conversion, usage, "
        "retention, NPS? Use only numbers you can defend."
    ),
    "Roadmap": (
        "What are the next two quarters' milestones, and why do they matter "
        "to this audience?"
    ),
    "Team": (
        "Who is on the team, and which experience makes them credible for "
        "this problem?"
    ),
    "Market size": (
        "What is the addressable market, and which slice are you winning "
        "first? Be ready to name the source of the number."
    ),
    "Onboarding & setup": (
        "How quickly can a new customer get value, and what are the exact "
        "setup steps?"
    ),
    "Performance & latency": (
        "What performance numbers can you substantiate, and how do you "
        "measure them?"
    ),
}


def upgrade() -> None:
    op.create_table(
        "rate_limit_buckets",
        sa.Column("key", sa.String(length=255), primary_key=True),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_rate_limit_buckets_window_start",
        "rate_limit_buckets",
        ["window_start"],
    )

    op.create_table(
        "ws_tickets",
        sa.Column("ticket_hash", sa.String(length=64), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["session_id"], ["coaching_sessions.id"], ondelete="CASCADE"
        ),
    )
    op.create_index("ix_ws_tickets_user_id", "ws_tickets", ["user_id"])
    op.create_index("ix_ws_tickets_session_id", "ws_tickets", ["session_id"])
    op.create_index("ix_ws_tickets_expires_at", "ws_tickets", ["expires_at"])

    for title, content in _GENERIC_SEED_CARDS.items():
        op.execute(
            sa.text(
                "UPDATE knowledge_cards SET content = :content, embedding = NULL "
                "WHERE source = 'seed' AND title = :title"
            ).bindparams(content=content, title=title)
        )


def downgrade() -> None:
    op.drop_index("ix_ws_tickets_expires_at", table_name="ws_tickets")
    op.drop_index("ix_ws_tickets_session_id", table_name="ws_tickets")
    op.drop_index("ix_ws_tickets_user_id", table_name="ws_tickets")
    op.drop_table("ws_tickets")
    op.drop_index("ix_rate_limit_buckets_window_start", table_name="rate_limit_buckets")
    op.drop_table("rate_limit_buckets")
    # Seed-card copy corrections are intentionally not reverted.
