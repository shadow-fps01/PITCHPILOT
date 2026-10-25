"""Application settings loaded from environment variables via Pydantic Settings."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed application configuration.

    Values are read from the environment (and a local `.env` file when the
    backend is started from the `backend/` directory). Values that are JSON
    lists in code (e.g. `FILLER_WORDS`) must be written as JSON in the env.
    """

    model_config = SettingsConfigDict(
        # The workspace-root .env is the user's single source of truth for API
        # keys (GROQ_API_KEY, AZURE_*, OPENROUTER_*, JWT_SECRET_KEY, …);
        # backend/.env holds app-specific config and overrides the root file.
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Core ──────────────────────────────────────────────────────────
    app_name: str = "PitchPilot"
    app_version: str = "1.0.0"
    api_prefix: str = "/api"
    debug: bool = False

    # ── Security ──────────────────────────────────────────────────────
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expires_minutes: int = 60 * 24 * 7  # one week
    # Password-reset links are single-use (the token carries the user's
    # token_version, which is bumped after a successful reset).
    password_reset_token_minutes: int = 30

    # ── Signups / recording consent ───────────────────────────────────
    # A private pilot can close public registration or restrict it to an
    # allowlist. Both are enforced server-side, not just hidden in the UI.
    signups_enabled: bool = True
    signup_allowlist: list[str] = []
    # Recording and sending audio to a transcription provider requires the
    # presenter to confirm they have permission from everyone on the call.
    require_recording_consent: bool = True
    consent_version: str = "2026-10-24"

    # ── Database ──────────────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/pitchpilot"
    db_pool_size: int = 5
    db_max_overflow: int = 10
    auto_create_tables: bool = True
    dns_cache_enabled: bool = True   # in-process getaddrinfo cache + stale-on-error

    # ── CORS ──────────────────────────────────────────────────────────
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # ── Speech-to-text ────────────────────────────────────────────────
    stt_provider: str = "auto"  # auto | azure | deepgram | openai | groq | mock
    deepgram_api_key: str = ""
    openai_api_key: str = ""
    # Groq (OpenAI-compatible batch transcriptions, emulated near-real-time)
    groq_api_key: str = ""
    groq_stt_model: str = "whisper-large-v3-turbo"
    groq_stt_segment_seconds: float = 3.0
    # Azure OpenAI STT reuses AZURE_OPENAI_API_KEY / AZURE_OPENAI_ENDPOINT.
    # The deployment is the name you gave the audio model in Azure, not the
    # model name itself. Empty deployment = Azure STT is not selected by auto.
    azure_openai_stt_deployment: str = ""
    azure_openai_stt_api_version: str = "2024-06-01"
    azure_openai_stt_segment_seconds: float = 3.0

    # ── Live coaching analytics ───────────────────────────────────────
    wpm_window_seconds: float = 5.0
    target_wpm_default: int = 140
    filler_words: list[str] = ["um", "uh", "like", "so", "you know", "basically", "actually", "literally"]
    filler_burst_window_seconds: float = 15.0
    filler_burst_threshold: int = 3
    pacing_tick_seconds: float = 3.0

    # ── Script auditor (LLM) ──────────────────────────────────────────
    llm_provider: str = "auto"  # auto | azure | openrouter | heuristic
    script_audit_word_interval: int = 45
    script_audit_min_gap_seconds: float = 30.0
    script_audit_context_words: int = 120
    azure_openai_api_key: str = ""
    azure_openai_endpoint: str = ""
    azure_openai_chat_deployment: str = "gpt-4o"
    azure_openai_api_version: str = "2024-12-01-preview"
    openrouter_api_key: str = ""
    openrouter_model: str = "openai/gpt-4o-mini"

    # ── WebSocket resilience ──────────────────────────────────────────
    ws_reconnect_grace_seconds: float = 30.0
    heartbeat_timeout_seconds: float = 40.0

    # ── Native dual audio capture (copilot upgrade F1) ────────────────
    audio_capture_enabled: bool = False
    capture_mic_name: str = ""        # substring match; empty = default mic
    capture_loopback_name: str = ""   # substring match; empty = first loopback
    capture_frame_ms: int = 50        # sub-200 ms chunking per spec
    capture_target_rate: int = 16000

    # ── Next-topic anticipation / RAG (copilot upgrade F3) ────────────
    anticipation_enabled: bool = True
    gemini_api_key: str = ""          # used for RAG embeddings (F3)
    embedding_model: str = "gemini-embedding-001"
    rag_top_k: int = 3
    rag_min_similarity: float = 0.45  # cosine-similarity gate for surfacing a card
    rag_cooldown_seconds: float = 25.0
    rag_window_words: int = 60

    # ── Multimodal vision worker (copilot upgrade F5) ─────────────────
    # Provider chain: VISION_MODEL → VISION_FALLBACK_MODEL → azure → nvidia.
    # First usable answer wins; a provider that 429s cools down 5 min.
    vision_provider: str = "auto"     # auto | gemini | azure | nvidia
    vision_enabled: bool = False      # opt-in: sends screen frames to the provider
    vision_model: str = "gemini-3.5-flash"
    vision_fallback_model: str = "gemini-3.5-flash-lite"  # multimodal, higher free RPM
    nvidia_api_key: str = ""          # NVIDIA NIM (build.nvidia.com) — vision fallback
    nvidia_vision_model: str = "meta/llama-3.2-90b-vision-instruct"
    vision_interval_seconds: float = 2.0    # capture cadence
    vision_min_interval_calls: float = 20.0  # rate limit for actual API calls
    vision_change_similarity: float = 0.95  # aHash gate: only send changed screens
    vision_context_max_age_seconds: float = 60.0

    # ── Agent prompts (all overridable via env — no hardcoded prompts) ─
    # VISION_PROMPT: generic screen description for the background worker.
    # VISION_ASK_PROMPT: used when the user asks about the screen; may contain
    #   a `{question}` placeholder which is replaced with the typed question.
    # COPILOT_SUGGEST_PROMPT / COPILOT_ASK_PROMPT: copilot dialogue system
    #   prompts; the suggest prompt's reply shape must stay the JSON object.
    vision_prompt: str = (
        "You are the screen analyst for a live AI copilot. Describe what is "
        "currently displayed on the presenter's screen in 25-40 words: name "
        "the application or window title, then the visible content — slide "
        "text, numbers, names, tables, charts, code or documents — that a "
        "presenter would reference while speaking. Always include concrete "
        "on-screen details; never answer with one or two words. Plain text, "
        "no headings."
    )
    vision_ask_prompt: str = (
        "Answer the presenter's question using what is visible on their "
        'screen. Question: "{question}" — describe only the on-screen facts '
        "that matter for the answer (max 80 words)."
    )
    copilot_suggest_prompt: str = (
        "You are PitchPilot Copilot, an AI assistant whispering helpful "
        "suggestions to the presenter during a LIVE sales/pitch/meeting "
        "call. You receive the live call transcript and an optional live "
        "description of what is on the presenter's screen. When the other "
        "party asks something, raises an objection, or a key moment arises, "
        "tell the presenter EXACTLY what to say next.\n"
        "Reply with ONLY a JSON object, no prose:\n"
        '{"trigger": "<what on the call or screen prompted this, under 10 '
        'words>", "text": "<the suggestion>"}\n'
        "Rules for `text`: max 55 words, conversational, first-person, "
        "ready to speak aloud verbatim, concrete (numbers/names from the "
        "screen or transcript when available). If nothing needs saying, "
        'return {"trigger": null, "text": null}.'
    )
    copilot_ask_prompt: str = (
        "You are PitchPilot Copilot, an AI assistant helping the presenter "
        "DURING a live call. You get the live call transcript, an optional "
        "live description of the presenter's screen, and their pitch "
        "outline. Answer the presenter's question concretely, using the "
        "screen description and transcript as your ground truth — quote "
        "actual numbers/names/text visible on screen.\n"
        "Reply with ONLY a JSON object, no prose:\n"
        '{"text": "<your answer>"}\n'
        "Rules for `text`: max 80 words, direct, actionable, ready to use "
        "mid-call. No preamble, no markdown."
    )

    # ── Cluely-style live copilot dialogue ────────────────────────────
    # Watches the transcript + screen and posts proactive suggestions; also
    # answers questions typed into the overlay's dialogue box. When enabled,
    # the screen-vision worker auto-starts alongside it (needs a provider).
    copilot_enabled: bool = True
    copilot_suggest_min_gap_seconds: float = 14.0   # anti-spam between suggestions
    copilot_suggest_every_words: int = 90           # periodic nudge cadence
    copilot_min_words_to_suggest: int = 8           # skip cold-start chatter
    copilot_stream_answers: bool = True             # stream answer tokens to the overlay

    # ── Post-session debrief (end-of-session AI review) ───────────────
    # DEBRIEF_PROMPT: end-of-session review prompt; must keep the JSON reply
    # shape {"summary": "<2 sentences>", "tips": ["<tip>", ...]}.
    debrief_enabled: bool = True
    debrief_prompt: str = (
        "You are PitchPilot, a public-speaking coach writing a short written "
        "debrief after a live pitch or presentation. You receive the session "
        "metrics, the presenter's outline, and the last words they said.\n"
        "Reply with ONLY a JSON object, no prose, in this exact shape:\n"
        '{"summary": "<2 sentences: what went well and the biggest fix>", '
        '"tips": ["<tip>", "<tip>", "<tip>"]}\n'
        "Rules: exactly 3 tips, each under 18 words, concrete and specific to "
        "the numbers you were given. No markdown, no preamble."
    )

    # ── Billing — PitchPilot Plus (Paystack) ──────────────────────────
    # PAYMENTS_PROVIDER: auto | paystack | demo.
    #   auto      → Paystack when PAYSTACK_SECRET_KEY is present, else demo.
    #   demo      → instant upgrade (no real charge; MVP demos only).
    # ALLOW_DEMO_BILLING is the explicit production opt-in for demo billing;
    # without it, a production boot refuses demo/auto-without-key.
    payments_provider: str = "auto"
    allow_demo_billing: bool = False
    paystack_secret_key: str = ""      # read from the workspace-root .env
    paystack_currency: str = "NGN"     # charge currency (NGN | USD | GHS …)
    plus_price_amount: int = 2250000   # minor units (kobo): 2250000 = ₦22,500
    plus_price_display: str = "₦22,500"  # shown to the user in the UI
    free_monthly_sessions: int = 3     # free-plan quota per calendar month
    free_monthly_scripts: int = 3     # free-plan quota per calendar month
    frontend_base_url: str = "http://localhost:5173"  # Paystack redirects back here

    # ── Deployment / runtime safety ───────────────────────────────────
    # ENVIRONMENT: development | production. In production the app refuses to
    # start with insecure defaults (default JWT secret, auto-created tables,
    # wildcard/localhost CORS, localhost frontend or DB URLs).
    environment: str = "development"

    # ── Abuse protection (in-process rate limiting) ───────────────────
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = 120        # general API budget per IP
    auth_rate_limit_per_minute: int = 12    # register/login per IP

    # ── Short-lived WebSocket tickets (keeps JWTs out of URLs/logs) ───
    ws_ticket_ttl_seconds: int = 900

    # ── Email verification (opt-in — enable in production) ────────────
    require_email_verification: bool = False
    # EMAIL_PROVIDER: smtp (default) | resend (HTTP API; works on hosts that
    # block outbound SMTP, such as Render Free).
    email_provider: str = "smtp"
    email_from: str = "PitchPilot <no-reply@pitchpilot.app>"
    resend_api_key: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True

    # ── Proxy / client IP trust ───────────────────────────────────────
    # True only when the API is reachable exclusively through a trusted
    # reverse proxy (Render, nginx). The limiter then uses the rightmost
    # X-Forwarded-For entry, which the nearest proxy appended. Never enable
    # this if uvicorn itself is exposed to the public internet.
    trust_proxy_headers: bool = False


def _is_placeholder(value: str) -> bool:
    """Detect empty values and the `<...>` / `example.com` placeholders used
    in the production template, so a copied-but-unfilled env cannot boot."""
    value = value.strip()
    if not value or "<" in value or ">" in value:
        return True
    lowered = value.lower()
    return (
        lowered.startswith("change-me")
        or lowered in {"changeme", "placeholder", "example"}
        or lowered.endswith(("example.com", "example.net", "example.org"))
        or lowered.endswith("your-domain.com")
    )


def production_config_issues(settings: "Settings") -> list[str]:
    """Insecure/missing settings that must block a production boot."""
    if settings.environment.strip().lower() != "production":
        return []
    issues: list[str] = []
    jwt_secret = settings.jwt_secret_key.strip()
    if _is_placeholder(jwt_secret) or len(jwt_secret) < 32:
        issues.append(
            "JWT_SECRET_KEY must be a random value of at least 32 characters"
        )
    if settings.auto_create_tables:
        issues.append("AUTO_CREATE_TABLES must be false in production (use Alembic)")
    provider = settings.payments_provider.strip().lower()
    if provider == "demo" and not settings.allow_demo_billing:
        issues.append(
            "PAYMENTS_PROVIDER=demo requires ALLOW_DEMO_BILLING=true "
            "(upgrades would otherwise be free)"
        )
    elif provider == "paystack" and _is_placeholder(settings.paystack_secret_key):
        issues.append("PAYMENTS_PROVIDER=paystack needs PAYSTACK_SECRET_KEY")
    elif (
        provider == "auto"
        and _is_placeholder(settings.paystack_secret_key)
        and not settings.allow_demo_billing
    ):
        issues.append(
            "PAYSTACK_SECRET_KEY is required in production, or set "
            "ALLOW_DEMO_BILLING=true for an explicitly free demo"
        )
    if any(origin.strip() == "*" for origin in settings.cors_origins):
        issues.append("CORS_ORIGINS must not contain '*'")
    if any(
        "localhost" in origin
        or "127.0.0.1" in origin
        or _is_placeholder(origin)
        for origin in settings.cors_origins
    ):
        issues.append("CORS_ORIGINS still points at localhost or a placeholder")
    if (
        "localhost" in settings.frontend_base_url
        or "127.0.0.1" in settings.frontend_base_url
        or _is_placeholder(settings.frontend_base_url)
    ):
        issues.append("FRONTEND_BASE_URL still points at localhost or a placeholder")
    if (
        "localhost" in settings.database_url
        or "127.0.0.1" in settings.database_url
        or _is_placeholder(settings.database_url)
    ):
        issues.append("DATABASE_URL still points at localhost or a placeholder")
    if settings.plus_price_amount <= 0 or not settings.plus_price_display.strip():
        issues.append("PLUS_PRICE_AMOUNT / PLUS_PRICE_DISPLAY must be configured")
    if settings.require_email_verification:
        email_provider = settings.email_provider.strip().lower()
        if email_provider == "resend":
            if _is_placeholder(settings.resend_api_key):
                issues.append("EMAIL_PROVIDER=resend needs RESEND_API_KEY")
        elif email_provider == "smtp":
            if _is_placeholder(settings.smtp_host):
                issues.append("REQUIRE_EMAIL_VERIFICATION=true needs SMTP_* configured")
        else:
            issues.append("EMAIL_PROVIDER must be 'smtp' or 'resend'")
        email_from = settings.email_from.lower()
        if "example.com" in email_from or "your-domain.com" in email_from:
            issues.append("EMAIL_FROM still uses a placeholder domain")
    stt_choice = settings.stt_provider.strip().lower()
    if stt_choice == "mock":
        issues.append(
            "STT_PROVIDER=mock is not allowed in production "
            "(transcripts would be simulated)"
        )
    elif stt_choice == "azure":
        missing = [
            name
            for name, value in (
                ("AZURE_OPENAI_API_KEY", settings.azure_openai_api_key),
                ("AZURE_OPENAI_ENDPOINT", settings.azure_openai_endpoint),
                ("AZURE_OPENAI_STT_DEPLOYMENT", settings.azure_openai_stt_deployment),
            )
            if _is_placeholder(value)
        ]
        if missing:
            issues.append(f"STT_PROVIDER=azure needs {', '.join(missing)}")
    elif stt_choice == "deepgram" and _is_placeholder(settings.deepgram_api_key):
        issues.append("STT_PROVIDER=deepgram needs DEEPGRAM_API_KEY")
    elif stt_choice == "openai" and _is_placeholder(settings.openai_api_key):
        issues.append("STT_PROVIDER=openai needs OPENAI_API_KEY")
    elif stt_choice == "groq" and _is_placeholder(settings.groq_api_key):
        issues.append("STT_PROVIDER=groq needs GROQ_API_KEY")
    elif stt_choice == "auto" and not (
        not _is_placeholder(settings.deepgram_api_key)
        or (
            not _is_placeholder(settings.azure_openai_api_key)
            and not _is_placeholder(settings.azure_openai_endpoint)
            and not _is_placeholder(settings.azure_openai_stt_deployment)
        )
        or not _is_placeholder(settings.openai_api_key)
        or not _is_placeholder(settings.groq_api_key)
    ):
        issues.append(
            "No speech-to-text provider configured in production "
            "(STT_PROVIDER=auto would fall back to the mock demo speaker)"
        )
    return issues


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor (import-safe, test-friendly)."""
    return Settings()
