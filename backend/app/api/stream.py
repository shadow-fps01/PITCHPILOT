"""The live duplex audio stream: `/api/ws/stream/{session_id}`.

Protocol
--------
Client → Server
  * binary frames: `b"PPA1"` + one source byte (`1` = presenter/mic,
    `2` = other party/shared call audio) + 16 kHz 16-bit mono PCM
    (sent every ~160 ms). Untagged legacy PCM is treated as presenter audio.
  * `{"type": "ping", "timestamp": ...}` heartbeat
  * `{"type": "stop"}` to end the session gracefully

Server → Client (typed models from `app.schemas.stream`)
  * `session_started` → `transcript` / `metrics` / `cue` stream → `session_ended`
  * `pong` / `error`

Auth: browsers cannot set WebSocket headers, so the client first mints a
short-lived ticket (`POST /api/auth/ws-ticket`) and passes it as `?ticket=`.
Long-lived JWTs are deliberately rejected in the query string so they cannot
leak into proxy access logs. Sessions survive client disconnects for a grace
period (`WS_RECONNECT_GRACE_SECONDS`) via the `LiveSessionRegistry`.
"""

import asyncio
import base64
import binascii
import time
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select

from app.config.settings import get_settings
from app.db.session import async_session_factory
from app.models.coaching_session import CoachingSession
from app.models.script import Script
from app.models.session_event import EventType, SessionEvent
from app.models.user import User
from app.schemas.stream import (
    CaptureCommand,
    CaptureStatusMessage,
    ClientAsk,
    ClientMessage,
    ClientPing,
    ClientScreenFrame,
    ClientStop,
    ClientTune,
    ContextCardMessage,
    CopilotDeltaMessage,
    CopilotMessage,
    CopilotSource,
    CueMessage,
    DebriefMessage,
    ErrorMessage,
    MetricsMessage,
    PongMessage,
    ScreenCommand,
    ServerMessage,
    SessionEndedMessage,
    SessionStartedMessage,
    SessionStatusMessage,
)
from app.services.anticipation import SurfacedCard
from app.services.capture import NativeCaptureService, capture_available
from app.services.copilot_chat import CopilotInsight
from app.services.debrief import generate_debrief
from app.services.session_registry import registry
from app.services.rate_limit import consume_daily_budget
from app.services.stt import STTUnavailable, TranscriptChunk, build_stt_provider
from app.services.vision import (
    ScreenVisionWorker,
    shared_worker,
    vision_available,
)
from app.services.ws_tickets import ws_tickets

router = APIRouter()

_client_message_adapter = TypeAdapter(ClientMessage)

_WS_UNAUTHORIZED = 4401
_WS_FORBIDDEN = 4403
_WS_BAD_REQUEST = 4400
_WS_ALREADY_ENDED = 4410

MAX_QUEUE_SIZE = 500
MAX_SESSION_SECONDS = 90 * 60
MAX_CONCURRENT_SESSIONS_PER_USER = 2
ASK_MIN_INTERVAL_SECONDS = 3.0
MAX_ASKS_PER_SESSION = 20

_AUDIO_MAGIC = b"PPA1"
_AUDIO_PRESENTER = 1
_AUDIO_OTHER = 2


def split_audio_frame(data: bytes) -> tuple[str, bytes]:
    """Split a tagged PCM frame into `(speaker, pcm16)`.

    The browser tags each frame so microphone and shared call audio can be
    transcribed independently (the coach must never count the other party's
    words as the presenter's). Old/untagged clients are treated as presenter
    audio for backwards compatibility.
    """
    if len(data) >= len(_AUDIO_MAGIC) + 1 and data.startswith(_AUDIO_MAGIC):
        source = data[len(_AUDIO_MAGIC)]
        pcm = data[len(_AUDIO_MAGIC) + 1 :]
        if source == _AUDIO_OTHER:
            return "other", pcm
        return "presenter", pcm
    return "presenter", data


async def _persist_event(session_id: uuid.UUID, cue: CueMessage, elapsed: float) -> None:
    """Store one coaching event; persistence failures never break the stream."""
    try:
        async with async_session_factory() as db:
            db.add(
                SessionEvent(
                    session_id=session_id,
                    timestamp=round(elapsed, 2),
                    event_type=cue.cue_type,
                    message=cue.message,
                    raw_transcript_snippet=cue.raw_transcript_snippet,
                )
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Failed to persist session event for {session_id}: {exc}")


async def _persist_card_event(
    session_id: uuid.UUID, card: SurfacedCard, elapsed: float
) -> None:
    """Store a surfaced RAG context card as a session event (F3)."""
    try:
        async with async_session_factory() as db:
            db.add(
                SessionEvent(
                    session_id=session_id,
                    timestamp=round(elapsed, 2),
                    event_type=EventType.CONTEXT_CARD,
                    message=f"Pre-fetched: {card.title}",
                    raw_transcript_snippet=card.content[:200],
                )
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Failed to persist context card for {session_id}: {exc}")


async def _persist_copilot_event(
    session_id: uuid.UUID,
    kind: str,
    text: str,
    trigger: str | None,
    elapsed: float,
) -> None:
    """Store one copilot dialogue message as a session event."""
    label = "Suggested" if kind == "suggestion" else "Answered"
    message = f"{label}: {text[:180]}"
    if trigger:
        message = f"[{trigger}] {message}"
    try:
        async with async_session_factory() as db:
            db.add(
                SessionEvent(
                    session_id=session_id,
                    timestamp=round(elapsed, 2),
                    event_type=EventType.COPILOT_ASSIST,
                    message=message,
                )
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Failed to persist copilot event for {session_id}: {exc}")


@router.websocket("/ws/stream/{session_id}")
async def stream_endpoint(websocket: WebSocket, session_id: str) -> None:
    settings = get_settings()
    await websocket.accept()
    outgoing: asyncio.Queue[ServerMessage] = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)

    def emit(message: ServerMessage) -> None:
        try:
            outgoing.put_nowait(message)
        except asyncio.QueueFull:  # a stalled client must not stall the coach
            try:
                outgoing.get_nowait()
                outgoing.put_nowait(message)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass

    async def reject(message: str, code: int) -> None:
        emit(ErrorMessage(message=message))
        try:
            await websocket.send_text(ErrorMessage(message=message).model_dump_json())
            await websocket.close(code=code)
        except Exception:  # noqa: BLE001  -  client may already be gone
            pass
        logger.info(f"Stream rejected ({code}): {message}")

    # ── Handshake: session id → single-use ticket → user ───────────────
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        await reject("Invalid session id", _WS_BAD_REQUEST)
        return

    ticket = websocket.query_params.get("ticket", "")
    if not ticket:
        await reject("Missing connection ticket", _WS_UNAUTHORIZED)
        return
    ticket_user_id = await ws_tickets.resolve(ticket, sid)
    if ticket_user_id is None:
        await reject(
            "This connection link has expired  -  please reconnect",
            _WS_UNAUTHORIZED,
        )
        return
    try:
        user_id = uuid.UUID(ticket_user_id)
    except ValueError:
        await reject("Invalid connection ticket", _WS_UNAUTHORIZED)
        return

    try:
        async with async_session_factory() as db:
            session = await db.get(CoachingSession, sid)
            if session is None or session.user_id != user_id:
                await reject("Session not found", _WS_FORBIDDEN)
                return
            if session.ended_at is not None:
                await reject("This session has already ended", _WS_ALREADY_ENDED)
                return
            user = await db.get(User, user_id)
            if user is None:
                await reject("Account not found", _WS_UNAUTHORIZED)
                return
            if settings.require_email_verification and not user.email_verified:
                await reject(
                    "Please verify your email address to start coaching",
                    _WS_FORBIDDEN,
                )
                return
            if settings.require_recording_consent and not user.consent_current:
                await reject(
                    "Please confirm that you have permission to record this call",
                    _WS_FORBIDDEN,
                )
                return
            script: Script | None = None
            if session.script_id is not None:
                script = await db.scalar(select(Script).where(Script.id == session.script_id))
    except Exception as exc:  # noqa: BLE001  -  DB issues must not hang the socket
        logger.warning(f"Stream handshake DB failure: {exc}")
        await reject("Could not load the session", 5000)
        return

    # Allow reconnects to an existing live session, but cap how many distinct
    # sessions one account can keep billable at once.
    if (
        registry.get(sid) is None
        and registry.active_for_user(user_id) >= MAX_CONCURRENT_SESSIONS_PER_USER
    ):
        await reject(
            "You already have the maximum number of live sessions open. "
            "Close one before starting another.",
            _WS_FORBIDDEN,
        )
        return

    # Global cap protects the shared provider budget/worker from a burst of
    # new sessions. Reconnects to an existing live session are still allowed.
    if (
        registry.get(sid) is None
        and registry.active_count() >= settings.max_concurrent_sessions
    ):
        await reject(
            "PitchPilot is at capacity right now. Please try again in a moment.",
            1013,
        )
        return

    state, created = registry.get_or_create(session, script)
    resumed = not created
    state.coach.start_clock()

    # ── Wire the coach into this connection ───────────────────────────
    async def on_cue(cue: CueMessage) -> None:
        await _persist_event(sid, cue, state.coach.elapsed())
        emit(cue)

    state.coach.on_cue = on_cue

    # ── Wire the RAG anticipator (F3) into this connection ───────────
    async def on_context_card(card: SurfacedCard) -> None:
        state.context_cards += 1
        await _persist_card_event(sid, card, state.coach.elapsed())
        emit(
            ContextCardMessage(
                title=card.title,
                content=card.content,
                score=card.score,
                keywords=card.keywords,
            )
        )

    if state.anticipation is not None:
        state.anticipation.on_card = on_context_card

    # ── Wire the Cluely-style copilot dialogue into this connection ────
    async def on_copilot_message(insight: "CopilotInsight") -> None:
        await _persist_copilot_event(
            sid, insight.kind, insight.text, insight.trigger, state.coach.elapsed()
        )
        emit(
            CopilotMessage(
                id=insight.id,
                kind=insight.kind,
                text=insight.text,
                trigger=insight.trigger,
                reply_to=insight.reply_to,
                screen_used=insight.screen_used,
                sources=[
                    CopilotSource(title=title, url=url)
                    for title, url in insight.sources
                ],
            )
        )

    if state.copilot is not None:
        state.copilot.on_message = on_copilot_message

    async def on_copilot_delta(insight_id: str, reply_to: str, text: str) -> None:
        emit(CopilotDeltaMessage(id=insight_id, reply_to=reply_to, text=text))

    if state.copilot is not None:
        state.copilot.on_delta = on_copilot_delta

    async def handle_ask(message: ClientAsk) -> None:
        if state.copilot is None:
            emit(
                CopilotMessage(
                    id=uuid.uuid4().hex,
                    kind="error",
                    text=(
                        "The suggestion service is unavailable right now. "
                        "Retry in a moment."
                    ),
                    reply_to=message.id,
                )
            )
            return
        question = message.text.strip()
        if not question:
            return
        now = time.monotonic()
        if now - state.last_ask_at < ASK_MIN_INTERVAL_SECONDS:
            emit(
                CopilotMessage(
                    id=uuid.uuid4().hex,
                    kind="error",
                    text="Please wait a moment before asking another question.",
                    reply_to=message.id,
                )
            )
            return
        if state.ask_count >= MAX_ASKS_PER_SESSION:
            emit(
                CopilotMessage(
                    id=uuid.uuid4().hex,
                    kind="error",
                    text=(
                        f"Ask limit reached for this session "
                        f"({MAX_ASKS_PER_SESSION} questions). Start a new session "
                        "to continue."
                    ),
                    reply_to=message.id,
                )
            )
            return
        budget = await consume_daily_budget(
            "asks", user_id, settings.daily_ask_limit
        )
        if not budget.allowed:
            emit(
                CopilotMessage(
                    id=uuid.uuid4().hex,
                    kind="error",
                    text=(
                        "Daily copilot question limit reached. "
                        "Please try again tomorrow."
                    ),
                    reply_to=message.id,
                )
            )
            return
        state.last_ask_at = now
        state.ask_count += 1
        logger.info(f"Copilot question received on session {sid} ({len(question)} chars)")
        asyncio.create_task(state.copilot.ask(question, message.id))

    # Browser screen sharing is explicitly toggled by the client. Keep the
    # flag per connection so a frame that was already in flight when the user
    # pressed "stop" cannot silently restart the worker.
    screen_client_on = {"value": False}
    last_screen_frame_at = {"value": -1e9}

    def bind_screen_worker(worker: ScreenVisionWorker) -> None:
        """Point this session's copilot/coach at the browser-frame worker."""
        if state.copilot is not None:
            state.copilot.screen_provider = worker.latest_context
            state.copilot.fresh_screen_provider = worker.describe_now
        state.coach.screen_context_provider = worker.latest_context

    async def ensure_screen_worker() -> ScreenVisionWorker | None:
        if not settings.vision_client_enabled or not vision_available(settings):
            return None
        worker = state.screen_worker
        if worker is not None and worker.active:
            bind_screen_worker(worker)
            return worker
        worker = ScreenVisionWorker(settings, capture_source="client")
        worker.start()
        if not worker.active:
            return None
        state.screen_worker = worker
        bind_screen_worker(worker)
        return worker

    def vision_is_active() -> bool:
        if state.screen_worker is not None and state.screen_worker.active:
            return True
        worker = shared_worker()
        return worker is not None and worker.active

    async def handle_screen_command(message: ScreenCommand) -> None:
        if message.action == "start":
            screen_client_on["value"] = True
            worker = await ensure_screen_worker()
            if worker is None:
                screen_client_on["value"] = False
                emit(
                    CopilotMessage(
                        id=uuid.uuid4().hex,
                        kind="status",
                        text="Screen vision isn't available right now.",
                    )
                )
                emit(status_message())
                return
            emit(
                CopilotMessage(
                    id=uuid.uuid4().hex,
                    kind="status",
                    text="Screen vision on  -  I can see what you share now.",
                )
            )
        else:
            screen_client_on["value"] = False
            worker = state.screen_worker
            state.screen_worker = None
            if worker is not None:
                await worker.stop()
            if state.copilot is not None:
                state.copilot.screen_provider = None
                state.copilot.fresh_screen_provider = None
            state.coach.screen_context_provider = None
            emit(
                CopilotMessage(
                    id=uuid.uuid4().hex,
                    kind="status",
                    text="Screen vision off  -  I'll rely on the call audio only.",
                )
            )
        emit(status_message())

    async def handle_screen_frame(message: ClientScreenFrame) -> None:
        """Accept a browser-shared JPEG, analyze it, then discard it.

        The worker only keeps the latest frame and its derived description;
        the image itself is never persisted or logged.
        """
        if not screen_client_on["value"]:
            return
        now = time.monotonic()
        if now - last_screen_frame_at["value"] < 0.4:
            return  # ignore floods / duplicate in-flight frames
        last_screen_frame_at["value"] = now

        worker = await ensure_screen_worker()
        if worker is None:
            screen_client_on["value"] = False
            emit(
                CopilotMessage(
                    id=uuid.uuid4().hex,
                    kind="status",
                    text="Screen vision isn't available right now.",
                )
            )
            emit(status_message())
            return

        image = message.image.strip()
        if image.startswith("data:"):
            _, _, image = image.partition(",")
        try:
            jpeg = base64.b64decode(image, validate=True)
        except (ValueError, binascii.Error):
            return
        if len(jpeg) < 4 or not jpeg.startswith(b"\xff\xd8\xff"):
            return
        worker.submit_frame(jpeg)

    # ── Native dual-audio capture handoff (F1) ─────────────────────────
    loop = asyncio.get_running_loop()
    capture_level = {"value": 0.0}
    capture_in_flight = {"count": 0}

    def on_capture_frame(pcm: bytes) -> None:
        """Thread → loop: hand a mixed PCM frame to the STT provider."""
        if capture_in_flight["count"] > 8:  # STT wedged  -  shed, don't pile up
            return
        capture_in_flight["count"] += 1

        def done(_: object) -> None:
            capture_in_flight["count"] -= 1

        future = asyncio.run_coroutine_threadsafe(provider.send_audio(pcm), loop)
        future.add_done_callback(done)

    def on_capture_level(level: float) -> None:
        capture_level["value"] = level

    capture_service: NativeCaptureService | None = None

    def status_message() -> SessionStatusMessage:
        return SessionStatusMessage(
            stt_provider=provider_name,
            capture_available=capture_available(settings),
            capture_active=capture_service is not None,
            vision_available=(
                settings.vision_client_enabled and vision_available(settings)
            ),
            vision_active=vision_is_active(),
            anticipation_active=state.anticipation is not None,
            copilot_active=state.copilot is not None and state.copilot.available,
            search_active=settings.search_enabled and bool(settings.tavily_api_key.strip()),
        )

    async def start_capture() -> None:
        nonlocal capture_service
        if capture_service is not None:
            return
        if not capture_available(settings):
            emit(
                CaptureStatusMessage(
                    active=False,
                    mode="off",
                    error="Native capture isn't available right now.",
                )
            )
            return
        try:
            service = NativeCaptureService(
                settings, on_frame=on_capture_frame, on_level=on_capture_level
            )
            service.start()
            capture_service = service
            emit(
                CaptureStatusMessage(
                    active=True,
                    mode=service.mode,  # type: ignore[arg-type]
                    mic=service.mic_name,
                    loopback=service.loopback_name,
                )
            )
        except Exception as exc:  # noqa: BLE001
            emit(CaptureStatusMessage(active=False, mode="off", error=str(exc)))

    async def stop_capture() -> None:
        nonlocal capture_service
        if capture_service is not None:
            capture_service.stop()
            capture_service = None
        capture_level["value"] = 0.0
        emit(CaptureStatusMessage(active=False, mode="off"))

    async def on_transcript(chunk: TranscriptChunk) -> None:
        """Presenter/microphone transcript: coach metrics + copilot context."""
        t = time.monotonic()
        try:
            for message in await state.coach.feed_transcript(chunk.text, chunk.is_final, t):
                if isinstance(message, MetricsMessage):
                    message.mic_level = capture_level["value"]
                emit(message)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Coach feed failure on session {sid}: {exc}")
        if chunk.is_final and state.copilot is not None:
            try:
                state.copilot.feed_final(chunk.text, speaker="presenter")
            except Exception as exc:  # noqa: BLE001  -  copilot must never break audio
                logger.warning(f"Copilot feed failure on session {sid}: {exc}")

    async def on_system_transcript(chunk: TranscriptChunk) -> None:
        """Other-party transcript: copilot context and RAG triggers only.

        Deliberately does NOT call `state.coach.feed_transcript`: WPM, filler
        counts and script coverage must describe the presenter's delivery, not
        the person on the other end of the call.
        """
        if not chunk.is_final:
            return
        if state.copilot is not None:
            try:
                state.copilot.feed_final(chunk.text, speaker="other")
            except Exception as exc:  # noqa: BLE001  -  copilot must never break audio
                logger.warning(f"Copilot system feed failure on session {sid}: {exc}")
        try:
            state.coach.feed_external_transcript(chunk.text)
        except Exception as exc:  # noqa: BLE001  -  anticipation is best-effort
            logger.warning(f"Anticipation system feed failed on session {sid}: {exc}")

    system_provider = None
    system_provider_disabled = False

    async def ensure_system_provider():
        """Lazily open a second STT stream for shared call audio.

        Starting it on the first system-audio frame avoids a second provider
        connection (and its cost) for mic-only sessions.
        """
        nonlocal system_provider, system_provider_disabled
        if system_provider is not None:
            return system_provider
        if system_provider_disabled:
            return None
        if provider_name == "none":
            system_provider_disabled = True
            return None
        try:
            candidate, name = build_stt_provider(on_system_transcript)
            await candidate.start()
        except Exception as exc:  # noqa: BLE001
            system_provider_disabled = True
            logger.warning(
                f"Other-party transcription unavailable on session {sid}: {exc}"
            )
            return None
        system_provider = candidate
        logger.info(
            f"Other-party transcription active on session {sid} via {name}"
        )
        return candidate

    try:
        provider, provider_name = build_stt_provider(on_transcript)
    except STTUnavailable as exc:
        await reject(f"Transcription unavailable: {exc}", 1011)
        return
    emit(SessionStartedMessage(session_id=sid, started_at=state.started_at))

    emit(status_message())

    # ── Background pumps ─────────────────────────────────────────────
    drain = asyncio.Event()

    async def sender_loop() -> None:
        while True:
            try:
                message = await asyncio.wait_for(outgoing.get(), timeout=0.25)
            except asyncio.TimeoutError:
                if drain.is_set() and outgoing.empty():
                    break
                continue
            try:
                await websocket.send_text(message.model_dump_json())
            except Exception:  # noqa: BLE001  -  socket died; stop pumping
                break

    async def ticker_loop() -> None:
        while True:
            await asyncio.sleep(settings.pacing_tick_seconds)
            t = time.monotonic()
            try:
                for message in await state.coach.periodic_metrics(t):
                    if isinstance(message, MetricsMessage):
                        message.mic_level = capture_level["value"]
                    emit(message)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Ticker failure on session {sid}: {exc}")

    sender_task = asyncio.create_task(sender_loop())
    ticker_task = asyncio.create_task(ticker_loop())

    try:
        await provider.start()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"STT provider failed to start ({provider_name}): {exc}")
        emit(ErrorMessage(message=f"Transcription backend failed to start: {exc}"))

    logger.info(
        f"Streaming session {sid} via {provider_name} "
        f"({'reconnected' if resumed else 'new'} connection #{state.connect_count})"
    )

    # Hard wall-clock cap: a live session must not stay billable forever.
    session_expired = {"value": False}

    async def session_watchdog() -> None:
        remaining = MAX_SESSION_SECONDS - (time.time() - state.started_at.timestamp())
        if remaining > 0:
            await asyncio.sleep(remaining)
        session_expired["value"] = True
        emit(
            ErrorMessage(
                message=(
                    f"This session has reached the {MAX_SESSION_SECONDS // 60}-minute "
                    "limit. Start a new session to continue."
                )
            )
        )
        try:
            await asyncio.sleep(0.5)  # give the sender a chance to flush the error
            await websocket.close(code=1000, reason="Session time limit reached")
        except Exception:  # noqa: BLE001  -  socket may already be gone
            pass

    watchdog_task = asyncio.create_task(session_watchdog())

    # ── Receive loop ─────────────────────────────────────────────────
    ended_gracefully = False
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            data = message.get("bytes")
            if data is not None:
                speaker, pcm = split_audio_frame(data)
                if not pcm:
                    continue
                if speaker == "other":
                    other = await ensure_system_provider()
                    if other is not None:
                        try:
                            await other.send_audio(pcm)
                        except Exception as exc:  # noqa: BLE001  -  keep the mic stream alive
                            logger.warning(
                                f"Other-party STT send failed on session {sid}: {exc}"
                            )
                else:
                    await provider.send_audio(pcm)
                continue
            text = message.get("text")
            if text is None:
                continue
            try:
                client_message = _client_message_adapter.validate_json(text)
            except ValidationError:
                emit(ErrorMessage(message="Unrecognized client message"))
                continue
            if isinstance(client_message, ClientPing):
                emit(PongMessage(timestamp=time.time()))
            elif isinstance(client_message, CaptureCommand):
                if client_message.action == "start":
                    await start_capture()
                else:
                    await stop_capture()
            elif isinstance(client_message, ClientAsk):
                await handle_ask(client_message)
            elif isinstance(client_message, ClientTune):
                state.coach.apply_profile(client_message.sensitivity)
                logger.info(
                    f"Coaching sensitivity → {client_message.sensitivity} on session {sid}"
                )
            elif isinstance(client_message, ScreenCommand):
                await handle_screen_command(client_message)
            elif isinstance(client_message, ClientScreenFrame):
                await handle_screen_frame(client_message)
            elif isinstance(client_message, ClientStop):
                ended_gracefully = True
                break
    except WebSocketDisconnect as exc:
        logger.info(f"Session {sid} client disconnected (code={exc.code}, reason={exc.reason!r})")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Stream receive error on {sid}: {exc}")

    if session_expired["value"]:
        ended_gracefully = True

    # ── Teardown ─────────────────────────────────────────────────────
    if capture_service is not None:
        capture_service.stop()
        capture_service = None
    if system_provider is not None:
        try:
            await system_provider.stop()
        except Exception as exc:  # noqa: BLE001  -  teardown must continue
            logger.warning(f"Other-party STT stop failed on {sid}: {exc}")
        system_provider = None
    ticker_task.cancel()
    watchdog_task.cancel()
    try:
        await provider.stop()
    except Exception as exc:  # noqa: BLE001  -  teardown must continue
        logger.warning(f"STT stop failed on {sid}: {exc}")

    if ended_gracefully:
        summary = await registry.finalize(sid)
        if summary is not None:
            emit(
                SessionEndedMessage(
                    duration_seconds=summary.duration_seconds or 0,
                    summary=summary,
                )
            )
            # A generated review is a nice-to-have. If the AI service is down,
            # send the plain measured stats instead of invented advice.
            review = None
            try:
                review = await asyncio.wait_for(
                    generate_debrief(
                        settings,
                        summary=summary,
                        outline=script.content_outline if script is not None else "",
                        keywords=list(script.keywords) if script is not None else [],
                        transcript=state.coach.analytics.context_text(300),
                        target_wpm=state.coach.target_wpm,
                    ),
                    timeout=9.0,
                )
            except Exception as exc:  # noqa: BLE001  -  timeout or generation error
                logger.warning(f"Debrief generation failed on {sid}: {exc}")
            if review is not None and review.source == "ai":
                emit(
                    DebriefMessage(
                        summary=review.summary,
                        tips=review.tips,
                        source=review.source,
                    )
                )
            else:
                emit(
                    DebriefMessage(
                        summary=(
                            f"{summary.duration_seconds or 0}s of speaking, "
                            f"{summary.total_filler_words} filler words, "
                            f"{summary.pacing_alerts} pacing alerts, "
                            f"{summary.script_misses} outline points missed. "
                            "A written review appears when the coaching service "
                            "is available."
                        ),
                        tips=[],
                        source="unavailable",
                    )
                )
        drain.set()
        try:
            await asyncio.wait_for(sender_task, timeout=3.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            sender_task.cancel()
        try:
            await websocket.close(code=1000)
        except Exception:  # noqa: BLE001
            pass
        logger.info(f"Session {sid} ended gracefully by the client")
    else:
        drain.set()
        sender_task.cancel()
        registry.schedule_finalize(sid)
        logger.info(
            f"Session {sid} socket dropped  -  holding state for "
            f"{settings.ws_reconnect_grace_seconds:.0f}s for a reconnect"
        )
