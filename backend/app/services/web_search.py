"""Live web search for the copilot's suggestions and answers.

Tavily's search API is used for two things:

1. **Proactive suggestions**  -  when the call reaches a factual moment
   (competitor, pricing, market, security, integration ...) the copilot can
   ground its suggestion in a short, fresh web snippet.
2. **Typed questions**  -  any question the presenter types is searched first
   so the answer reflects what is on the web now, not just the transcript.

Design constraints:

- Search is optional at runtime. If no key is configured, callers get
  ``SearchUnavailable`` and must show an explicit error rather than invent
  facts.
- Results are trimmed hard (title, URL, ~480-char snippet) to keep the
  prompt small and the latency inside the live-call budget.
- One outcome per query: ``SearchOutcome(results=[...], error=...)``. An
  empty result list is a valid answer ("nothing relevant found").
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field

import httpx
from loguru import logger

from app.config.settings import Settings

TAVILY_URL = "https://api.tavily.com/search"
_SEARCH_TIMEOUT_SECONDS = 7.0
_SNIPPET_CHARS = 480
_MAX_RESULTS = 4

_FACTUAL_RE = re.compile(
    r"\b(competitor|competitors|alternative|pricing|price|discount|cost|"
    r"market|competition|industry|benchmark|latest|news|funding|raised|"
    r"valuation|revenue|growth|traction|churn|retention|gdpr|soc.?2|"
    r"compliance|security|integration|integrations|crm|api|"
    r"how (?:much|many)|what(?:'s| is) the (?:price|cost)|"
    r"who (?:else|are|is)|do you (?:integrate|support)|"
    r"case study|roi|conversion)\b",
    re.IGNORECASE,
)


class SearchUnavailable(Exception):
    """Tavily is not configured or returned an error; surface it, don't fake it."""


@dataclass(frozen=True, slots=True)
class SearchResult:
    title: str
    url: str
    snippet: str


@dataclass(frozen=True, slots=True)
class SearchOutcome:
    results: list[SearchResult] = field(default_factory=list)
    error: str | None = None


def is_factual_moment(text: str) -> bool:
    """True when a transcript/answer moment likely needs current web facts."""
    return bool(_FACTUAL_RE.search(text))


def _search_query(text: str) -> str:
    """Trim a spoken/typed snippet into a usable search query."""
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'&+.\-]*", text)
    return " ".join(words[:24])


class TavilySearch:
    """Thin Tavily client with a hard timeout and a small result cap."""

    def __init__(self, settings: Settings) -> None:
        self._key = settings.tavily_api_key.strip()
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(_SEARCH_TIMEOUT_SECONDS, connect=4.0)
        )

    @property
    def configured(self) -> bool:
        return bool(self._key)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def search(
        self, query: str, *, max_results: int = _MAX_RESULTS
    ) -> SearchOutcome:
        """Run one search. Never raises; errors come back on the outcome."""
        if not self._key:
            return SearchOutcome(error="Search is not configured")
        query = _search_query(query)
        if not query:
            return SearchOutcome()
        payload = {
            "query": query,
            "search_depth": "basic",
            "max_results": max(1, min(max_results, _MAX_RESULTS)),
            "include_answer": False,
            "include_raw_content": False,
        }
        headers = {"Authorization": f"Bearer {self._key}"}
        try:
            response = await self._client.post(
                TAVILY_URL, headers=headers, json=payload
            )
            response.raise_for_status()
            body = response.json()
        except Exception as exc:  # noqa: BLE001 - search must never break a call
            logger.warning(f"Web search failed for {query!r}: {exc}")
            return SearchOutcome(error="Web search is unavailable right now")

        results: list[SearchResult] = []
        for item in body.get("results") or []:
            url = str(item.get("url") or "").strip()
            title = str(item.get("title") or "").strip()
            snippet = " ".join(str(item.get("content") or "").split())
            if not url or not snippet:
                continue
            results.append(
                SearchResult(
                    title=title or url,
                    url=url,
                    snippet=snippet[:_SNIPPET_CHARS],
                )
            )
            if len(results) >= _MAX_RESULTS:
                break
        return SearchOutcome(results=results)


def build_search(settings: Settings) -> TavilySearch | None:
    """Return a search client when configured, else None (explicit errors)."""
    return TavilySearch(settings) if settings.tavily_api_key.strip() else None

