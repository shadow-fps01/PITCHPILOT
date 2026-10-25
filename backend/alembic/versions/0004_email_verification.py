"""Email verification: users.email_verified (opt-in at the app level).

Revision ID: 0004_email_verified
Revises: 0003_billing
Create Date: 2026-10-24

Adds users.email_verified, defaulting to true so pre-existing accounts stay
usable; new registrations set it explicitly when verification is required.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_email_verified"
down_revision: Union[str, None] = "0003_billing"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "email_verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "email_verified")
