"""RAG knowledge cards: pre-fetchable answers for the next-topic anticipator.

Embeddings use Gemini `text-embedding-004` (768 dims) and are stored in a
pgvector column so similarity search runs inside your Supabase Postgres.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from pgvector.sqlalchemy import Vector

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.user import User


class KnowledgeCard(Base):
    __tablename__ = "knowledge_cards"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    script_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("scripts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    keywords: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )
    source: Mapped[str] = mapped_column(String(30), nullable=False, default="manual")
    # 768-dim Gemini embedding; NULL until embedded (keyword fallback applies).
    embedding: Mapped[list[float] | None] = mapped_column(Vector(768), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="knowledge_cards")

    def __repr__(self) -> str:
        return f"<KnowledgeCard id={self.id} title={self.title!r}>"
