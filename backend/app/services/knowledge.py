"""Knowledge-card provisioning for the RAG base (copilot upgrade F3).

Cards are auto-created from script outlines, a default Q&A deck is seeded at
registration, and everything is embedded via Gemini when a key is available.
Cards without embeddings still surface through the keyword-search fallback.
"""

import asyncio
import uuid

from loguru import logger
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.config.settings import get_settings
from app.db.session import async_session_factory
from app.models.knowledge_card import KnowledgeCard
from app.models.user import User
from app.services.embedding import get_shared_embedding_client

# A compact default deck so every new account has working anticipation
# from day one. These are prompts, not claims: the copilot must not present a
# seeded pricing/traction/team number as the user's own fact. Users fill in
# their real numbers in their scripts and knowledge cards.
DEFAULT_SEED_CARDS: list[tuple[str, str, list[str]]] = [
    ("Pricing model", "What is your price per seat, your annual discount, and your trial length? State only the exact numbers you are ready to defend on the call.", ["pricing", "discount", "cost", "price", "free trial"]),
    ("The ask", "What are you asking for, how will you use it, and what will it unlock? Keep the amount and the milestones ready.", ["the ask", "fundraising", "raise", "runway", "valuation"]),
    ("Traction", "Which proof points can you cite  -  pilots, conversion, usage, retention, NPS? Use only numbers you can defend.", ["traction", "pilots", "conversion", "nps", "growth"]),
    ("Roadmap", "What are the next two quarters' milestones, and why do they matter to this audience?", ["roadmap", "next quarter", "languages", "integrations", "crm"]),
    ("Team", "Who is on the team, and which experience makes them credible for this problem?", ["team", "founders", "engineers", "advisor"]),
    ("Competition", "Unlike generic teleprompters or post-hoc analytics tools, we coach live with short, private cues. The overlay is designed to stay out of a shared call tab or window; a whole-screen share will include it.", ["competition", "competitors", "differentiation", "unique"]),
    ("Market size", "What is the addressable market, and which slice are you winning first? Be ready to name the source of the number.", ["market", "market size", "tam", "growth"]),
    ("Security & privacy", "Audio is processed live and is not stored. Transcript excerpts and coaching notes are removed after 30 days. Screen frames are analyzed and discarded. Share only the call tab or window; a whole-screen share includes the overlay.", ["security", "privacy", "encryption", "retention", "screen"]),
    ("Onboarding & setup", "How quickly can a new customer get value, and what are the exact setup steps?", ["onboarding", "setup", "integration", "zoom"]),
    ("Performance & latency", "What performance numbers can you substantiate, and how do you measure them?", ["latency", "performance", "realtime", "websocket", "speed"]),
]


async def _embed(title: str, content: str) -> list[float] | None:
    embedder = get_shared_embedding_client()
    if embedder is None:
        return None
    return await embedder.embed(f"{title}. {content}")


async def upsert_script_card(
    user_id: uuid.UUID, script_id: uuid.UUID, title: str, outline: str, keywords: list[str]
) -> None:
    """One knowledge card per script (source='script'); replaces any previous."""
    if not outline.strip():
        return
    try:
        embedding = await _embed(f"Outline: {title}", outline)
        async with async_session_factory() as db:
            await db.execute(
                delete(KnowledgeCard).where(
                    KnowledgeCard.user_id == user_id,
                    KnowledgeCard.script_id == script_id,
                    KnowledgeCard.source == "script",
                )
            )
            db.add(
                KnowledgeCard(
                    user_id=user_id,
                    script_id=script_id,
                    title=f"Outline: {title}",
                    content=outline,
                    keywords=keywords,
                    source="script",
                    embedding=embedding,
                )
            )
            await db.commit()
        logger.info(f"Knowledge card stored for script {script_id} (embedded={embedding is not None})")
    except Exception as exc:  # noqa: BLE001  -  provisioning must never break the API
        logger.warning(f"Failed to store knowledge card for script {script_id}: {exc}")


def schedule_upsert_script_card(
    user_id: uuid.UUID, script_id: uuid.UUID, title: str, outline: str, keywords: list[str]
) -> None:
    """Fire-and-forget background provisioning from request handlers."""
    asyncio.create_task(
        upsert_script_card(user_id, script_id, title, outline, keywords)
    )


async def seed_default_cards(user_id: uuid.UUID) -> None:
    """Idempotent seed of the default deck for a new user."""
    try:
        async with async_session_factory() as db:
            existing = await db.scalar(
                select(KnowledgeCard.id)
                .where(KnowledgeCard.user_id == user_id, KnowledgeCard.source == "seed")
                .limit(1)
            )
        if existing is not None:
            return
        for title, content, keywords in DEFAULT_SEED_CARDS:
            embedding = await _embed(title, content)
            async with async_session_factory() as db:
                # The account can be deleted while embeddings are in flight.
                if await db.scalar(select(User.id).where(User.id == user_id)) is None:
                    logger.info(f"Knowledge seeding stopped: user {user_id} was deleted")
                    return
                db.add(
                    KnowledgeCard(
                        user_id=user_id,
                        script_id=None,
                        title=title,
                        content=content,
                        keywords=keywords,
                        source="seed",
                        embedding=embedding,
                    )
                )
                try:
                    await db.commit()
                except IntegrityError:
                    logger.info(f"Knowledge seeding stopped: user {user_id} was deleted")
                    return
            await asyncio.sleep(0.1)  # be gentle with the embedding API
        logger.info(f"Seeded {len(DEFAULT_SEED_CARDS)} default knowledge cards for user {user_id}")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Knowledge seeding failed for user {user_id}: {exc}")


def schedule_seed_default_cards(user_id: uuid.UUID) -> None:
    asyncio.create_task(seed_default_cards(user_id))
