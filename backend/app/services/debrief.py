"""End-of-session written debrief (post-session AI review).

Once a session closes, the review turns the raw metrics into something a
presenter can act on: a two-sentence summary plus three concrete tips. It runs
through the same LLM chain as the copilot (Azure OpenAI -> OpenRouter) and
always falls back to a keyless, rule-based debrief so the feature works with
zero API keys.

Prompts live in settings (`DEBRIEF_PROMPT`) — nothing hardcoded here.
"""

from dataclasses import dataclass, field

from loguru import logger

from app.config.settings import Settings
from app.schemas.stream import SessionSummarySchema
from app.services.copilot_chat import CopilotLLM, _extract_json


@dataclass(frozen=True, slots=True)
class DebriefResult:
    summary: str
    tips: list[str] = field(default_factory=list)
    source: str = "heuristic"  # ai | heuristic


def heuristic_debrief(
    summary: SessionSummarySchema, target_wpm: int
) -> DebriefResult:
    """Keyless, rule-based review — always available."""
    duration = summary.duration_seconds or 0
    tips: list[str] = []

    if summary.total_filler_words >= 5:
        tips.append(
            f"Cut the fillers — {summary.total_filler_words} this session; "
            "use a beat of silence where you'd say “um”."
        )
    elif summary.total_filler_words > 0:
        tips.append("Fillers stayed low — keep pausing instead of filling the gap.")

    if summary.pacing_alerts > 0:
        tips.append(
            f"Pace drifted {summary.pacing_alerts}× — anchor near {target_wpm} WPM "
            "and take a breath at each talking point."
        )

    if summary.script_misses > 0:
        tips.append(
            f"You skipped {summary.script_misses} outline point(s) — lead with them "
            "earlier next time."
        )

    if summary.context_cards > 0:
        tips.append(
            f"{summary.context_cards} ready answer(s) surfaced — trust them and "
            "answer sooner."
        )

    if not tips:
        tips.append("Clean, structured delivery — keep the same flow.")

    filler = ["Record the next session and compare the trend.", "Rehearse the outline aloud once before you present."]
    for extra in filler:
        if len(tips) >= 3:
            break
        tips.append(extra)

    return DebriefResult(
        summary=(
            f"{duration}s of speaking with {summary.total_filler_words} filler words, "
            f"{summary.pacing_alerts} pacing alerts and {summary.script_misses} "
            "missed points."
        ),
        tips=tips[:3],
        source="heuristic",
    )


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
    except Exception:  # noqa: BLE001 — never trust the model's formatting
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
    """Generate the debrief; falls back to the heuristic on any failure."""
    if not settings.debrief_enabled:
        return heuristic_debrief(summary, target_wpm)

    has_key = bool(settings.azure_openai_api_key or settings.openrouter_api_key)
    if not has_key:
        return heuristic_debrief(summary, target_wpm)

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
        logger.info("Debrief fell back to the heuristic")
        return heuristic_debrief(summary, target_wpm)
    return parsed
