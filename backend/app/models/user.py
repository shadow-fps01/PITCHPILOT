"""User account model."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

PLAN_FREE = "free"
PLAN_PLUS = "plus"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(
        String(320), unique=True, index=True, nullable=False
    )
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    # Defaults to true so existing rows are treated as verified; registration
    # sets it explicitly (false only when verification is required).
    email_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=func.true()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # ── Session revocation + recording consent ────────────────────────
    # Every access token carries this version; password reset increments it
    # so previously issued tokens stop working immediately.
    token_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    consented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consent_version: Mapped[str | None] = mapped_column(String(20))

    # ── Billing (PitchPilot Plus) ───────────────────────────────────
    plan: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default=PLAN_FREE
    )
    paystack_customer_code: Mapped[str | None] = mapped_column(String(100))
    paystack_subscription_code: Mapped[str | None] = mapped_column(String(100))
    paystack_email_token: Mapped[str | None] = mapped_column(String(200))
    plus_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    plus_renews_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    plus_cancel_at_period_end: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=func.false()
    )

    @property
    def is_plus(self) -> bool:
        """Plus membership with lazy expiry for cancelled subscriptions."""
        if self.plan != PLAN_PLUS:
            return False
        if self.plus_cancel_at_period_end and self.plus_renews_at is not None:
            renews = self.plus_renews_at
            now = (
                datetime.now(tz=renews.tzinfo)
                if renews.tzinfo is not None
                else datetime.now(tz=timezone.utc).replace(tzinfo=None)
            )
            return now < renews
        return True

    @property
    def consent_current(self) -> bool:
        """True when the user accepted the current recording-consent text."""
        if self.consented_at is None:
            return False
        from app.config.settings import get_settings

        return (self.consent_version or "") == get_settings().consent_version

    scripts: Mapped[list["Script"]] = relationship(  # noqa: F821
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    sessions: Mapped[list["CoachingSession"]] = relationship(  # noqa: F821
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    knowledge_cards: Mapped[list["KnowledgeCard"]] = relationship(  # noqa: F821
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    billing_events: Mapped[list["BillingEvent"]] = relationship(  # noqa: F821
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r}>"
