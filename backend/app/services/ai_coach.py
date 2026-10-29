"""The AICoach: converts a live transcript stream into coaching cues.

The coach wires three engines together:

1. the Pacing Engine (WPM over a sliding window, via `TranscriptAnalytics`),
2. the Filler Word Tracker (burst detection + cumulative counts),
3. the Contextual Script Auditor (periodic LLM checks against the outline).

It returns/emits strictly-typed `ServerMessage` objects that the WebSocket
handler forwards to the browser overlay, and can survive client reconnects
(its state lives in the `LiveSessionRegistry`).
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from loguru import logger

from app.config.settings import Settings
from app.schemas.stream import (
    CueMessage,
    MetricsMessage,
    TranscriptMessage,
)
from app.services.audio_processor import TranscriptAnalytics
from app.services.llm_client import AIUnavailable, LLMClient, ScriptAuditRequest
from app.models.session_event import EventType

PACING_CUE_COOLDOWN_SECONDS = 15.0
FILLER_CUE_COOLDOWN_SECONDS = 18.0
MIN_WINDOW_WORDS_FOR_PACING = 12
AUDIT_OFFLINE_BACKOFF_SECONDS = 60.0


@dataclass(frozen=True, slots=True)
class CoachProfile:
    """Cue thresholds + frequency for one sensitivity preset.

    The client picks a preset per session (`calm` / `balanced` / `coach`);
    `balanced` matches the long-standing server defaults.
    """

    label: str
    filler_burst_threshold: int
    min_window_words: int
    fast_multiplier: float
    warn_delta: int
    slow_delta: int
    pacing_cooldown: float
    filler_cooldown: float


COACH_PROFILES: dict[str, CoachProfile] = {
    # Fewer, higher-confidence cues  -  for a calm, low-interruption coach.
    "calm": CoachProfile("calm", 5, 16, 1.55, 45, 55, 26.0, 34.0),
    # The shipped default.
    "balanced": CoachProfile("balanced", 3, 12, 1.35, 25, 30, 15.0, 18.0),
    # More cues, tighter thresholds  -  for practice / drilling.
    "coach": CoachProfile("coach", 2, 9, 1.20, 15, 22, 10.0, 12.0),
}
DEFAULT_COACH_PROFILE = "balanced"


@dataclass(slots=True)
class CoachCounters:
    filler_cues: int = 0
    pacing_cues: int = 0
    script_cues: int = 0


class AICoach:
    """Stateful per-session coach fed by the STT pipeline."""

    def __init__(
        self,
        *,
        settings: Settings,
        target_wpm: int,
        outline: str,
        keywords: list[str],
        llm: LLMClient | None,
        on_cue: Callable[[CueMessage], Awaitable[None]] | None = None,
        anticipation: "NextTopicAnticipator | None" = None,  # F3 RAG
        screen_context_provider: Callable[[], str | None] | None = None,  # F5
    ) -> None:
        self._settings = settings
        self._target_wpm = target_wpm
        self._outline = outline
        self._keywords = list(keywords)
        self._llm = llm
        self.on_cue = on_cue  # swapped per (re)connection by the WS handler
        self.anticipation = anticipation
        self.screen_context_provider = screen_context_provider

        self.analytics = TranscriptAnalytics(
            window_seconds=settings.wpm_window_seconds,
            filler_words=settings.filler_words,
            filler_burst_window_seconds=settings.filler_burst_window_seconds,
        )

        self.counters = CoachCounters()
        self._covered: set[str] = set()
        self.profile: CoachProfile = COACH_PROFILES[DEFAULT_COACH_PROFILE]

        self._started_monotonic: float | None = None
        self._last_pacing_cue_t: float = -1e9
        self._last_filler_cue_t: float = -1e9
        self._last_audit_t: float = -1e9
        self._words_since_audit = 0
        self._audit_lock = asyncio.Lock()
        self._audit_task: asyncio.Task[None] | None = None
        self._anticipation_task: asyncio.Task[None] | None = None
        self._audit_offline = False

    # ── Lifecycle ─────────────────────────────────────────────────────
    def apply_profile(self, name: str) -> None:
        """Switch the cue sensitivity preset (client-selected, per session)."""
        self.profile = COACH_PROFILES.get(name, COACH_PROFILES[DEFAULT_COACH_PROFILE])

    def start_clock(self) -> None:
        """Begin the session clock (idempotent; resumes keep the original)."""
        if self._started_monotonic is None:
            self._started_monotonic = time.monotonic()

    @property
    def started_at(self) -> float | None:
        return self._started_monotonic

    def elapsed(self, t: float | None = None) -> float:
        if self._started_monotonic is None:
            return 0.0
        return (t if t is not None else time.monotonic()) - self._started_monotonic

    # ── Main pipeline ────────────────────────────────────────────────
    async def feed_transcript(self, text: str, is_final: bool, t: float) -> list[TranscriptMessage | MetricsMessage]:
        """Process one STT chunk; returns transcript/metrics messages.

        Cues (pacing, filler) are delivered via the `on_cue` callback so that
        every cue  -  regardless of origin  -  is persisted and emitted by a
        single code path in the WebSocket handler.
        """
        self.start_clock()
        if not is_final:
            return [TranscriptMessage(text=text, is_final=False)]

        word_count, _matches = self.analytics.add_final(text, t)
        self._words_since_audit += word_count

        messages: list[TranscriptMessage | MetricsMessage] = [
            TranscriptMessage(text=text, is_final=True),
            self._metrics_snapshot(t),
        ]

        filler_cue = self._maybe_filler_cue(t)
        if filler_cue is not None:
            await self._emit_cue(filler_cue)
        pacing_cue = self._maybe_pacing_cue(t)
        if pacing_cue is not None:
            await self._emit_cue(pacing_cue)

        self._maybe_schedule_audit(t)
        self._maybe_schedule_anticipation()
        return messages

    async def periodic_metrics(self, t: float) -> list[MetricsMessage]:
        """Called by the metrics ticker; emits a snapshot + optional pacing cue."""
        if self._started_monotonic is None:
            return []
        messages: list[MetricsMessage] = [self._metrics_snapshot(t)]
        pacing_cue = self._maybe_pacing_cue(t)
        if pacing_cue is not None:
            await self._emit_cue(pacing_cue)
        return messages

    async def _emit_cue(self, cue: CueMessage) -> None:
        """Deliver a cue through the connection's callback (persist + emit)."""
        if self.on_cue is None:
            return
        try:
            await self.on_cue(cue)
        except Exception as exc:  # noqa: BLE001  -  cues must never break the stream
            logger.warning(f"on_cue callback failed: {exc}")

    def _maybe_schedule_anticipation(self) -> None:
        """Zero-prompt next-topic evaluation over the rolling transcript (F3)."""
        if self.anticipation is None:
            return
        if (
            self._anticipation_task is not None
            and not self._anticipation_task.done()
        ):
            return  # one evaluation in flight; the next final re-triggers
        context = self.analytics.context_text(self._settings.script_audit_context_words)
        self._anticipation_task = asyncio.create_task(
            self.anticipation.evaluate(context)
        )

    def feed_external_transcript(self, text: str) -> None:
        """Use the other party's words for next-topic anticipation only.

        This deliberately does not touch the speaking analytics. It is called
        for shared call/system audio so the anticipator can react to a question
        from the other side without counting their words as the presenter's.
        """
        text = text.strip()
        if not text or self.anticipation is None:
            return
        if (
            self._anticipation_task is not None
            and not self._anticipation_task.done()
        ):
            return
        context = (
            f"{self.analytics.context_text(self._settings.script_audit_context_words)} "
            f"{text}"
        ).strip()
        self._anticipation_task = asyncio.create_task(
            self.anticipation.evaluate(context)
        )

    # ── Engines ──────────────────────────────────────────────────────
    def _metrics_snapshot(self, t: float) -> MetricsMessage:
        assert self._started_monotonic is not None
        return MetricsMessage(
            wpm=self.analytics.wpm(t, started_at=self._started_monotonic),
            filler_word_count=self.analytics.filler_count,
            elapsed_seconds=round(self.elapsed(t), 1),
            target_wpm=self._target_wpm,
        )

    def _maybe_filler_cue(self, t: float) -> CueMessage | None:
        burst = self.analytics.filler_burst(t)
        if burst < self.profile.filler_burst_threshold:
            return None
        if t - self._last_filler_cue_t < self.profile.filler_cooldown:
            return None
        self._last_filler_cue_t = t
        self.counters.filler_cues += 1
        word = self.analytics.recent_filler_word() or "um"
        return CueMessage(
            cue_type=EventType.FILLER_WORD,
            message=(
                f'You have said "{word}"-style fillers {burst} times in the last '
                f"{int(self._settings.filler_burst_window_seconds)}s  -  try a short pause instead."
            ),
            severity="warn",
            raw_transcript_snippet=self.analytics.context_text(12),
        )

    def _maybe_pacing_cue(self, t: float) -> CueMessage | None:
        assert self._started_monotonic is not None
        if self.analytics.window_word_count(t) < self.profile.min_window_words:
            return None
        if t - self._last_pacing_cue_t < self.profile.pacing_cooldown:
            return None
        wpm = self.analytics.wpm(t, started_at=self._started_monotonic)
        if wpm <= 0:
            return None

        cue: CueMessage | None = None
        if wpm > self._target_wpm * self.profile.fast_multiplier:
            cue = CueMessage(
                cue_type=EventType.PACING_ALERT,
                message=f"Slow down  -  you are at {wpm:.0f} WPM (target {self._target_wpm}).",
                severity="alert",
            )
        elif wpm > self._target_wpm + self.profile.warn_delta:
            cue = CueMessage(
                cue_type=EventType.PACING_ALERT,
                message=f"Ease off the pace a little  -  {wpm:.0f} WPM.",
                severity="warn",
            )
        elif wpm < self._target_wpm - self.profile.slow_delta:
            cue = CueMessage(
                cue_type=EventType.PACING_ALERT,
                message=f"Pick up the pace  -  {wpm:.0f} WPM (target {self._target_wpm}).",
                severity="info",
            )
        if cue is not None:
            self._last_pacing_cue_t = t
            self.counters.pacing_cues += 1
        return cue

    def _maybe_schedule_audit(self, t: float) -> None:
        if not (self._outline.strip() or self._keywords):
            return
        if self._words_since_audit < self._settings.script_audit_word_interval:
            return
        if t - self._last_audit_t < self._settings.script_audit_min_gap_seconds:
            return
        if self._audit_lock.locked():
            return
        self._words_since_audit = 0
        self._audit_task = asyncio.create_task(self._run_script_audit(t))

    async def _run_script_audit(self, t: float) -> None:
        async with self._audit_lock:
            self._last_audit_t = t
            if self._llm is None:
                return
            context = self.analytics.context_text(self._settings.script_audit_context_words)
            if len(context.split()) < 10:
                return
            request = ScriptAuditRequest(
                outline=self._outline,
                keywords=self._keywords,
                recent_transcript=context,
                already_covered=set(self._covered),
                screen_context=(
                    self.screen_context_provider()
                    if self.screen_context_provider is not None
                    else None
                ),
            )
            try:
                result = await self._llm.audit_transcript(request)
            except AIUnavailable as exc:
                # No silent fallback: tell the connection the script audit is
                # offline once, then back off instead of hammering the API.
                logger.warning(f"Script audit unavailable: {exc}")
                if not self._audit_offline:
                    self._audit_offline = True
                    await self._emit_cue(
                        CueMessage(
                            cue_type=EventType.SCRIPT_MISS,
                            message=(
                                "Script coaching is offline right now  -  your "
                                "pacing and filler cues are still live."
                            ),
                            severity="warn",
                        )
                    )
                self._last_audit_t = t + AUDIT_OFFLINE_BACKOFF_SECONDS
                return
            except Exception as exc:  # noqa: BLE001  -  audits must never kill the stream
                logger.warning(f"Script audit raised: {exc}")
                return
            self._audit_offline = False
            if not result.missed or not result.cue:
                return
            self._covered.update(result.covered)
            self.counters.script_cues += 1
            cue = CueMessage(
                cue_type=EventType.SCRIPT_MISS,
                message=result.cue,
                severity="info",
                raw_transcript_snippet=context[-160:],
            )
            await self._emit_cue(cue)

    # ── Wrap-up ──────────────────────────────────────────────────────
    async def aclose(self) -> None:
        for task in (self._audit_task, self._anticipation_task):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:  # noqa: BLE001
                    pass

    @property
    def total_words(self) -> int:
        return self.analytics.total_words

    @property
    def target_wpm(self) -> int:
        return self._target_wpm
