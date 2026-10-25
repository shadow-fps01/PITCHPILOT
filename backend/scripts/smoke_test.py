"""Headless smoke test for the live coaching pipeline (no DB / no keys).

Simulates a fast, filler-heavy speaker against the AICoach and prints the
metrics + cues the overlay would receive. Run from `backend/`:

    .venv\\Scripts\\python scripts\\smoke_test.py
"""

import asyncio
import time
from collections.abc import Awaitable

from app.config.settings import get_settings
from app.models.session_event import EventType
from app.services.ai_coach import AICoach
from app.services.llm_client import HeuristicAuditor

FAST_SENTENCES = [
    "So um investors love traction and we are growing super fast right now",
    "Uh our like platform handles a huge volume of live calls every single day",
    "Um the team basically shipped the new analytics module in just two weeks",
    "So uh like that brings us to the roadmap for next quarter maybe",
]

SCRIPT_OUTLINE = "1. Problem\n2. Solution\n3. Pricing\n4. The ask"
KEYWORDS = ["pricing", "discount", "the ask"]


async def main() -> None:
    settings = get_settings()
    collected_cues: list[str] = []

    async def on_cue(cue) -> None:  # type: ignore[no-untyped-def]
        collected_cues.append(f"[{cue.cue_type.value}] {cue.message}")

    coach = AICoach(
        settings=settings,
        target_wpm=140,
        outline=SCRIPT_OUTLINE,
        keywords=KEYWORDS,
        llm=HeuristicAuditor(),
        on_cue=on_cue,
    )

    t0 = time.monotonic()
    # Simulate ~200 WPM: a word every 0.3 s.
    word_clock = 0.0
    print("Feeding a fast, filler-heavy simulated speaker (~200 WPM)…\n")

    for sentence in FAST_SENTENCES * 2:
        words = sentence.split()
        for i in range(1, len(words) + 1):
            interim = " ".join(words[:i])
            is_final = i == len(words)
            t = t0 + word_clock
            messages = await coach.feed_transcript(interim, is_final, t)
            if is_final:
                metrics = [m for m in messages if m.type == "metrics"]
                if metrics:
                    m = metrics[0]
                    print(
                        f"  t={word_clock:5.1f}s  wpm={m.wpm:5.1f}  "
                        f"fillers={m.filler_word_count}  '{interim[:40]}…'"
                    )
            word_clock += 0.3  # ~200 WPM
        word_clock += 0.4

    # Let the script auditor settle.
    await asyncio.sleep(0.2)
    if coach._audit_task is not None:
        await coach._audit_task

    print(f"\nTotal words spoken: {coach.total_words}")
    print(f"Cue counters: {coach.counters}")
    print("\nCues emitted:")
    for cue in collected_cues:
        print(f"  {cue}")
    for cue_type in (EventType.FILLER_WORD, EventType.PACING_ALERT, EventType.SCRIPT_MISS):
        assert any(cue_type.value in c for c in collected_cues), (
            f"Expected a {cue_type.value} cue"
        )
    assert coach.total_words > 40, "Expected the transcript to be counted"
    print("\nSMOKE TEST PASSED [OK]")


if __name__ == "__main__":
    asyncio.run(main())
