"""Live end-to-end check of the next-topic anticipation loop (F3).

Seeds the default knowledge deck for the e2e user, then evaluates a trigger
transcript through the real anticipator + pgvector vector search.

Run from `backend/`:
    $env:PYTHONPATH='.'; .venv\\Scripts\\python scripts\\test_anticipation.py
"""

import asyncio
import time
import uuid

from sqlalchemy import select

from app.config.settings import get_settings
from app.db.session import async_session_factory
from app.models.knowledge_card import KnowledgeCard
from app.models.user import User
from app.services.anticipation import NextTopicAnticipator, SurfacedCard
from app.services.embedding import get_shared_embedding_client
from app.services.knowledge import seed_default_cards

EMAIL = "pitchpilot.e2e@gmail.com"
TRIGGER_TRANSCRIPT = (
    "so um we built the live coaching overlay and the analytics engine today "
    "and then let me talk about the pricing and what discount we can offer "
    "and after that i will take any questions about the ask"
)


async def main() -> int:
    settings = get_settings()

    async with async_session_factory() as db:
        user = await db.scalar(select(User).where(User.email == EMAIL))
        if user is None:
            print(f"User {EMAIL} not found — run scripts\\e2e_test.py once first.")
            return 2
        user_id: uuid.UUID = user.id

    await seed_default_cards(user_id)

    async with async_session_factory() as db:
        cards = list(
            await db.scalars(
                select(KnowledgeCard).where(KnowledgeCard.user_id == user_id)
            )
        )
    embedded = sum(1 for card in cards if card.embedding is not None)
    print(f"knowledge cards: {len(cards)} ({embedded} with embeddings)")

    surfaced: list[SurfacedCard] = []

    async def on_card(card: SurfacedCard) -> None:
        surfaced.append(card)

    anticipator = NextTopicAnticipator(
        settings=settings,
        user_id=user_id,
        embedding_client=get_shared_embedding_client(),
        on_card=on_card,
    )

    print("evaluating trigger transcript through the anticipator…")
    await anticipator.evaluate(TRIGGER_TRANSCRIPT)
    # Cooldown keeps a second call silent — proves the cooldown works.
    await anticipator.evaluate(TRIGGER_TRANSCRIPT)

    if not surfaced:
        print("No card surfaced — check RAG_MIN_SIMILARITY / embeddings.")
        return 1

    card = surfaced[0]
    print(f"surfaced: {card.title!r} score={card.score} keywords={card.keywords}")
    print(f"content: {card.content[:90]}…")
    print(f"cooldown respected: {'yes' if len(surfaced) == 1 else 'NO'}")

    assert card.score >= settings.rag_min_similarity, "score below threshold"
    assert len(surfaced) == 1, "cooldown failed — second card should not surface"
    print("\nANTICIPATION TEST PASSED [OK]")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
