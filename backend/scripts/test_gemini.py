"""Live check: does the configured Gemini key work for embeddings (F3)?

Run from `backend/`:
    $env:PYTHONPATH='.'; .venv\\Scripts\\python scripts\\test_gemini.py
"""

import asyncio


async def main() -> int:
    from app.config.settings import get_settings
    from app.services.embedding import get_shared_embedding_client

    settings = get_settings()
    print(f"embedding model: {settings.embedding_model}")
    print(f"key (masked)  : {settings.gemini_api_key[:8]}...{settings.gemini_api_key[-4:]}")

    client = get_shared_embedding_client()
    if client is None:
        print("No key configured — anticipation will use keyword search.")
        return 2

    vector = await client.embed("pricing, discounts and the fundraising ask")
    if vector is None:
        print("EMBEDDING FAILED — key rejected or network error.")
        print("The app still works: anticipation falls back to keyword search.")
        return 1

    print(f"EMBEDDING OK — {len(vector)} dims, first 3: {vector[:3]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
