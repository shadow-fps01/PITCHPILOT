"""Real-time transcript analytics: pacing engine + filler-word tracker.

The `TranscriptAnalytics` class consumes *final* transcript chunks and
maintains everything the live coach needs:

- a sliding-window words-per-minute (WPM) engine,
- a cumulative filler-word counter with burst detection,
- a rolling buffer of recent words used as context for the LLM script auditor.
"""

import re
from collections import deque
from dataclasses import dataclass, field


def count_words(text: str) -> int:
    """Number of whitespace-separated tokens in a transcript chunk."""
    return len(text.split())


@dataclass(slots=True)
class TranscriptAnalytics:
    """Stateful analytics over a single coaching session's transcript.

    All timestamps are seconds on a monotonic clock supplied by the caller,
    which keeps the class deterministic and easy to unit-test.
    """

    window_seconds: float = 5.0
    filler_words: list[str] = field(default_factory=list)
    filler_burst_window_seconds: float = 15.0

    _word_events: list[tuple[float, int]] = field(default_factory=list)
    _total_words: int = 0
    _filler_hits: list[tuple[float, str]] = field(default_factory=list)
    _recent_words: deque[str] = field(default_factory=lambda: deque(maxlen=400))
    _filler_regex: re.Pattern[str] = field(init=False)

    def __post_init__(self) -> None:
        # Longest phrases first so multi-word fillers win over their parts.
        phrases = sorted({w.strip().lower() for w in self.filler_words if w.strip()}, key=len, reverse=True)
        if phrases:
            escaped = "|".join(re.escape(p) for p in phrases)
            pattern = rf"\b(?:{escaped})\b"
        else:
            # A pattern that can never match — keeps call sites branch-free.
            pattern = r"(?!x)x"
        self._filler_regex = re.compile(pattern, re.IGNORECASE)

    # ── Ingestion ────────────────────────────────────────────────────
    def add_final(self, text: str, t: float) -> tuple[int, list[str]]:
        """Record a final transcript chunk.

        Returns `(word_count, filler_matches)` where `filler_matches` lists
        each filler occurrence detected in this chunk (with duplicates).
        """
        words = text.split()
        self._total_words += len(words)
        self._word_events.append((t, len(words)))
        self._recent_words.extend(words)

        matches = [m.group(0).lower() for m in self._filler_regex.finditer(text)]
        for match in matches:
            self._filler_hits.append((t, match))
        return len(words), matches

    # ── Pacing ───────────────────────────────────────────────────────
    def window_word_count(self, t: float) -> int:
        """Words spoken inside the trailing WPM window."""
        cutoff = t - self.window_seconds
        if self._word_events and self._word_events[0][0] > cutoff:
            return self._total_words
        return sum(n for event_t, n in self._word_events if event_t >= cutoff)

    def wpm(self, t: float, started_at: float | None = None) -> float:
        """Running words-per-minute over the trailing window.

        Uses elapsed speech time instead of the full window early in the
        session so the number is meaningful from the first seconds. All word
        events occur after `started_at`, so early in a session the trailing
        window naturally covers every word spoken so far.
        """
        if started_at is None:
            effective_window = self.window_seconds
        else:
            effective_window = min(self.window_seconds, max(t - started_at, 0.0))
        if effective_window < 1.0:
            return 0.0
        return round(self.window_word_count(t) * 60.0 / effective_window, 1)

    def overall_wpm(self, t: float, started_at: float) -> float:
        """Words-per-minute across the whole session so far."""
        elapsed = max(t - started_at, 0.0)
        if elapsed < 1.0 or self._total_words == 0:
            return 0.0
        return round(self._total_words * 60.0 / elapsed, 1)

    # ── Fillers ──────────────────────────────────────────────────────
    @property
    def filler_count(self) -> int:
        return len(self._filler_hits)

    def filler_burst(self, t: float) -> int:
        """Filler occurrences within the trailing burst window."""
        cutoff = t - self.filler_burst_window_seconds
        return sum(1 for hit_t, _ in self._filler_hits if hit_t >= cutoff)

    def recent_filler_word(self) -> str | None:
        """The most recently detected filler word, if any."""
        return self._filler_hits[-1][1] if self._filler_hits else None

    # ── Context ──────────────────────────────────────────────────────
    def context_text(self, max_words: int) -> str:
        """Trailing transcript words, most recent last."""
        if len(self._recent_words) <= max_words:
            return " ".join(self._recent_words)
        return " ".join(list(self._recent_words)[-max_words:])

    @property
    def total_words(self) -> int:
        return self._total_words
