"""LLM clients powering the Contextual Script Auditor.

The auditor periodically reviews the recent transcript against the user's
outline/keywords and reports which talking points are still missing. Three
backends are supported:

- Azure OpenAI (chat completions)
- OpenRouter (OpenAI-compatible API)
- A local heuristic auditor (keyword matching) that always works, no keys needed.
"""

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import httpx
from loguru import logger

from app.config.settings import Settings

_AUDIT_TIMEOUT_SECONDS = 15.0

_SYSTEM_PROMPT = (
    "You are PitchPilot, a live public-speaking coach listening to an ongoing "
    "pitch or presentation. You are given the speaker's outline/key talking "
    "points and the most recent words they said. Identify which talking "
    "points from the outline the speaker has NOT yet covered and should "
    "mention soon, and which ones they have already covered.\n"
    "Reply with ONLY a JSON object, no prose, in this exact shape:\n"
    '{"covered": ["point", ...], "missed": ["point", ...], '
    '"cue": "short imperative coaching cue under 12 words, or null"}\n'
    "Rules: `covered` and `missed` must contain short paraphrases of the "
    "outline talking points (max 6 items each). If the speaker has covered "
    "everything or there is not enough speech yet, return empty lists and "
    'cue=null. Never invent talking points that are not implied by the '
    "outline or keywords."
)


@dataclass(frozen=True)
class ScriptAuditResult:
    covered: list[str] = field(default_factory=list)
    missed: list[str] = field(default_factory=list)
    cue: str | None = None


@dataclass(frozen=True)
class ScriptAuditRequest:
    outline: str
    keywords: list[str]
    recent_transcript: str
    already_covered: set[str] = field(default_factory=set)
    screen_context: str | None = None


class LLMClient(ABC):
    """Interface for script-auditor backends."""

    @abstractmethod
    async def audit_transcript(self, request: ScriptAuditRequest) -> ScriptAuditResult:
        """Analyse the recent transcript against the outline; never raises."""


def _build_user_prompt(request: ScriptAuditRequest) -> str:
    covered_list = ", ".join(sorted(request.already_covered)) or "(none yet)"
    screen_section = (
        f"SCREEN CONTEXT (live vision worker summary):\n\"{request.screen_context}\"\n\n"
        if request.screen_context
        else ""
    )
    return (
        f"OUTLINE / TALKING POINTS:\n{request.outline or '(no outline provided)'}\n\n"
        f"KEYWORDS: {', '.join(request.keywords) or '(none)'}\n\n"
        f"ALREADY CONFIRMED COVERED (previous audits): {covered_list}\n\n"
        f"{screen_section}"
        f"RECENT TRANSCRIPT (latest words last):\n\"{request.recent_transcript}\"\n\n"
        "Return the JSON object now."
    )


def _parse_audit_json(raw: str) -> ScriptAuditResult:
    """Robustly extract the audit JSON from an LLM response."""
    text = raw.strip()
    if text.startswith("```"):
        # Strip a markdown code fence: ```json ... ``` or ``` ... ```
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("No JSON object found in LLM response")
    payload = json.loads(text[start : end + 1])
    covered = [str(p) for p in payload.get("covered", [])][:6]
    missed = [str(p) for p in payload.get("missed", [])][:6]
    cue = payload.get("cue")
    if not isinstance(cue, str) or not cue.strip():
        cue = None
    return ScriptAuditResult(covered=covered, missed=missed, cue=cue)


class AzureOpenAIClient(LLMClient):
    """Chat-completions client for Azure OpenAI resources."""

    def __init__(self, api_key: str, endpoint: str, deployment: str, api_version: str) -> None:
        self._url = (
            f"{endpoint.rstrip('/')}/openai/deployments/{deployment}/chat/completions"
        )
        self._headers = {"api-key": api_key, "Content-Type": "application/json"}
        self._params = {"api-version": api_version}

    async def audit_transcript(self, request: ScriptAuditRequest) -> ScriptAuditResult:
        body = {
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(request)},
            ],
            "temperature": 0.2,
            "max_tokens": 400,
            "response_format": {"type": "json_object"},
        }
        try:
            async with httpx.AsyncClient(timeout=_AUDIT_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    self._url, headers=self._headers, params=self._params, json=body
                )
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                return _parse_audit_json(content)
        except Exception as exc:  # noqa: BLE001 — auditor must never break the stream
            logger.warning(f"Azure script audit failed: {exc}")
            return ScriptAuditResult()


class OpenRouterClient(LLMClient):
    """OpenAI-compatible client for OpenRouter."""

    def __init__(self, api_key: str, model: str) -> None:
        self._url = "https://openrouter.ai/api/v1/chat/completions"
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self._model = model

    async def audit_transcript(self, request: ScriptAuditRequest) -> ScriptAuditResult:
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(request)},
            ],
            "temperature": 0.2,
            "max_tokens": 400,
            "response_format": {"type": "json_object"},
        }
        try:
            async with httpx.AsyncClient(timeout=_AUDIT_TIMEOUT_SECONDS) as client:
                response = await client.post(self._url, headers=self._headers, json=body)
                response.raise_for_status()
                content = response.json()["choices"][0]["message"]["content"]
                return _parse_audit_json(content)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"OpenRouter script audit failed: {exc}")
            return ScriptAuditResult()


class HeuristicAuditor(LLMClient):
    """Keyless fallback auditor: tracks keyword mentions across the session."""

    def __init__(self) -> None:
        import re

        self._keyword_patterns: dict[str, re.Pattern[str]] = {}

    async def audit_transcript(self, request: ScriptAuditRequest) -> ScriptAuditResult:
        import re

        covered = set(request.already_covered)
        missed: list[str] = []
        for keyword in request.keywords:
            if keyword not in self._keyword_patterns:
                self._keyword_patterns[keyword] = re.compile(
                    rf"\b{re.escape(keyword.strip())}\b", re.IGNORECASE
                )
            if self._keyword_patterns[keyword].search(request.recent_transcript):
                covered.add(keyword)
            elif keyword not in covered:
                missed.append(keyword)
        cue: str | None = None
        if missed:
            cue = f"Don't forget to mention: {', '.join(missed[:3])}"
        return ScriptAuditResult(covered=sorted(covered), missed=missed[:6], cue=cue)


def get_llm_client(settings: Settings) -> LLMClient:
    """Pick the auditor backend from settings, with graceful auto-detection."""
    provider = settings.llm_provider.lower()
    if provider == "azure" or (
        provider == "auto" and settings.azure_openai_api_key and settings.azure_openai_endpoint
    ):
        return AzureOpenAIClient(
            api_key=settings.azure_openai_api_key,
            endpoint=settings.azure_openai_endpoint,
            deployment=settings.azure_openai_chat_deployment,
            api_version=settings.azure_openai_api_version,
        )
    if provider == "openrouter" or (provider == "auto" and settings.openrouter_api_key):
        return OpenRouterClient(
            api_key=settings.openrouter_api_key, model=settings.openrouter_model
        )
    return HeuristicAuditor()
