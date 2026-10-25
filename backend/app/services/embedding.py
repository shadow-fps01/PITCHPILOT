"""Gemini text embeddings for the RAG knowledge base.

Used to embed knowledge cards at write time and live transcript windows at
query time. Fails soft: any error returns `None` and the anticipator falls
back to keyword search, so a missing/invalid key never breaks the stream.
"""

import abc

import httpx
from loguru import logger

from app.config.settings import Settings, get_settings

_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
_MAX_TEXT_CHARS = 6000
_MAX_CONSECUTIVE_FAILURES = 3


class EmbeddingClient(abc.ABC):
    """Interface for embedding backends."""

    @abc.abstractmethod
    async def embed(self, text: str) -> list[float] | None:
        """Return an embedding vector, or None when unavailable."""


class GeminiEmbeddingClient(EmbeddingClient):
    def __init__(self, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model
        self._failures = 0
        self._disabled = False

    async def embed(self, text: str) -> list[float] | None:
        if self._disabled:
            return None
        url = f"{_GEMINI_BASE}/models/{self._model}:embedContent?key={self._api_key}"
        body = {
            "model": f"models/{self._model}",
            "content": {"parts": [{"text": text[:_MAX_TEXT_CHARS]}]},
            # 768 keeps our pgvector Vector(768) column valid (MRL-style
            # truncation supported by gemini-embedding-* models).
            "outputDimensionality": 768,
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(url, json=body)
                response.raise_for_status()
                values = response.json()["embedding"]["values"]
                self._failures = 0
                return [float(value) for value in values]
        except Exception as exc:  # noqa: BLE001 — embeddings must never break the app
            self._failures += 1
            if self._failures >= _MAX_CONSECUTIVE_FAILURES:
                self._disabled = True
                logger.warning(f"Gemini embeddings disabled after repeated failures: {exc}")
            return None


def get_embedding_client(settings: Settings) -> EmbeddingClient | None:
    """Return the configured embedding client, or None when no key exists."""
    if settings.gemini_api_key:
        return GeminiEmbeddingClient(settings.gemini_api_key, settings.embedding_model)
    return None


# ── App-wide shared instance (circuit-breaker state is global) ──────────
_shared_client: EmbeddingClient | None = None
_shared_initialized: bool = False


def get_shared_embedding_client() -> EmbeddingClient | None:
    """Lazily create the single shared embedding client for the process."""
    global _shared_client, _shared_initialized
    if not _shared_initialized:
        _shared_client = get_embedding_client(get_settings())
        _shared_initialized = True
    return _shared_client
