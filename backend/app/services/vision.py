"""Silent background screen-vision worker (copilot upgrade F5).

Captures the primary display at a fixed cadence (default 2 s), perceptual-hash
gates unchanged frames, rate-limits the actual multimodal calls, and keeps a
short rolling "screen context" summary that the copilot, the script-auditor
LLM and the RAG anticipator merge into their prompts.

Prompts are NOT hardcoded: they live in settings (`VISION_PROMPT` /
`VISION_ASK_PROMPT`) so they can be tuned from `backend/.env` without code
changes.

Vision backend chain (first usable answer wins):
  1. Gemini `VISION_MODEL`          (gemini-3.5-flash)
  2. Gemini `VISION_FALLBACK_MODEL` (gemini-3.5-flash-lite — higher free RPM)
  3. Azure OpenAI gpt-4o            (multimodal chat completions)
  4. NVIDIA NIM vision model         (OpenAI-compatible integrate API)

Each provider has its own cooldown ("circuit breaker"): a 429 cools it down
for 5 minutes, any other failure for 60 s, so one exhausted free tier never
blocks screen awareness. All capture and network work runs off the event
loop (`asyncio.to_thread` / async client) so the overlay and the live audio
path never block.
"""

import asyncio
import base64
import hashlib
import io
import time

import httpx
import PIL.Image
from loguru import logger

from app.config.settings import Settings

_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
_NVIDIA_BASE = "https://integrate.api.nvidia.com/v1/chat/completions"

_PROVIDER_RATE_LIMIT_COOLDOWN = 300.0   # 429 — free tier exhausted for now
_PROVIDER_ERROR_COOLDOWN = 60.0         # network/5xx/bad-key — retry sooner


class _RateLimited(Exception):
    """The provider answered 429 — its quota is exhausted for now."""


def _grab_frame() -> tuple[bytes, int] | None:
    """Capture monitor 1 → downscaled JPEG + 64-bit average hash (thread).

    `mss` is deliberately not a production dependency: hosted deployments use
    browser-shared frames only. A native desktop build can install it
    separately; without it this helper simply returns `None`.
    """
    try:
        import mss  # optional native-capture extra

        with mss.mss() as sct:
            monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
            shot = sct.grab(monitor)
            image = PIL.Image.frombytes("RGB", shot.size, shot.rgb)

        image.thumbnail((1024, 1024))
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=70)
        jpeg = buffer.getvalue()

        # Average hash (aHash) over an 8x8 grayscale thumbnail.
        small = image.convert("L").resize((8, 8))
        pixels = list(small.getdata())
        average = sum(pixels) / len(pixels)
        bits = 0
        for index, pixel in enumerate(pixels):
            if pixel > average:
                bits |= 1 << index
        return jpeg, bits
    except Exception as exc:  # noqa: BLE001 — screen capture must never crash the app
        logger.debug(f"Screen grab failed: {exc}")
        return None


def _hash_similarity(a: int, b: int) -> float:
    distance = bin(a ^ b).count("1")
    return 1.0 - distance / 64.0


def resolve_vision_providers(settings: Settings) -> list[str]:
    """Ordered vision provider names available with the configured keys."""
    provider = settings.vision_provider.lower()
    gemini = ["gemini", "gemini-lite"] if settings.gemini_api_key else []
    azure = ["azure"] if (settings.azure_openai_api_key and settings.azure_openai_endpoint) else []
    nvidia = ["nvidia"] if settings.nvidia_api_key else []
    if provider == "gemini":
        return gemini
    if provider == "azure":
        return azure
    if provider == "nvidia":
        return nvidia
    return gemini + azure + nvidia  # "auto"


class ScreenVisionWorker:
    """Background vision worker.

    In ``server`` mode it silently captures the host display with mss (desktop
    only). In ``client`` mode it never touches the host display: the browser
    sends the frames the user explicitly chose to share, and the worker keeps
    only the latest frame plus a short-lived derived description.
    """

    def __init__(self, settings: Settings, *, capture_source: str = "server") -> None:
        self._settings = settings
        self._capture_source = capture_source
        self._providers = resolve_vision_providers(settings)
        self._cooldowns: dict[str, float] = {}  # provider → skip-until (monotonic)
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._client: httpx.AsyncClient | None = None
        self._last_hash: int | None = None
        self._last_call_at: float = -1e9
        self._last_ondemand_at: float = -1e9
        self._context: str | None = None
        self._context_at: float = -1e9
        self._failure_streak = 0
        # Client-frame mode: latest browser frame + its byte hash. Frames are
        # never persisted; only the provider's short description is retained.
        self._latest_frame: bytes | None = None
        self._latest_frame_hash: str | None = None
        self._latest_frame_at: float = -1e9
        self._last_analyzed_hash: str | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────
    def start(self) -> None:
        if self._task is not None:
            return
        if not self._providers:
            logger.warning(
                "Screen-vision worker has no usable provider — check "
                "GEMINI_API_KEY / AZURE_OPENAI_* / NVIDIA_API_KEY"
            )
            return
        self._stop_event.clear()
        # Short connect timeout: a dead provider must fail fast so the chain
        # reaches the next one within the copilot's answer budget.
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=4.0))
        self._task = asyncio.create_task(self._loop())
        source = "browser frames" if self._capture_source == "client" else "server display"
        logger.info(
            f"Screen-vision worker started ({source}; "
            f"providers={' -> '.join(self._providers)}, "
            f"interval={self._settings.vision_interval_seconds}s, "
            f"rate-limited to one call / {self._settings.vision_min_interval_calls}s)"
        )

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001,E722
                pass
            self._task = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self._latest_frame = None
        self._latest_frame_hash = None
        self._latest_frame_at = -1e9
        self._last_analyzed_hash = None

    @property
    def active(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def providers(self) -> list[str]:
        return list(self._providers)

    @property
    def capture_source(self) -> str:
        return self._capture_source

    def submit_frame(self, jpeg: bytes) -> None:
        """Accept a browser-shared JPEG frame without blocking the audio loop.

        The worker's loop decides when to spend a provider call. Keeping only
        the latest frame bounds memory and guarantees stale screens are never
        analyzed after the user stops sharing.
        """
        if not jpeg or len(jpeg) > 4_000_000:
            return
        self._latest_frame = jpeg
        self._latest_frame_hash = hashlib.sha1(jpeg).hexdigest()
        self._latest_frame_at = time.monotonic()

    # ── Context access ─────────────────────────────────────────────────
    def latest_context(self) -> str | None:
        """Fresh screen context for prompt fusion, or None if stale/absent."""
        if self._context is None:
            return None
        age = time.monotonic() - self._context_at
        if age > self._settings.vision_context_max_age_seconds:
            return None
        return self._context

    async def describe_now(self, question: str | None = None) -> str | None:
        """On-demand capture + description, bypassing the change gate.

        Used when the user asks the copilot a question from the dialogue box:
        the answer should reflect what is on screen *right now*. The prompt
        comes from `VISION_ASK_PROMPT` (with a `{question}` placeholder) when
        a question is supplied, else from `VISION_PROMPT`. Provider cooldowns
        are respected via the shared `_describe` chain.
        """
        now = time.monotonic()
        if now - self._last_ondemand_at < 1.5:
            return self.latest_context()
        self._last_ondemand_at = now

        if self._capture_source == "client":
            jpeg = self._latest_frame
            client_hash = self._latest_frame_hash
            if (
                jpeg is None
                or client_hash is None
                or now - self._latest_frame_at
                > self._settings.vision_context_max_age_seconds
            ):
                return self.latest_context()
            frame_hash: int | str = client_hash
        else:
            grabbed = await asyncio.to_thread(_grab_frame)
            if grabbed is None:
                return None
            jpeg, frame_hash = grabbed

        if question:
            try:
                prompt = self._settings.vision_ask_prompt.format(question=question)
            except (KeyError, IndexError):
                # Template uses unknown placeholders — fall back verbatim.
                prompt = self._settings.vision_ask_prompt
        else:
            prompt = self._settings.vision_prompt
        summary = await self._describe(jpeg, prompt=prompt, max_tokens=200)
        if summary:
            if self._capture_source == "client":
                self._last_analyzed_hash = str(frame_hash)
            else:
                self._last_hash = int(frame_hash)
            self._context = summary
            self._context_at = time.monotonic()
            self._failure_streak = 0
        return summary

    # ── Worker loop ────────────────────────────────────────────────────
    async def _loop(self) -> None:
        while not self._stop_event.is_set():
            await asyncio.sleep(self._settings.vision_interval_seconds)
            if self._stop_event.is_set():
                return
            try:
                if self._capture_source == "client":
                    jpeg = self._latest_frame
                    client_hash = self._latest_frame_hash
                    if jpeg is None or client_hash is None:
                        continue
                    if client_hash == self._last_analyzed_hash:
                        continue  # unchanged browser frame — no API call
                    now = time.monotonic()
                    if (
                        now - self._last_call_at
                        < self._settings.vision_min_interval_calls
                    ):
                        continue  # rate limit — protects every provider's quota
                    self._last_call_at = now
                    summary = await self._describe(jpeg)
                    if summary:
                        self._last_analyzed_hash = client_hash
                        self._context = summary
                        self._context_at = time.monotonic()
                        self._failure_streak = 0
                    continue

                grabbed = await asyncio.to_thread(_grab_frame)
                if grabbed is None:
                    continue
                jpeg, frame_hash = grabbed

                if (
                    self._last_hash is not None
                    and _hash_similarity(self._last_hash, frame_hash)
                    >= self._settings.vision_change_similarity
                ):
                    continue  # screen unchanged — no API call

                now = time.monotonic()
                if now - self._last_call_at < self._settings.vision_min_interval_calls:
                    continue  # rate limit — protects every provider's quota
                self._last_call_at = now

                summary = await self._describe(jpeg)
                if summary:
                    self._last_hash = frame_hash
                    self._context = summary
                    self._context_at = time.monotonic()
                    self._failure_streak = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — one bad frame never kills the loop
                self._failure_streak += 1
                if self._failure_streak <= 3:
                    logger.warning(f"Vision worker error: {exc}")

    # ── Provider chain ─────────────────────────────────────────────────
    async def _describe(
        self, jpeg: bytes, prompt: str | None = None, max_tokens: int = 120
    ) -> str | None:
        """Try each healthy provider in order; first usable text wins."""
        assert self._client is not None
        if prompt is None:
            prompt = self._settings.vision_prompt
        now = time.monotonic()
        for name in self._providers:
            if now < self._cooldowns.get(name, -1e9):
                continue  # this provider just failed — skip it this round
            try:
                if name == "gemini":
                    text = await self._describe_gemini(
                        self._settings.vision_model, jpeg, prompt, max_tokens
                    )
                elif name == "gemini-lite":
                    text = await self._describe_gemini(
                        self._settings.vision_fallback_model, jpeg, prompt, max_tokens
                    )
                elif name == "azure":
                    text = await self._describe_azure(jpeg, prompt, max_tokens)
                else:
                    text = await self._describe_nvidia(jpeg, prompt, max_tokens)
            except _RateLimited:
                self._cooldowns[name] = time.monotonic() + _PROVIDER_RATE_LIMIT_COOLDOWN
                logger.warning(
                    f"Vision provider '{name}' rate-limited (429) — cooling down "
                    f"{_PROVIDER_RATE_LIMIT_COOLDOWN:.0f}s, falling back"
                )
                continue
            except Exception as exc:  # noqa: BLE001
                self._cooldowns[name] = time.monotonic() + _PROVIDER_ERROR_COOLDOWN
                logger.warning(
                    f"Vision provider '{name}' failed ({type(exc).__name__}) — "
                    f"cooling down {_PROVIDER_ERROR_COOLDOWN:.0f}s: {exc}"
                )
                continue
            self._cooldowns.pop(name, None)  # healthy again
            if text:
                logger.debug(f"Vision context via '{name}': {text[:80]}")
                return text
            # 200 OK but empty/blocked — try the next provider
        return None

    async def _describe_gemini(
        self, model: str, jpeg: bytes, prompt: str, max_tokens: int
    ) -> str | None:
        assert self._client is not None
        url = f"{_GEMINI_BASE}/models/{model}:generateContent"
        generation_config: dict[str, object] = {
            "maxOutputTokens": max_tokens,
            "temperature": 0.1,
        }
        # Gemini 3.x spends part of maxOutputTokens on internal reasoning. A
        # screen description is a simple visual task, so keep thinking at its
        # lightest supported level; otherwise a 120-token answer can be
        # swallowed entirely by thought tokens.
        if model.startswith("gemini-2.5-flash"):
            generation_config["thinkingConfig"] = {"thinkingBudget": 0}
        elif model.startswith(("gemini-3.5", "gemini-3.6", "gemini-3-flash")):
            generation_config["thinkingConfig"] = {"thinkingLevel": "minimal"}
        elif model.startswith("gemini-3.1-flash-lite"):
            generation_config["thinkingConfig"] = {"thinkingLevel": "minimal"}
        elif model.startswith("gemini-3"):
            generation_config["thinkingConfig"] = {"thinkingLevel": "low"}

        body = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt},
                        {
                            "inline_data": {
                                "mime_type": "image/jpeg",
                                "data": base64.b64encode(jpeg).decode("ascii"),
                            }
                        },
                    ]
                }
            ],
            "generationConfig": generation_config,
        }
        response = await self._client.post(
            url,
            headers={"x-goog-api-key": self._settings.gemini_api_key},
            json=body,
        )
        if response.status_code == 429:
            raise _RateLimited(model)
        response.raise_for_status()
        candidates = response.json().get("candidates") or []
        parts = candidates[0].get("content", {}).get("parts") if candidates else None
        if not parts:
            # Blocked/empty completion — nothing usable in the response.
            logger.debug(f"Vision response from {model} had no usable parts")
            return None
        text = " ".join(str(part.get("text", "")) for part in parts).strip()
        return text or None

    async def _describe_azure(
        self, jpeg: bytes, prompt: str, max_tokens: int
    ) -> str | None:
        assert self._client is not None
        url = (
            f"{self._settings.azure_openai_endpoint.rstrip('/')}/openai/deployments/"
            f"{self._settings.azure_openai_chat_deployment}/chat/completions"
        )
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64.b64encode(jpeg).decode('ascii')}"
                            },
                        },
                    ],
                }
            ],
            "max_tokens": max_tokens,
            "temperature": 0.1,
        }
        response = await self._client.post(
            url,
            headers={
                "api-key": self._settings.azure_openai_api_key,
                "Content-Type": "application/json",
            },
            params={"api-version": self._settings.azure_openai_api_version},
            json=body,
        )
        if response.status_code == 429:
            raise _RateLimited("azure")
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        text = str(content).strip() if content else ""
        return text or None

    async def _describe_nvidia(
        self, jpeg: bytes, prompt: str, max_tokens: int
    ) -> str | None:
        assert self._client is not None
        body = {
            "model": self._settings.nvidia_vision_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64.b64encode(jpeg).decode('ascii')}"
                            },
                        },
                    ],
                }
            ],
            "max_tokens": max_tokens,
            "temperature": 0.1,
        }
        response = await self._client.post(
            _NVIDIA_BASE,
            headers={
                "Authorization": f"Bearer {self._settings.nvidia_api_key}",
                "Content-Type": "application/json",
            },
            json=body,
        )
        if response.status_code == 429:
            raise _RateLimited("nvidia")
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        text = str(content).strip() if content else ""
        return text or None


# ── App-wide shared worker (owned by the FastAPI lifespan) ───────────────
_shared_worker: "ScreenVisionWorker | None" = None


def set_shared_worker(worker: "ScreenVisionWorker | None") -> None:
    global _shared_worker
    _shared_worker = worker


def shared_worker() -> "ScreenVisionWorker | None":
    return _shared_worker


def vision_available(settings: Settings) -> bool:
    """True when at least one vision provider is configured."""
    return bool(resolve_vision_providers(settings))


def ensure_shared_worker(settings: Settings) -> "ScreenVisionWorker | None":
    """Create + start the shared vision worker on demand (copilot toggle).

    Returns the running worker, or None when no provider is configured. Safe
    to call repeatedly; never crashes the caller.
    """
    global _shared_worker
    if not settings.vision_enabled:
        logger.info("Screen vision is disabled (VISION_ENABLED=false)")
        return None
    if _shared_worker is not None and _shared_worker.active:
        return _shared_worker
    if not vision_available(settings):
        return None
    try:
        worker = ScreenVisionWorker(settings)
        worker.start()
        _shared_worker = worker
        logger.info("Screen-vision worker started on demand (copilot screen toggle)")
        return worker
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Could not start the screen-vision worker: {exc}")
        return None