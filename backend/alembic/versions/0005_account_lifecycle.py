"""Account lifecycle: token revocation, recording consent, signup controls.

Revision ID: 0005_account_lifecycle
Revises: 0004_email_verified
Create Date: 2026-10-24

Adds:
- users.token_version (invalidate JWTs after a password reset)
- users.consented_at / users.consent_version (recording/AI-processing consent)

Signup allowlisting itself is configuration-only; no schema change is needed.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_account_lifecycle"
down_revision: Union[str, None] = "0004_email_verified"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "token_version",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "users",
        sa.Column("consented_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("consent_version", sa.String(length=20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "consent_version")
    op.drop_column("users", "consented_at")
    op.drop_column("users", "token_version")
