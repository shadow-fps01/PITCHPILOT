"""List the Gemini models available to the configured key.

Run from `backend/`:
    $env:PYTHONPATH='.'; .venv\\Scripts\\python scripts\\list_gemini_models.py
"""

import asyncio

import httpx

from app.config.settings import get_settings


async def main() -> int:
    settings = get_settings()
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models"
        f"?key={settings.gemini_api_key}&pageSize=200"
    )
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(url)
        print("HTTP", response.status_code)
        if response.status_code != 200:
            print(response.text[:400])
            return 1
        for model in response.json().get("models", []):
            name = model.get("name", "").replace("models/", "")
            methods = ",".join(model.get("supportedGenerationMethods", []))
            print(f"{name:38s} {methods}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
