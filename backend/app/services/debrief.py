"""End-of-session written debrief (post-session AI review).

Once a session closes, the review turns the raw metrics into something a
presenter can act on: a two-sentence summary plus three concrete tips. It runs
through the same LLM chain as the copilot (gpt-4o on Azure OpenAI, OpenRouter
only as failover). When no AI backend can answer, ``generate_debrief`` returns
``None`` so the caller can show measured stats instead of invented advice.

Prompts live in settings (`DEBRIEF_PROMPT`)  -  nothing hardcoded here.
"""

from dataclasses import dataclass, field

from loguru import logger

from app.config.settings import Settings
from app.schemas.stream import SessionSummarySchema
from app.services.copilot_chat import CopilotLLM, _extract_json, build_copilot_llm


@dataclass(frozen=True, slots=True)
class DebriefResult:
    summary: str
    tips: list[str] = field(default_factory=list)
    source: str = "heuristic"  # ai | heuristic


def _build_prompt(
    summary: SessionSummarySchema,
    outline: str,
    keywords: list[str],
    transcript: str,
    target_wpm: int,
) -> str:
    return (
        f"SESSION METRICS: duration_seconds={summary.duration_seconds or 0}, "
        f"target_wpm={target_wpm}, filler_words={summary.total_filler_words}, "
        f"pacing_alerts={summary.pacing_alerts}, missed_points={summary.script_misses}, "
        f"answer_cards={summary.context_cards}\n\n"
        f"PRESENTER'S OUTLINE: {outline or '(none)'}\n"
        f"KEYWORDS: {', '.join(keywords) or '(none)'}\n\n"
        f"RECENT TRANSCRIPT (latest words last): \"{transcript or '(no transcript)'}\"\n\n"
        "Reply with the JSON object now."
    )


def _parse(raw: str) -> DebriefResult | None:
    try:
        payload = _extract_json(raw)
    except Exception:  # noqa: BLE001  -  never trust the model's formatting
        return None
    summary_text = payload.get("summary")
    if not isinstance(summary_text, str) or not summary_text.strip():
        return None
    tips = [
        str(tip).strip()
        for tip in (payload.get("tips") or [])
        if str(tip).strip()
    ][:3]
    return DebriefResult(summary=summary_text.strip(), tips=tips, source="ai")


async def generate_debrief(
    settings: Settings,
    *,
    summary: SessionSummarySchema,
    outline: str,
    keywords: list[str],
    transcript: str,
    target_wpm: int,
) -> DebriefResult:
    """Generate the debrief; None when no AI backend can answer."""
    if not settings.debrief_enabled or build_copilot_llm(settings) is None:
        return None

    llm = CopilotLLM(settings)
    try:
        raw = await llm.complete(
            settings.debrief_prompt,
            _build_prompt(summary, outline, keywords, transcript, target_wpm),
            max_tokens=340,
            temperature=0.4,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Debrief LLM call failed: {exc}")
        raw = None
    finally:
        await llm.aclose()

    parsed = _parse(raw) if raw else None
    if parsed is None:
        logger.info("Debrief unavailable - no AI review generated")
    return parsed
