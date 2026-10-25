"""PitchPilot FastAPI application entrypoint."""

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger

from app.api import auth_router, billing_router, scripts_router, sessions_router, stream_router
from app.config.settings import get_settings
from app.db.session import create_all_tables
from app.logging_config import setup_logging

# Flaky-resolver shield: cache getaddrinfo results in-process with
# stale-on-error fallback (this machine's DNS intermittently fails API hosts).
if get_settings().dns_cache_enabled:
    from app.services.dns_cache import install as _install_dns_cache

    _install_dns_cache()

settings = get_settings()
setup_logging(debug=settings.debug)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Fail fast on insecure production configuration.
    from app.config.settings import production_config_issues

    issues = production_config_issues(settings)
    if issues:
        joined = "; ".join(issues)
        logger.error(f"Refusing to start: insecure production config → {joined}")
        raise RuntimeError(f"Insecure production configuration: {joined}")

    # Live sessions hold per-session state in-process — warn on multi-worker.
    workers = os.environ.get("WEB_CONCURRENCY") or os.environ.get("UVICORN_WORKERS")
    if workers is not None and str(workers).isdigit() and int(workers) > 1:
        logger.warning(
            f"WEB_CONCURRENCY={workers}: live coaching keeps session state "
            "in-process, so multiple workers will strand sockets. Run ONE "
            "worker (or move session state to a shared store)."
        )

    if settings.auto_create_tables:
        try:
            await create_all_tables()
            logger.info("Database tables verified/created")
        except Exception as exc:  # noqa: BLE001 — DB may not be up yet
            logger.warning(
                f"Automatic table creation skipped ({exc}). "
                "Run `alembic upgrade head` once PostgreSQL is reachable."
            )
    from app.services.stt import resolve_stt_choice

    stt_choice = resolve_stt_choice(settings)
    if stt_choice == "mock":
        logger.info("STT provider: mock (demo mode — no transcription key found)")
    else:
        logger.info(f"STT provider: {stt_choice} (live transcription active)")

    # F1: native dual-audio capture availability
    from app.services.capture import capture_available

    logger.info(
        f"Native audio capture: "
        f"{'available' if capture_available(settings) else 'disabled (AUDIO_CAPTURE_ENABLED=false)'}"
    )

    # F3: RAG anticipation readiness
    from app.services.embedding import get_shared_embedding_client

    anticipator_mode = "vector search (Gemini embeddings)" if get_shared_embedding_client() is not None else "keyword search (no embedding key)"
    logger.info(f"Next-topic anticipation: {'enabled — ' + anticipator_mode if settings.anticipation_enabled else 'disabled'}")

    # F5: screen-vision worker (copilot screen-awareness; prompts come from
    # VISION_PROMPT / VISION_ASK_PROMPT in settings — nothing hardcoded).
    # Opt-in ONLY: screen frames are captured and sent to the configured
    # providers exclusively when VISION_ENABLED=true. The live copilot works
    # from the call transcript without it; the `screen` WebSocket command is
    # ignored while the feature is disabled.
    vision_worker = None
    from app.services.vision import vision_available

    if vision_available(settings) and settings.vision_enabled:
        from app.services.vision import ScreenVisionWorker, set_shared_worker

        vision_worker = ScreenVisionWorker(settings)
        vision_worker.start()
        set_shared_worker(vision_worker)
        logger.info(
            "Screen-vision worker: active (screen frames go to the configured "
            f"vision providers: {' -> '.join(vision_worker.providers)})"
        )
    else:
        logger.info(
            "Screen-vision worker: disabled (opt-in) "
            f"(VISION_ENABLED={settings.vision_enabled}, "
            f"providers={'none' if not vision_available(settings) else 'configured'})"
        )

    # Cluely-style live copilot dialogue
    logger.info(
        f"Live copilot dialogue: {'enabled' if settings.copilot_enabled else 'disabled (COPILOT_ENABLED=false)'}"
    )

    if settings.signups_enabled and not settings.signup_allowlist:
        logger.info("Signups: open")
    elif settings.signup_allowlist:
        logger.info(f"Signups: allowlist only ({len(settings.signup_allowlist)} addresses)")
    else:
        logger.info("Signups: closed (existing accounts can still sign in)")
    if not settings.require_recording_consent:
        logger.warning(
            "REQUIRE_RECORDING_CONSENT=false — users can start sessions without "
            "confirming permission to record the call"
        )

    # Billing — PitchPilot Plus (Paystack subscription, demo fallback)
    from app.services.billing import resolve_provider

    payments_mode = resolve_provider(settings)
    logger.info(
        f"Payments: {payments_mode} — Plus {settings.plus_price_display}/mo "
        f"({settings.free_monthly_sessions} free sessions & "
        f"{settings.free_monthly_scripts} free scripts per month on the free plan)"
    )

    logger.info(f"{settings.app_name} v{settings.app_version} is live")

    # Warm the DB pool/DNS in the background so the first user request is
    # never hit by a cold-resolver blip (common on this network).
    async def _warm_db() -> None:
        from sqlalchemy import text

        from app.db.session import engine

        for _ in range(5):
            try:
                async with engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
                logger.info("Database pool warmed up")
                return
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                await asyncio.sleep(1.5)

    warmup_task = asyncio.create_task(_warm_db())

    yield
    warmup_task.cancel()
    if vision_worker is not None:
        from app.services.vision import set_shared_worker

        await vision_worker.stop()
        set_shared_worker(None)
    logger.info(f"{settings.app_name} shutting down")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Just-in-time live public speaking & Zoom pitch coach",
    lifespan=lifespan,
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """Attach a stable request id to every response for log correlation."""
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    if response.status_code >= 500:
        logger.error(
            f"{request.method} {request.url.path} -> {response.status_code} "
            f"[request_id={request_id}]"
        )
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix=settings.api_prefix)
app.include_router(billing_router, prefix=settings.api_prefix)
app.include_router(scripts_router, prefix=settings.api_prefix)
app.include_router(sessions_router, prefix=settings.api_prefix)
app.include_router(stream_router, prefix=settings.api_prefix)


@app.get(f"{settings.api_prefix}/health", tags=["health"])
async def health() -> dict[str, str]:
    """Liveness: the process is up and serving requests."""
    return {"status": "ok", "app": settings.app_name, "version": settings.app_version}


@app.get(f"{settings.api_prefix}/ready", tags=["health"])
async def ready() -> JSONResponse:
    """Readiness: the process can reach its database."""
    from sqlalchemy import text

    from app.db.session import engine

    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 — readiness must not leak details
        logger.warning(f"Readiness check failed: {exc}")
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable"},
        )
    return JSONResponse(content={"status": "ready"})
