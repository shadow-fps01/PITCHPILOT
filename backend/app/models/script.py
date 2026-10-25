"""User-authored pitch outline / talking-point script."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Script(Base):
    __tablename__ = "scripts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content_outline: Mapped[str] = mapped_column(Text, nullable=False, default="")
    target_wpm: Mapped[int] = mapped_column(Integer, nullable=False, default=140)
    keywords: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(  # noqa: F821
        back_populates="scripts"
    )
    sessions: Mapped[list["CoachingSession"]] = relationship(  # noqa: F821
        back_populates="script",
        foreign_keys="CoachingSession.script_id",
    )

    def __repr__(self) -> str:
        return f"<Script id={self.id} title={self.title!r}>"
