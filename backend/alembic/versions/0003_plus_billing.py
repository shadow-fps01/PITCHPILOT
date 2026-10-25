"""PitchPilot Plus billing: user plan columns + billing_events audit table.

Revision ID: 0003_plus_billing
Revises: 0002_knowledge
Create Date: 2026-10-16

Adds:
- users.plan / paystack_* / plus_* billing columns
- scripts.created_at (needed to count free-plan monthly script quota)
- billing_events (payments & plan-change audit log)
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "0003_billing"
down_revision: Union[str, None] = "0002_knowledge"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── users: plan + Paystack subscription state ────────────────────────
    op.add_column(
        "users",
        sa.Column("plan", sa.String(length=10), nullable=False, server_default="free"),
    )
    op.add_column("users", sa.Column("paystack_customer_code", sa.String(length=100), nullable=True))
    op.add_column("users", sa.Column("paystack_subscription_code", sa.String(length=100), nullable=True))
    op.add_column("users", sa.Column("paystack_email_token", sa.String(length=200), nullable=True))
    op.add_column("users", sa.Column("plus_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("plus_renews_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "plus_cancel_at_period_end",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    # ── scripts: created_at for the monthly free-plan script quota ───────
    op.add_column(
        "scripts",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # ── billing_events: payment & plan-change audit log ──────────────────
    op.create_table(
        "billing_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("reference", sa.String(length=100), nullable=True),
        sa.Column("amount", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(length=10), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("raw_payload", JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_billing_events_user_id_users"), ondelete="CASCADE"
        ),
    )
    op.create_index(
        op.f("ix_billing_events_user_id"), "billing_events", ["user_id"], unique=False
    )
    op.create_index(
        "ix_billing_events_type_reference",
        "billing_events",
        ["event_type", "reference"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_billing_events_type_reference", table_name="billing_events")
    op.drop_index(op.f("ix_billing_events_user_id"), table_name="billing_events")
    op.drop_table("billing_events")
    op.drop_column("scripts", "created_at")
    op.drop_column("users", "plus_cancel_at_period_end")
    op.drop_column("users", "plus_renews_at")
    op.drop_column("users", "plus_started_at")
    op.drop_column("users", "paystack_email_token")
    op.drop_column("users", "paystack_subscription_code")
    op.drop_column("users", "paystack_customer_code")
    op.drop_column("users", "plan")
