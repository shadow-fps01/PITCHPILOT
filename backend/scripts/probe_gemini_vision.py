"""Probe which Gemini models accept generateContent with the configured key.

Run from `backend/`:
    $env:PYTHONPATH='.'; .venv\\Scripts\\python scripts\\probe_gemini_vision.py
"""

import asyncio

import httpx

from app.config.settings import get_settings

CANDIDATES = [
    "gemini-2.5-flash",
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
    "gemini-3.5-flash",
    "gemini-3.6-flash",
    "gemini-2.5-pro",
]


async def main() -> int:
    settings = get_settings()
    key = settings.gemini_api_key
    async with httpx.AsyncClient(timeout=30.0) as client:
        for model in CANDIDATES:
            url = (
                "https://generativelanguage.googleapis.com/v1beta/models"
                f"/{model}:generateContent?key={key}"
            )
            body = {
                "contents": [{"parts": [{"text": "Reply with exactly: ok"}]}],
                "generationConfig": {"maxOutputTokens": 10},
            }
            try:
                response = await client.post(url, json=body)
            except Exception as exc:  # noqa: BLE001
                print(f"{model:28s} NETWORK ERROR {exc}")
                continue
            if response.status_code == 200:
                print(f"{model:28s} OK")
            else:
                detail = ""
                try:
                    detail = response.json()["error"]["message"][:110]
                except Exception:  # noqa: BLE001
                    detail = response.text[:110]
                print(f"{model:28s} HTTP {response.status_code} {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
