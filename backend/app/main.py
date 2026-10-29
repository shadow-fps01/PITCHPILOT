"""PitchPilot FastAPI application entrypoint."""

import asyncio
import os
import re
import uuid
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger
from starlette.middleware.trustedhost import TrustedHostMiddleware

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
        logger.error(f"Refusing to start: insecure production config -> {joined}")
        raise RuntimeError(f"Insecure production configuration: {joined}")

    # Live sessions hold per-session state in-process -- warn on multi-worker.
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
        except Exception as exc:  # noqa: BLE001  -  DB may not be up yet
            logger.warning(
                f"Automatic table creation skipped ({exc}). "
                "Run `alembic upgrade head` once PostgreSQL is reachable."
            )
    from app.services.stt import resolve_stt_choice

    stt_choice = resolve_stt_choice(settings)
    if stt_choice == "none":
        logger.error(
            "STT provider: not configured  -  live transcription will be "
            "unavailable until a transcription provider key is set"
        )
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
    logger.info(f"Next-topic anticipation: {'enabled  -  ' + anticipator_mode if settings.anticipation_enabled else 'disabled'}")

    # F5: screen vision. Hosted web clients share a tab/window explicitly and
    # send frames over their live WebSocket; a server-side mss capture is only
    # started for the desktop/native setup when both flags are on.
    vision_worker = None
    from app.services.vision import vision_available

    if (
        vision_available(settings)
        and settings.vision_enabled
        and settings.vision_server_capture_enabled
    ):
        from app.services.vision import ScreenVisionWorker, set_shared_worker

        vision_worker = ScreenVisionWorker(settings)
        vision_worker.start()
        set_shared_worker(vision_worker)
        logger.info(
            "Server screen capture: active (screen frames go to the configured "
            f"vision providers: {' -> '.join(vision_worker.providers)})"
        )
    else:
        logger.info(
            "Server screen capture: disabled "
            f"(VISION_ENABLED={settings.vision_enabled}, "
            "VISION_SERVER_CAPTURE_ENABLED="
            f"{settings.vision_server_capture_enabled})"
        )

    if settings.vision_client_enabled and vision_available(settings):
        logger.info(
            "Browser screen sharing: available (client-initiated; "
            "frames arrive over the live WebSocket)"
        )
    else:
        logger.info(
            "Browser screen sharing: disabled "
            f"(VISION_CLIENT_ENABLED={settings.vision_client_enabled}, "
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

    from app.services.email import email_provider_configured

    if email_provider_configured(settings):
        logger.info(
            "Email delivery: "
            f"{settings.email_provider.strip().lower()} configured "
            f"(verification {'on' if settings.require_email_verification else 'off'})"
        )
    else:
        logger.warning(
            "Email delivery: disabled (no provider configured; password recovery "
            "returns a generic response but cannot deliver a reset link in production)"
        )
    if not settings.require_recording_consent:
        logger.warning(
            "REQUIRE_RECORDING_CONSENT=false  -  users can start sessions without "
            "confirming permission to record the call"
        )

    # Billing  -  PitchPilot Plus (Paystack subscription, demo fallback)
    from app.services.billing import paystack_key_mode, resolve_provider

    payments_mode = resolve_provider(settings)
    paystack_mode = (
        f" ({paystack_key_mode(settings)} key)"
        if payments_mode == "paystack"
        else ""
    )
    logger.info(
        f"Payments: {payments_mode}{paystack_mode}  -  Plus "
        f"{settings.plus_price_display}/mo "
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

    # Retention is opt-in so a local instance cannot mutate a shared database.
    retention_task: asyncio.Task[None] | None = None
    if settings.retention_cleanup_enabled:
        from app.services.retention import cleanup_loop

        retention_task = asyncio.create_task(cleanup_loop(settings))
        logger.info(
            "Retention cleanup: enabled "
            f"({settings.data_retention_days}-day transcript retention, "
            f"{settings.stale_session_hours}h abandoned-session cutoff)"
        )
    else:
        logger.info("Retention cleanup: disabled (RETENTION_CLEANUP_ENABLED=false)")

    yield
    warmup_task.cancel()
    if retention_task is not None:
        retention_task.cancel()
        try:
            await retention_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass

    # Persist an end time for every live session before the process exits.
    # Without this, a Render restart leaves those rows looking "Live" forever.
    from app.services.session_registry import registry

    try:
        await asyncio.wait_for(registry.finalize_all(), timeout=8.0)
    except asyncio.TimeoutError:
        logger.warning("Timed out finalizing live sessions during shutdown")
    except Exception as exc:  # noqa: BLE001  -  shutdown must continue
        logger.warning(f"Live-session shutdown finalization failed: {exc}")

    if vision_worker is not None:
        from app.services.vision import set_shared_worker

        await vision_worker.stop()
        set_shared_worker(None)
    logger.info(f"{settings.app_name} shutting down")


_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_IS_PRODUCTION = settings.environment.strip().lower() == "production"
_docs_enabled = not _IS_PRODUCTION

_API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"


def _apply_security_headers(response, request_id: str, path: str) -> None:
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
    response.headers["Cache-Control"] = "no-store"
    # The API never returns HTML in production (docs are disabled), so a
    # locked-down CSP is safe. In development the docs pages need their CDN
    # assets, so only send the API CSP for JSON endpoints.
    if _IS_PRODUCTION or not path.startswith(("/docs", "/redoc", "/openapi")):
        response.headers["Content-Security-Policy"] = _API_CSP
    if _IS_PRODUCTION:
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Just-in-time live public speaking & Zoom pitch coach",
    lifespan=lifespan,
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """Attach a sanitized request id and the API security headers."""
    incoming = request.headers.get("x-request-id", "")
    request_id = incoming if _REQUEST_ID_RE.fullmatch(incoming) else uuid.uuid4().hex

    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            too_large = int(content_length) > settings.max_http_body_bytes
        except ValueError:
            response = JSONResponse(
                {"detail": "Invalid Content-Length header"}, status_code=400
            )
            _apply_security_headers(response, request_id, request.url.path)
            return response
        if too_large:
            response = JSONResponse(
                {"detail": "Request body too large"}, status_code=413
            )
            _apply_security_headers(response, request_id, request.url.path)
            return response

    response = await call_next(request)
    _apply_security_headers(response, request_id, request.url.path)
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
# Outermost middleware: rejects requests whose Host header is not ours
# (DNS-rebinding / Host-header attacks) before routing.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)


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
    except Exception as exc:  # noqa: BLE001  -  readiness must not leak details
        logger.warning(f"Readiness check failed: {exc}")
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable"},
        )
    return JSONResponse(content={"status": "ready"})
