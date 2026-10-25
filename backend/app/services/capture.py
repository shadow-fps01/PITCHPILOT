"""Native dual audio capture (copilot upgrade F1).

Background capture service with native system bindings (`soundcard` over
WASAPI/PortAudio — the maintained equivalent of the PyAudio approach).
Simultaneously captures and mixes two channels:

- **System audio (loopback)** — the incoming remote voice in calls,
- **Microphone** — the outgoing local voice,

mixes them into one mono stream, resamples to 16 kHz 16-bit PCM, and emits
~50 ms frames straight into the persistent STT WebSocket provider — no
browser involvement, comfortably within the sub-200 ms chunking budget.

Runs entirely on daemon threads with an asyncio hand-off; capture failures
degrade gracefully to mic-only (or an explanatory error), never to a crash.
"""

import threading
import time
from collections.abc import Callable

import numpy as np
from loguru import logger

from app.config.settings import Settings


class CaptureError(Exception):
    """Raised when native capture cannot start (e.g. no devices)."""


def _soundcard():
    """Lazy import so a machine without an audio subsystem never breaks startup."""
    import soundcard as sc

    return sc


def list_capture_devices() -> dict[str, list[str]]:
    """Enumerate microphone + system-loopback device names (for diagnostics)."""
    try:
        microphones = [
            m.name for m in _soundcard().all_microphones(include_loopback=False)
        ]
    except Exception:  # noqa: BLE001
        microphones = []
    try:
        loopbacks = [
            m.name
            for m in _soundcard().all_microphones(include_loopback=True)
            if getattr(m, "isloopback", False)
        ]
    except Exception:  # noqa: BLE001
        loopbacks = []
    return {"microphones": microphones, "loopbacks": loopbacks}


def capture_available(settings: Settings) -> bool:
    """True when native capture is enabled and at least one mic exists."""
    if not settings.audio_capture_enabled:
        return False
    try:
        return _soundcard().default_microphone() is not None
    except Exception:  # noqa: BLE001
        return len(list_capture_devices()["microphones"]) > 0


def _pick_microphone(name_substring: str):
    sc = _soundcard()
    if name_substring:
        for mic in sc.all_microphones(include_loopback=False):
            if name_substring.lower() in mic.name.lower():
                return mic
        logger.warning(f"No microphone matching {name_substring!r}; using default")
    return sc.default_microphone()


def _pick_loopback(name_substring: str):
    sc = _soundcard()
    loopbacks = [
        m
        for m in sc.all_microphones(include_loopback=True)
        if getattr(m, "isloopback", False)
    ]
    if name_substring:
        for loopback in loopbacks:
            if name_substring.lower() in loopback.name.lower():
                return loopback
        logger.warning(
            f"No loopback matching {name_substring!r}; using first available"
        )
    return loopbacks[0] if loopbacks else None


def _resample(frame: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    """Linear-interpolation resample of a 1-D float array."""
    if source_rate == target_rate or frame.size == 0:
        return frame
    output_length = int(frame.size * target_rate / source_rate)
    positions = np.linspace(0.0, frame.size - 1.0, output_length)
    return np.interp(positions, np.arange(frame.size), frame)


class NativeCaptureService:
    """Thread-based dual capture; emits mixed 16 kHz PCM frames + RMS level."""

    def __init__(
        self,
        settings: Settings,
        on_frame: Callable[[bytes], None],
        on_level: Callable[[float], None],
    ) -> None:
        self._settings = settings
        self._on_frame = on_frame
        self._on_level = on_level

        self._running = threading.Event()
        self._latest: dict[str, tuple[float, np.ndarray]] = {}
        self._latest_lock = threading.Lock()
        self._threads: list[threading.Thread] = []

        self.mic_name: str | None = None
        self.loopback_name: str | None = None
        self.mode: str = "off"

    # ── Lifecycle ─────────────────────────────────────────────────────
    def start(self) -> None:
        if self._running.is_set():
            return

        microphone = _pick_microphone(self._settings.capture_mic_name)
        if microphone is None:
            raise CaptureError("No microphone device found")
        loopback = _pick_loopback(self._settings.capture_loopback_name)

        self.mic_name = microphone.name
        self.loopback_name = loopback.name if loopback is not None else None
        self.mode = "dual" if loopback is not None else "mic-only"
        self._running.set()

        targets = [("mic", microphone)]
        if loopback is not None:
            targets.append(("loopback", loopback))

        for tag, device in targets:
            thread = threading.Thread(
                target=self._device_loop,
                args=(tag, device),
                name=f"pitchpilot-capture-{tag}",
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)

        mixer = threading.Thread(
            target=self._mixer_loop, name="pitchpilot-capture-mixer", daemon=True
        )
        mixer.start()
        self._threads.append(mixer)

        logger.info(
            f"Native capture started ({self.mode}): mic={self.mic_name!r}, "
            f"loopback={self.loopback_name!r}"
        )

    def stop(self) -> None:
        self._running.clear()
        for thread in self._threads:
            if thread.is_alive():
                thread.join(timeout=1.5)
        self._threads.clear()
        with self._latest_lock:
            self._latest.clear()
        self.mode = "off"
        logger.info("Native capture stopped")

    # ── Device + mixer threads ────────────────────────────────────────
    def _device_loop(self, tag: str, device) -> None:
        """Pull frames from one capture device into the shared latest-slot."""
        blocksize = int(48000 * self._settings.capture_frame_ms / 1000)
        try:
            with device.recorder(samplerate=48000, channels=1, blocksize=blocksize) as recorder:
                while self._running.is_set():
                    frame = recorder.record(numframes=blocksize)
                    if frame.ndim > 1:
                        frame = frame.mean(axis=1)
                    with self._latest_lock:
                        self._latest[tag] = (time.monotonic(), frame.astype(np.float32))
        except Exception as exc:  # noqa: BLE001 — device death logs and exits the thread
            if self._running.is_set():
                logger.warning(f"Capture device {tag!r} loop ended: {exc}")

    def _mixer_loop(self) -> None:
        """Mix latest mic + loopback frames → 16 kHz mono PCM @ ~frame cadence."""
        frame_seconds = self._settings.capture_frame_ms / 1000.0
        target_rate = self._settings.capture_target_rate
        while self._running.is_set():
            time.sleep(frame_seconds)
            with self._latest_lock:
                snapshots = {
                    tag: frame for tag, (_, frame) in list(self._latest.items())
                }
            if not snapshots:
                continue

            mixed: np.ndarray | None = None
            for frame in snapshots.values():
                resampled = _resample(frame, 48000, target_rate)
                if mixed is None:
                    mixed = resampled
                else:
                    length = min(mixed.size, resampled.size)
                    mixed = (
                        0.5 * mixed[:length] + 0.5 * resampled[:length]
                    )
            if mixed is None or mixed.size == 0:
                continue

            rms = float(np.sqrt(np.mean(np.square(mixed))))
            self._on_level(min(rms, 1.0))
            clipped = np.clip(mixed, -1.0, 1.0)
            pcm = (clipped * 32767.0).astype(np.int16).tobytes()
            self._on_frame(pcm)
