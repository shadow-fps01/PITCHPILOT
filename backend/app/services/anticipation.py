"""Zero-prompting "Next-Topic" anticipation (copilot upgrade F3).

Continuously evaluates the live transcript for speaker intents and question
patterns; when a trigger fires, asynchronously queries the pgvector knowledge
base (RAG) and surfaces the most relevant context card BEFORE the speaker
finishes their thought — no clicks, no manual prompting.

Card sources:
- auto-created from the user's script outlines (`source="script"`),
- a small default deck seeded at registration (`source="seed"`),
- anything the user adds later (`source="manual"`).

Falls back to keyword-overlap scoring when embeddings are unavailable.
"""

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from loguru import logger
from sqlalchemy import select

from app.config.settings import Settings
from app.db.session import async_session_factory
from app.models.knowledge_card import KnowledgeCard
from app.services.embedding import EmbeddingClient

# Spoken questions rarely carry "?" — match question-starter patterns.
_TRIGGER_QUESTION = re.compile(
    r"\b(what|how|why|when|who|where|which|could you|can you tell|do you|"
    r"are we|is there|tell me about|talk about)\b",
    re.IGNORECASE,
)
_TRIGGER_PHRASES = (
    "the ask", "next topic", "moving on", "let me talk about",
    "let's discuss", "any questions", "pricing", "roadmap", "traction",
    "competitor", "competitors", "revenue", "our team", "the team",
    "the market", "growth", "fundraising", "valuation",
)
_KEYWORD_SPLIT = re.compile(r"\w+")


@dataclass(frozen=True)
class SurfacedCard:
    """A knowledge card handed to the WS layer for display/persistence."""

    title: str
    content: str
    score: float
    keywords: list[str] = field(default_factory=list)


class NextTopicAnticipator:
    """Per-session, reconnect-safe semantic evaluator over the transcript."""

    def __init__(
        self,
        settings: Settings,
        user_id,  # uuid.UUID (untyped to avoid import cycle noise)
        embedding_client: EmbeddingClient | None,
        on_card: Callable[[SurfacedCard], Awaitable[None]],
    ) -> None:
        self._settings = settings
        self._user_id = user_id
        self._embedder = embedding_client
        self.on_card = on_card  # rebound per (re)connection by the WS handler

        self._shown_titles: set[str] = set()
        self._last_card_at: float = -1e9
        self._last_trigger_check: float = -1e9
        self._lock = asyncio.Lock()

    # ── Public API (called by AICoach on final transcripts) ───────────
    async def evaluate(self, recent_transcript: str) -> None:
        """Evaluate the rolling transcript window; may surface a card."""
        if not self._settings.anticipation_enabled:
            return
        t = time.monotonic()
        if t - self._last_trigger_check < 2.0:
            return  # cheap pre-throttle before the lock
        self._last_trigger_check = t
        if self._lock.locked():
            return
        async with self._lock:
            if t - self._last_card_at < self._settings.rag_cooldown_seconds:
                return
            window = self._window(recent_transcript)
            if not self._is_trigger(window):
                return

            cards = await self._search(window)
            for card, score in cards:
                if score < self._settings.rag_min_similarity:
                    break
                if card.title in self._shown_titles:
                    continue
                self._shown_titles.add(card.title)
                self._last_card_at = t
                logger.info(f"Anticipator surfaced card: {card.title!r} (score={score:.2f})")
                await self.on_card(
                    SurfacedCard(
                        title=card.title,
                        content=card.content,
                        score=round(score, 3),
                        keywords=list(card.keywords),
                    )
                )
                return

    # ── Internals ─────────────────────────────────────────────────────
    def _window(self, transcript: str) -> str:
        words = transcript.split()
        if len(words) <= self._settings.rag_window_words:
            return transcript
        return " ".join(words[-self._settings.rag_window_words :])

    def _is_trigger(self, text: str) -> bool:
        if "?" in text:
            return True
        if _TRIGGER_QUESTION.search(text):
            return True
        lowered = text.lower()
        return any(phrase in lowered for phrase in _TRIGGER_PHRASES)

    async def _search(self, window: str) -> list[tuple[KnowledgeCard, float]]:
        """Top-K cards with similarity scores; vector search when possible."""
        embedding: list[float] | None = None
        if self._embedder is not None:
            embedding = await self._embedder.embed(window)

        async with async_session_factory() as db:
            if embedding is not None:
                distance = KnowledgeCard.embedding.cosine_distance(embedding)
                stmt = (
                    select(KnowledgeCard, (1.0 - distance).label("sim"))
                    .where(
                        KnowledgeCard.user_id == self._user_id,
                        KnowledgeCard.embedding.isnot(None),
                    )
                    .order_by(distance)
                    .limit(self._settings.rag_top_k)
                )
                rows = (await db.execute(stmt)).all()
                return [(row[0], float(row[1] or 0.0)) for row in rows]

            # Keyword fallback: overlap between transcript words and card keywords.
            words = {w.lower() for w in _KEYWORD_SPLIT.findall(window)}
            stmt = select(KnowledgeCard).where(
                KnowledgeCard.user_id == self._user_id
            )
            cards = list((await db.scalars(stmt)).all())
            scored: list[tuple[KnowledgeCard, float]] = []
            for card in cards:
                keywords = [k.lower() for k in card.keywords]
                if not keywords:
                    continue
                hits = sum(1 for k in keywords if k in words)
                score = hits / len(keywords)
                if score > 0:
                    scored.append((card, min(score, 0.99)))
            scored.sort(key=lambda pair: pair[1], reverse=True)
            return scored[: self._settings.rag_top_k]
