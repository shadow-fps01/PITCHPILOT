"""Discrete coaching events emitted during a session (filler words, pacing, script misses)."""

import enum
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Enum, Float, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.coaching_session import CoachingSession


class EventType(str, enum.Enum):
    FILLER_WORD = "FILLER_WORD"
    PACING_ALERT = "PACING_ALERT"
    SCRIPT_MISS = "SCRIPT_MISS"
    CONTEXT_CARD = "CONTEXT_CARD"
    COPILOT_ASSIST = "COPILOT_ASSIST"


class SessionEvent(Base):
    __tablename__ = "session_events"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    session_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("coaching_sessions.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    # Seconds elapsed since the session started.
    timestamp: Mapped[float] = mapped_column(Float, nullable=False)
    event_type: Mapped[EventType] = mapped_column(
        Enum(EventType, native_enum=False), nullable=False
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    raw_transcript_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)

    session: Mapped["CoachingSession"] = relationship(back_populates="events")

    def __repr__(self) -> str:
        return f"<SessionEvent id={self.id} type={self.event_type!r} t={self.timestamp}>"
