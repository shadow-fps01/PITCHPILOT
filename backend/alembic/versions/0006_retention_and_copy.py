"""Retention index + correction of the seeded privacy/competition cards.

Revision ID: 0006_retention_copy
Revises: 0005_account_lifecycle
Create Date: 2026-10-25

The original seed deck claimed transcripts were deleted after 30 days and
that the overlay was never visible in a screen share. The first claim is now
backed by the retention job; the second was too broad (whole-screen shares
include the overlay). This migration updates cards already seeded for
existing accounts, and adds the index used by retention cleanup.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006_retention_copy"
down_revision: Union[str, None] = "0005_account_lifecycle"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_SECURITY_COPY = (
    "Audio is processed live and is not stored. Transcript excerpts and "
    "coaching notes are removed after 30 days. Screen frames are analyzed and "
    "discarded. Share only the call tab or window; a whole-screen share "
    "includes the overlay."
)
_COMPETITION_COPY = (
    "Unlike generic teleprompters or post-hoc analytics tools, we coach live "
    "with short, private cues. The overlay is designed to stay out of a shared "
    "call tab or window; a whole-screen share will include it."
)


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_coaching_sessions_ended_at "
        "ON coaching_sessions (ended_at)"
    )
    # `keywords` is a PostgreSQL text[] column.
    op.execute(
        sa.text(
            "UPDATE knowledge_cards SET content = :content, "
            "keywords = ARRAY['security','privacy','encryption','retention','screen'] "
            "WHERE source = 'seed' AND title = 'Security & privacy'"
        ).bindparams(content=_SECURITY_COPY)
    )
    op.execute(
        sa.text(
            "UPDATE knowledge_cards SET content = :content, "
            "keywords = ARRAY['competition','competitors','differentiation','unique'] "
            "WHERE source = 'seed' AND title = 'Competition'"
        ).bindparams(content=_COMPETITION_COPY)
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_coaching_sessions_ended_at")
    # Copy corrections are intentionally not reverted: the old text made
    # claims the product does not support.
