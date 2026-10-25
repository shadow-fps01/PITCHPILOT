"""Copilot-upgrade integration check: imports, config, devices.

Run from `backend/`:
    $env:PYTHONPATH='.'; .venv\\Scripts\\python scripts\\check_copilot.py
"""

import importlib


def main() -> int:
    modules = [
        "app.main",
        "app.api.stream",
        "app.api.auth",
        "app.api.scripts",
        "app.api.sessions",
        "app.services.capture",
        "app.services.vision",
        "app.services.embedding",
        "app.services.anticipation",
        "app.services.knowledge",
        "app.services.ai_coach",
        "app.services.session_registry",
        "app.models",
    ]
    for name in modules:
        importlib.import_module(name)
    print(f"IMPORTS OK ({len(modules)} modules)")

    from app.config.settings import get_settings

    settings = get_settings()
    print(
        f"config: capture_enabled={settings.audio_capture_enabled} "
        f"vision_enabled={settings.vision_enabled} "
        f"anticipation={settings.anticipation_enabled} "
        f"frame_ms={settings.capture_frame_ms}"
    )
    print(f"gemini key: {'loaded (root .env chain)' if settings.gemini_api_key else 'MISSING'}")

    from app.services.capture import capture_available, list_capture_devices

    devices = list_capture_devices()
    print(
        f"audio devices: {len(devices['microphones'])} mic(s), "
        f"{len(devices['loopbacks'])} loopback(s); native capture available="
        f"{capture_available(settings)}"
    )
    for name in devices["loopbacks"][:5]:
        print(f"  loopback: {name}")
    for name in devices["microphones"][:5]:
        print(f"  mic: {name}")

    from app.services.capture import NativeCaptureService
    from app.services.embedding import get_shared_embedding_client
    from app.services.anticipation import NextTopicAnticipator
    from app.services.vision import ScreenVisionWorker

    print("copilot services constructible:", end=" ")
    service = NativeCaptureService(settings, lambda _: None, lambda _: None)
    anticipator = NextTopicAnticipator(
        settings, None, get_shared_embedding_client(), lambda card: None
    )
    worker = ScreenVisionWorker(settings)
    assert service is not None and anticipator is not None and worker is not None
    print("OK")
    print("\nCOPILOT CHECK PASSED [OK]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
