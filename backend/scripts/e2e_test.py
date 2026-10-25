"""Full end-to-end test against a running backend (Supabase-backed).

Mock mode (default) — the server's mock STT speaks a simulated pitch:
    $env:PYTHONPATH='.'; .venv\\Scripts\\python scripts\\e2e_test.py

Real-audio mode — streams a 16 kHz/16-bit/mono WAV through the live
WebSocket at real-time pace (use with STT_PROVIDER=auto + a Groq key, i.e.
real transcription):
    $env:PYTHONPATH='.'; .venv\\Scripts\\python scripts\\e2e_test.py --audio <path.wav>

Both modes verify: auth -> script -> session -> WS stream -> cues persisted
to the database -> graceful end with session_ended.
"""

import asyncio
import json
import sys
import wave

import httpx
import websockets
from websockets.exceptions import ConnectionClosed

BASE = "http://127.0.0.1:8000/api"
WS_BASE = "ws://127.0.0.1:8000/api"
EMAIL = "pitchpilot.e2e@gmail.com"
PASSWORD = "demodemo123"
# Mock mode: the simulated speaker needs ~20s before its filler count reaches
# the burst threshold (>=3) and its rolling window clears the pacing gate
# (>=12 words) — 10s was too short to ever fire a cue.
LISTEN_SECONDS = 22.0
FRAME_BYTES = 3200  # 100 ms of 16 kHz 16-bit mono
# Marker word unique to the synthetic test WAV (not in the mock corpus) and
# spoken in the FIRST seconds, so it survives the stop-time tail cutoff.
REAL_AUDIO_MARKER = "welcome"


def load_wav_pcm(path: str) -> bytes:
    with wave.open(path, "rb") as wav:
        if (
            wav.getframerate() != 16000
            or wav.getnchannels() != 1
            or wav.getsampwidth() != 2
        ):
            raise SystemExit("WAV must be 16 kHz / 16-bit / mono")
        return wav.readframes(wav.getnframes())


async def main() -> int:
    audio_path: str | None = None
    if "--audio" in sys.argv:
        index = sys.argv.index("--audio")
        if index + 1 >= len(sys.argv):
            raise SystemExit("Usage: e2e_test.py [--audio <wav path>]")
        audio_path = sys.argv[index + 1]
    real_audio = audio_path is not None
    pcm = load_wav_pcm(audio_path) if audio_path is not None else b""
    # +8s after the audio ends: lets the final Groq segments flush through
    # before the stop message (tail latency ≈ segment + HTTP round-trip).
    listen_seconds = (
        min(len(pcm) / 32000 + 8.0, 60.0) if real_audio else LISTEN_SECONDS
    )

    counts: dict[str, int] = {}
    cue_messages: list[str] = []
    context_cards: list[str] = []
    transcripts: list[str] = []
    session_ended: dict[str, object] | None = None

    async with httpx.AsyncClient(base_url=BASE, timeout=30.0) as api:
        # ── auth ── (retry: this machine's resolver sometimes blips on the
        # first request after a server restart)
        r = await api.post("/auth/register", json={"email": EMAIL, "password": PASSWORD})
        if r.status_code == 409:
            r = await api.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
        for attempt in range(3):
            if r.status_code < 500:
                break
            await asyncio.sleep(3)
            r = await api.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
        r.raise_for_status()
        token: str = r.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        print(f"[auth] OK (user {r.json()['user']['email']})")

        # ── consent ──
        r = await api.post("/auth/consent", headers=headers)
        r.raise_for_status()
        print("[consent] OK")

        # ── script ──
        r = await api.post(
            "/scripts",
            json={
                "title": "E2E demo pitch",
                "content_outline": "1. Problem\n2. Solution\n3. Pricing\n4. The ask",
                "target_wpm": 140,
                "keywords": ["pricing", "discount", "the ask"],
            },
            headers=headers,
        )
        r.raise_for_status()
        script_id: str = r.json()["id"]
        print(f"[script] OK ({script_id})")

        # ── session ──
        r = await api.post("/sessions", json={"script_id": script_id}, headers=headers)
        r.raise_for_status()
        session_id: str = r.json()["id"]
        print(f"[session] OK ({session_id})")

        # ── short-lived WS ticket (the JWT never goes in the URL) ──
        r = await api.post("/auth/ws-ticket", headers=headers)
        r.raise_for_status()
        ticket: str = r.json()["ticket"]

        # ── live WS stream ──
        uri = f"{WS_BASE}/ws/stream/{session_id}?ticket={ticket}"
        loop = asyncio.get_event_loop()
        t0 = loop.time()
        deadline = t0 + listen_seconds
        stopped = False

        async with websockets.connect(uri, max_size=2**22) as ws:
            if not real_audio:
                # A binary frame of silence to exercise the audio path.
                await ws.send(b"\x00\x00" * 1600)
                print(f"[ws {loop.time() - t0:6.2f}s] connected, sent silence frame")
            else:
                print(
                    f"[ws {loop.time() - t0:6.2f}s] connected, streaming "
                    f"{len(pcm) / 32000:.1f}s of real audio at real-time pace"
                )

            async def feeder() -> None:
                """Stream WAV frames every 100 ms (like the browser worklet)."""
                for offset in range(0, len(pcm), FRAME_BYTES):
                    if stopped:
                        return
                    try:
                        await ws.send(pcm[offset : offset + FRAME_BYTES])
                    except ConnectionClosed:
                        return
                    await asyncio.sleep(0.1)

            async def pump() -> None:
                nonlocal stopped, session_ended
                while True:
                    remaining = deadline - loop.time()
                    timeout = remaining if remaining > 0 else 20.0
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                    except ConnectionClosed as exc:
                        print(
                            f"[ws {loop.time() - t0:6.2f}s] CLOSED BY PEER "
                            f"code={exc.code} reason={exc.reason!r}"
                        )
                        return
                    except asyncio.TimeoutError:
                        if not stopped:
                            # Listen window elapsed — end the session gracefully.
                            stopped = True
                            print(f"[ws {loop.time() - t0:6.2f}s] listen window over, sending stop")
                            try:
                                await ws.send(json.dumps({"type": "stop"}))
                                continue
                            except ConnectionClosed as exc:
                                print(f"[ws {loop.time() - t0:6.2f}s] stop send failed: {exc.code}")
                                return
                        print(f"[ws {loop.time() - t0:6.2f}s] no session_ended after stop")
                        return
                    message: dict = json.loads(raw)
                    kind = message.get("type", "?")
                    counts[kind] = counts.get(kind, 0) + 1
                    if kind == "cue":
                        cue_messages.append(f"[{message['cue_type']}] {message['message']}")
                        print(f"[ws {loop.time() - t0:6.2f}s] cue: {cue_messages[-1]}")
                    elif kind == "context_card":
                        context_cards.append(
                            f"{message['title']} (score={message['score']})"
                        )
                        print(
                            f"[ws {loop.time() - t0:6.2f}s] context card: {context_cards[-1]}"
                        )
                    elif kind == "metrics":
                        print(
                            f"[ws {loop.time() - t0:6.2f}s] metrics: wpm={message['wpm']} "
                            f"fillers={message['filler_word_count']} "
                            f"elapsed={message['elapsed_seconds']}s"
                        )
                    elif kind == "transcript" and message.get("is_final"):
                        transcripts.append(str(message["text"]))
                    elif kind == "session_ended":
                        session_ended = message
                        print(f"[ws {loop.time() - t0:6.2f}s] session_ended received")
                        return
                    elif kind == "error":
                        print(f"[ws {loop.time() - t0:6.2f}s] server error: {message['message']}")

            tasks: list[asyncio.Task[None]] = [
                asyncio.create_task(pump(), name="pump"),
            ]
            if real_audio:
                tasks.append(asyncio.create_task(feeder(), name="feeder"))
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks),
                    timeout=listen_seconds + 30.0,
                )
            except asyncio.TimeoutError:
                print("[ws] ERROR: pump still running at hard timeout — connection wedged")

        print(f"[ws] message counts: {counts}")
        for transcript in transcripts:
            print(f"  transcript: {transcript}")
        for card in context_cards:
            print(f"  card: {card}")
        for cue in cue_messages:
            print(f"  cue: {cue}")

        # ── verify persistence ──
        r = await api.get(f"/sessions/{session_id}", headers=headers)
        r.raise_for_status()
        detail = r.json()
        summary = detail["summary"]
        print(
            f"[report] ended_at={detail['ended_at']} "
            f"duration={summary['duration_seconds']}s "
            f"fillers={summary['total_filler_words']} "
            f"pacing={summary['pacing_alerts']} "
            f"misses={summary['script_misses']} "
            f"events={summary['event_count']}"
        )

        assert session_ended is not None, "expected a session_ended message"
        assert counts.get("session_started", 0) >= 1, "no session_started"
        if real_audio:
            assert len(transcripts) >= 2, "expected multiple Groq final transcripts"
            joined = " ".join(transcripts).lower()
            assert REAL_AUDIO_MARKER in joined, (
                "transcript does not contain the real-audio marker — is the mock still active?"
            )
            assert counts.get("cue", 0) >= 1, "no cues fired for real speech"
        else:
            assert counts.get("transcript", 0) > 5, "mock STT sent no transcripts"
            assert counts.get("metrics", 0) >= 2, "no metrics streamed"
            assert counts.get("cue", 0) >= 1, "no cues fired in the live stream"
        assert counts.get("metrics", 0) >= 2, "no metrics streamed"
        # Cues AND pre-fetched context cards are both persisted as events.
        persisted_total = counts.get("cue", 0) + counts.get("context_card", 0)
        assert summary["event_count"] == persisted_total, (
            "persisted event count does not match cues + cards streamed"
        )
        assert (summary["duration_seconds"] or 0) >= 8, "duration too short"
        if real_audio:
            assert counts.get("context_card", 0) >= 1, (
                "no RAG context card surfaced for the trigger speech"
            )
        print("\nE2E TEST PASSED [OK]")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
