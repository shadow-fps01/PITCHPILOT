"""The Cluely-style Live Copilot: transcript-aware dialogue.

While the AICoach watches *how* the presenter speaks (pacing, fillers,
outline coverage), the Copilot watches *what the call is about*:

1. It consumes the rolling final transcript (both call sides when native
   capture is on).
2. When the conversation hits a moment that needs help — the other party
   asks a question, raises an objection, negotiates price, goes silent — it
   proactively posts a short, ready-to-speak suggestion into the dialogue
   feed. No clicking, no prompting.
3. The presenter can also type any question into the dialogue box ("Answer
   that", "How do I respond to the churn concern?") and get an answer
   grounded in the transcript and the script outline.

Every answer is LLM-generated (Azure OpenAI → OpenRouter → keyless heuristic
fallback, mirroring the script-auditor chain). All work runs as fire-and-
forget asyncio tasks so the audio path and the overlay never block.
"""

import asyncio
import json
import re
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

import httpx
from loguru import logger

from app.config.settings import Settings

_LLM_TIMEOUT_SECONDS = 18.0
_QUESTION_RE = re.compile(
    r"\b(what|how|why|when|who|where|which|could you|can you|do you|"
    r"are you|is there|tell me|explain|walk me|show me|any)\b.*",
    re.IGNORECASE,
)
_QUESTION_END = "?"
_OBJECTION_PHRASES = (
    "too expensive", "price is", "pricing", "discount", "cheaper",
    "budget", "cost", "think about it", "not sure", "not interested",
    "call you back", "get back to you", "talk to my team", "my partner",
    "competitor", "competitors", "alternative", "why should we",
    "guarantee", "contract", "cancel", "lock in", "risks", "concern",
    "how long does", "what happens if", "who else uses", "case study",
    "integration", "security", "compliance", "gdpr", "timeline",
)
_QUESTION_STARTERS = ("what", "how", "why", "when", "who", "where", "which", "can", "could", "do", "does", "is", "are")


def _looks_like_question(text: str) -> bool:
    stripped = text.strip()
    if stripped.endswith(_QUESTION_END):
        return True
    lower = stripped.lower()
    return any(lower.startswith(starter + " ") for starter in _QUESTION_STARTERS) and len(stripped.split()) >= 3


def _looks_like_objection(text: str) -> bool:
    lower = text.lower()
    return any(phrase in lower for phrase in _OBJECTION_PHRASES)


def _extract_json(raw: str) -> dict:
    """Robustly pull a JSON object out of an LLM reply."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("No JSON object in copilot reply")
    payload = json.loads(text[start : end + 1])
    return payload if isinstance(payload, dict) else {}


def _extract_streaming_text(buffer: str) -> str:
    """Best-effort live value of the `"text"` field in a partial JSON reply.

    Lets us render answer tokens as they arrive even though the model is
    emitting a JSON object (`{"text": "..."}`). Tolerates incomplete escapes
    and an unterminated string.
    """
    key = buffer.find('"text"')
    if key == -1:
        return ""
    colon = buffer.find(":", key + 6)
    if colon == -1:
        return ""
    quote = buffer.find('"', colon + 1)
    if quote == -1:
        return ""
    escapes = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f"}
    out: list[str] = []
    i = quote + 1
    while i < len(buffer):
        char = buffer[i]
        if char == "\\":
            if i + 1 >= len(buffer):
                break  # incomplete escape — wait for the next token
            nxt = buffer[i + 1]
            if nxt == "u" and i + 5 < len(buffer):
                try:
                    out.append(chr(int(buffer[i + 2 : i + 6], 16)))
                    i += 6
                    continue
                except ValueError:
                    pass
            out.append(escapes.get(nxt, nxt))
            i += 2
            continue
        if char == '"':
            break
        out.append(char)
        i += 1
    return "".join(out)


def _clean_freeform(raw: str) -> str | None:
    """Fallback: strip a code fence and return prose when JSON parsing fails."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    text = text.strip()
    return text or None


# ── LLM backends ──────────────────────────────────────────────────────────
class CopilotLLM:
    """Minimal chat-completion client: Azure OpenAI → OpenRouter."""

    def __init__(self, settings: Settings) -> None:
        self._azure_key = settings.azure_openai_api_key
        self._azure_url = (
            f"{settings.azure_openai_endpoint.rstrip('/')}/openai/deployments/"
            f"{settings.azure_openai_chat_deployment}/chat/completions"
            if settings.azure_openai_endpoint
            else ""
        )
        self._azure_params = {"api-version": settings.azure_openai_api_version}
        self._or_key = settings.openrouter_api_key
        self._or_model = settings.openrouter_model
        # Short connect timeout: fail fast onto the next backend when the
        # resolver blips (this machine's DNS is intermittent).
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(_LLM_TIMEOUT_SECONDS, connect=5.0))

    async def aclose(self) -> None:
        await self._client.aclose()

    async def complete(
        self, system: str, user: str, *, max_tokens: int = 300, temperature: float = 0.4
    ) -> str | None:
        """One chat completion; tries Azure then OpenRouter. None when both fail."""
        body = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if self._azure_key and self._azure_url:
            try:
                return await self._post_azure(body)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Copilot Azure call failed: {exc}")
                # This machine's resolver blips on first contact — one retry.
                await asyncio.sleep(1.5)
                try:
                    return await self._post_azure(body)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"Copilot Azure retry failed: {exc}")
        if self._or_key:
            try:
                return await self._post_openrouter(body)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Copilot OpenRouter call failed: {exc}")
        return None

    async def _post_azure(self, body: dict) -> str | None:
        response = await self._client.post(
            self._azure_url,
            headers={"api-key": self._azure_key, "Content-Type": "application/json"},
            params=self._azure_params,
            json=body,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

    async def _post_openrouter(self, body: dict) -> str | None:
        response = await self._client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self._or_key}",
                "Content-Type": "application/json",
            },
            json={**body, "model": self._or_model},
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

    # ── Streaming (token-by-token answers) ────────────────────────────
    async def stream(
        self, system: str, user: str, *, max_tokens: int = 300, temperature: float = 0.4
    ) -> AsyncIterator[str]:
        """Yield content deltas for a chat completion (Azure → OpenRouter).

        Silently stops if a backend fails; the caller falls back to the
        non-streaming `complete()` when nothing arrived.
        """
        body = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if self._azure_key and self._azure_url:
            yielded = False
            try:
                async for delta in self._stream_endpoint(
                    self._azure_url,
                    {"api-key": self._azure_key, "Content-Type": "application/json"},
                    self._azure_params,
                    body,
                ):
                    yielded = True
                    yield delta
                if yielded:
                    return
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Copilot Azure stream failed: {exc}")
                if yielded:
                    return  # partial answer already delivered
        if self._or_key:
            try:
                async for delta in self._stream_endpoint(
                    "https://openrouter.ai/api/v1/chat/completions",
                    {
                        "Authorization": f"Bearer {self._or_key}",
                        "Content-Type": "application/json",
                    },
                    None,
                    {**body, "model": self._or_model},
                ):
                    yield delta
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Copilot OpenRouter stream failed: {exc}")

    async def _stream_endpoint(
        self, url: str, headers: dict, params: dict | None, body: dict
    ) -> AsyncIterator[str]:
        """Parse an OpenAI-compatible SSE chat stream into content deltas."""
        async with self._client.stream(
            "POST", url, headers=headers, params=params, json=body
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = payload.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                content = delta.get("content")
                if content:
                    yield str(content)


# ── Prompt assembly ──────────────────────────────────────────────────────
# System prompts are NOT hardcoded — they live in settings
# (COPILOT_SUGGEST_PROMPT / COPILOT_ASK_PROMPT) so they can be tuned from
# `backend/.env` without code changes. Only the context block below is built
# here, from the live transcript/outline/screen data.


def _context_block(
    transcript: str, screen: str | None, outline: str, keywords: list[str]
) -> str:
    screen_section = (
        f"SCREEN RIGHT NOW (live vision worker summary):\n\"{screen}\"\n\n" if screen else ""
    )
    outline_section = (
        f"PRESENTER'S OUTLINE: {outline}\nKEYWORDS: {', '.join(keywords)}\n\n"
        if outline.strip() or keywords
        else ""
    )
    return (
        f"{outline_section}{screen_section}"
        f"LIVE CALL TRANSCRIPT (latest last):\n\"{transcript}\""
    )


# ── Output type ───────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class CopilotInsight:
    """A copilot message handed to the WS layer for emit + persistence."""

    id: str
    kind: Literal["suggestion", "answer", "status"]
    text: str
    trigger: str | None = None
    reply_to: str | None = None
    screen_used: bool = False


class HeuristicCopilot:
    """Keyless fallback: answers from the outline + last transcript words."""

    def answer_question(
        self,
        question: str,
        transcript: str,
        screen: str | None,
        outline: str,
        keywords: list[str],
    ) -> str | None:
        del screen  # the heuristic fallback never uses the screen
        q_words = {w.lower() for w in re.findall(r"\w+", question) if len(w) > 3}
        best_line, best_score = None, 0
        for line in outline.splitlines():
            line = line.strip("-• \t")
            if not line:
                continue
            l_words = {w.lower() for w in re.findall(r"\w+", line)}
            overlap = len(q_words & l_words) + sum(
                1 for kw in keywords if kw.lower() in question.lower() and kw.lower() in line.lower()
            )
            if overlap > best_score:
                best_line, best_score = line, overlap
        if best_line is not None and best_score > 0:
            return f"From your outline: {best_line}"
        tail = " ".join(transcript.split()[-25:]) if transcript else ""
        if tail:
            return (
                f"Keyless demo mode — here is the recent call context you can "
                f"lean on: …{tail}"
            )
        return None


# ── The per-session engine ────────────────────────────────────────────────
class CopilotChat:
    """Cluely-style dialogue engine; one instance per live session."""

    def __init__(
        self,
        *,
        settings: Settings,
        outline: str = "",
        keywords: list[str] | None = None,
        llm: CopilotLLM | None,
        screen_provider: Callable[[], str | None] | None = None,
        fresh_screen_provider: (
            Callable[[str | None], Awaitable[str | None]] | None
        ) = None,
        on_message: Callable[[CopilotInsight], Awaitable[None]] | None = None,
        on_delta: Callable[[str, str, str], Awaitable[None]] | None = None,
    ) -> None:
        self._settings = settings
        self._outline = outline
        self._keywords = list(keywords or [])
        self._llm = llm
        self._heuristic = HeuristicCopilot()
        self.screen_provider = screen_provider  # last cached vision summary
        self.fresh_screen_provider = fresh_screen_provider  # on-demand capture
        self.on_message = on_message  # rebound per (re)connection
        self.on_delta = on_delta  # streamed answer tokens (rebound per connection)

        # Final chunks are stored with their speaker so the copilot can tell
        # the presenter's words from the other party's. The coach's analytics
        # deliberately consume only the presenter stream.
        self._transcript: deque[tuple[str, str]] = deque(maxlen=40)
        self._task: asyncio.Task[None] | None = None
        self._last_suggest_t: float = -1e9
        self._last_suggest_words: int = 0
        self._total_words = 0

    # ── Wiring helpers ────────────────────────────────────────────────
    async def _emit(self, insight: CopilotInsight) -> None:
        if self.on_message is None:
            return
        try:
            await self.on_message(insight)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Copilot on_message failed: {exc}")

    async def _emit_delta(self, insight_id: str, reply_to: str, text: str) -> None:
        if self.on_delta is None:
            return
        try:
            await self.on_delta(insight_id, reply_to, text)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Copilot on_delta failed: {exc}")

    # ── Proactive path (transcript-driven) ────────────────────────────
    def feed_final(self, text: str, speaker: str = "presenter") -> None:
        """Add a final transcript chunk; maybe fire a proactive suggestion.

        `speaker` is either `"presenter"` (microphone) or `"other"` (the
        shared call/system-audio stream). Both are used as copilot context,
        but only the presenter stream feeds the speaking coach's metrics.
        """
        text = text.strip()
        if not text:
            return
        speaker = "other" if speaker == "other" else "presenter"
        self._transcript.append((speaker, text))
        self._total_words += len(text.split())
        if not self._settings.copilot_enabled:
            return
        now = time.monotonic()
        if _looks_like_question(text) or _looks_like_objection(text):
            trigger = "Question/objection detected in the call"
        elif (
            self._total_words - self._last_suggest_words
            >= self._settings.copilot_suggest_every_words
            and self._total_words >= self._settings.copilot_min_words_to_suggest
        ):
            trigger = "Moment to move the conversation forward"
        else:
            return
        if now - self._last_suggest_t < self._settings.copilot_suggest_min_gap_seconds:
            return
        if self._task is not None and not self._task.done():
            return  # one generation in flight
        self._last_suggest_t = now
        self._last_suggest_words = self._total_words
        self._task = asyncio.create_task(self._generate_suggestion(trigger))

    def _transcript_text(self) -> str:
        """Format the rolling transcript with explicit speaker labels."""
        lines = [
            f"{'Other party' if speaker == 'other' else 'Presenter'}: {text}"
            for speaker, text in self._transcript
        ]
        return "\n".join(lines)[-1600:]

    async def _generate_suggestion(self, trigger: str) -> None:
        transcript = self._transcript_text()
        if len(transcript.split()) < self._settings.copilot_min_words_to_suggest:
            return
        original_trigger = trigger
        screen = self.screen_provider() if self.screen_provider is not None else None
        user_prompt = (
            f"{_context_block(transcript, screen, self._outline, self._keywords)}\n\n"
            f"DETECTED MOMENT: {trigger}\n"
            "Reply with the JSON object now."
        )
        text: str | None = None
        if self._llm is not None:
            raw = await self._llm.complete(
                self._settings.copilot_suggest_prompt, user_prompt,
                max_tokens=220, temperature=0.5,
            )
            if raw:
                try:
                    payload = _extract_json(raw)
                    candidate = payload.get("text")
                    text = candidate.strip() if isinstance(candidate, str) and candidate.strip() else None
                    trig = payload.get("trigger")
                    if isinstance(trig, str) and trig.strip():
                        trigger = trig.strip()
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"Copilot suggestion parse failed: {exc}")
        if text is None and original_trigger == "Question/objection detected in the call":
            text = self._heuristic.answer_question(
                transcript[-200:], transcript, screen, self._outline, self._keywords
            )
        if text:
            await self._emit(
                CopilotInsight(
                    id=uuid.uuid4().hex,
                    kind="suggestion",
                    text=text,
                    trigger=trigger,
                    screen_used=screen is not None,
                )
            )

    # ── Reactive path (user-typed question) ───────────────────────────
    @staticmethod
    def _parse_answer(raw: str) -> str | None:
        """Pull the answer out of an LLM reply (JSON, or tolerated prose)."""
        if not raw or not raw.strip():
            return None
        try:
            payload = _extract_json(raw)
        except Exception:  # noqa: BLE001 — not JSON: treat as prose
            return _clean_freeform(raw)
        candidate = payload.get("text")
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
        return None

    async def ask(self, question: str, reply_to: str) -> None:
        """Answer a question typed into the dialogue box; emits an `answer`.

        With an LLM configured, answer tokens are streamed to the overlay via
        `on_delta` as they arrive, then a final `answer` insight closes the
        message (same `id`). Falls back to the keyless heuristic on any error.
        """
        transcript = self._transcript_text()
        screen: str | None = None
        if self.fresh_screen_provider is not None:
            try:
                screen = await self.fresh_screen_provider(question)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Copilot fresh-screen capture failed: {exc}")
            if screen is None and self.screen_provider is not None:
                screen = self.screen_provider()
        elif self.screen_provider is not None:
            screen = self.screen_provider()

        insight_id = uuid.uuid4().hex
        text: str | None = None
        last_visible = ""
        if self._llm is not None:
            user_prompt = (
                f"{_context_block(transcript, screen, self._outline, self._keywords)}\n\n"
                f"PRESENTER'S QUESTION: \"{question}\"\n"
                "Reply with the JSON object now."
            )
            raw = ""
            if self._settings.copilot_stream_answers and self.on_delta is not None:
                last_emit = 0.0
                try:
                    async for chunk in self._llm.stream(
                        self._settings.copilot_ask_prompt, user_prompt,
                        max_tokens=280, temperature=0.4,
                    ):
                        raw += chunk
                        visible = _extract_streaming_text(raw)
                        if not visible or visible == last_visible:
                            continue
                        now = time.monotonic()
                        if now - last_emit < 0.05:  # throttle WS chatter
                            continue
                        last_emit = now
                        last_visible = visible
                        await self._emit_delta(insight_id, reply_to, visible)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"Copilot streaming answer failed: {exc}")
            else:
                raw = (
                    await self._llm.complete(
                        self._settings.copilot_ask_prompt, user_prompt,
                        max_tokens=280, temperature=0.4,
                    )
                    or ""
                )
            text = self._parse_answer(raw)
            if text is None and last_visible:
                text = last_visible  # JSON never closed — keep what streamed

        if text is None:
            text = self._heuristic.answer_question(
                question, transcript, screen, self._outline, self._keywords
            )
        if text is None:
            text = (
                "I could not generate an answer just now — the answer service "
                "did not respond. Try again in a moment."
            )
        await self._emit(
            CopilotInsight(
                id=insight_id,
                kind="answer",
                text=text,
                trigger=question[:120],
                reply_to=reply_to,
                screen_used=screen is not None,
            )
        )
        logger.info(
            f"Copilot answer ready ({len(text)} chars, screen_used={screen is not None}, "
            f"streamed={bool(last_visible)})"
        )

    # ── Wrap-up ───────────────────────────────────────────────────────
    async def aclose(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass


def build_copilot_llm(settings: Settings) -> CopilotLLM | None:
    """Return a chat LLM when any key backend is configured, else None."""
    if settings.azure_openai_api_key or settings.openrouter_api_key:
        return CopilotLLM(settings)
    return None
