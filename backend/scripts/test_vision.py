"""Live single-shot test of the screen-vision worker (F5).

Grabs one real screenshot and runs it through every configured vision
provider in the chain:
    gemini (VISION_MODEL) → gemini-lite (VISION_FALLBACK_MODEL)
    → azure (gpt-4o) → nvidia (NIM vision model)

then runs the full chain exactly as the worker would and prints the winner.
Prompts come from settings (VISION_PROMPT / VISION_ASK_PROMPT) — nothing is
hardcoded here.

Run from `backend/`:
    $env:PYTHONPATH='.'; .venv\\Scripts\\python scripts\\test_vision.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from app.config.settings import get_settings  # noqa: E402
from app.services.dns_cache import install as _install_dns_cache  # noqa: E402
from app.services.vision import (  # noqa: E402
    ScreenVisionWorker,
    _grab_frame,
    resolve_vision_providers,
)

_install_dns_cache()


async def probe(worker: ScreenVisionWorker, name: str, jpeg: bytes) -> None:
    """Run one provider directly and print the outcome."""
    settings = worker._settings
    try:
        if name == "gemini":
            text = await worker._describe_gemini(settings.vision_model, jpeg, settings.vision_prompt, 120)
        elif name == "gemini-lite":
            text = await worker._describe_gemini(
                settings.vision_fallback_model, jpeg, settings.vision_prompt, 120
            )
        elif name == "azure":
            text = await worker._describe_azure(jpeg, settings.vision_prompt, 120)
        else:
            text = await worker._describe_nvidia(jpeg, settings.vision_prompt, 120)
    except Exception as exc:  # noqa: BLE001
        print(f"  [{name:12}] FAIL: {exc}")
        return
    if text:
        print(f"  [{name:12}] OK: {text[:110]}")
    else:
        print(f"  [{name:12}] EMPTY (200 but no usable text)")


async def main() -> int:
    settings = get_settings()
    providers = resolve_vision_providers(settings)
    if not providers:
        print(
            "No vision provider configured — set GEMINI_API_KEY, "
            "AZURE_OPENAI_* or NVIDIA_API_KEY."
        )
        return 2

    print("grabbing a screenshot…")
    grabbed = await asyncio.to_thread(_grab_frame)
    if grabbed is None:
        print("Screen grab failed (mss could not capture the display).")
        return 1
    jpeg, frame_hash = grabbed
    print(f"frame: {len(jpeg):,} bytes JPEG, aHash={frame_hash:016x}")
    print(f"chain : {' -> '.join(providers)}\n")

    worker = ScreenVisionWorker(settings)
    worker._client = httpx.AsyncClient(timeout=httpx.Timeout(40.0, connect=10.0))
    try:
        for name in providers:
            await probe(worker, name, jpeg)
        print("\nfull chain (as the worker runs it):")
        summary = await worker._describe(jpeg)
        if summary is None:
            print("  chain produced no usable context — every provider failed")
            return 1
        print(f"  context: {summary}")
        print("\nVISION TEST PASSED [OK]")
        return 0
    finally:
        await worker._client.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
