#!/bin/sh
# Apply migrations, then start the API.
#
# IMPORTANT: exactly ONE uvicorn worker. Live coaching sessions keep their
# state in-process (see LiveSessionRegistry), so a second worker would strand
# WebSocket connections that were accepted by the first.
#
# Client IP trust is handled in app/services/rate_limit.py: it reads the
# RIGHTMOST X-Forwarded-For entry (the one the nearest proxy appended) and
# only when the peer is private/loopback or TRUST_PROXY_HEADERS=true. Do not
# use --forwarded-allow-ips="*": uvicorn would trust the leftmost, spoofable
# entry and bypass per-IP rate limits.
set -e

echo "→ Applying database migrations…"
alembic upgrade head

echo "→ Starting PitchPilot API (1 worker, wsproto)…"
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "${PORT:-8000}" \
    --workers 1 \
    --ws wsproto \
    --ws-max-size 4194304 \
    --ws-ping-interval 20 \
    --ws-ping-timeout 20 \
    --timeout-keep-alive 65 \
    --no-server-header \
    --proxy-headers \
    --forwarded-allow-ips="${FORWARDED_ALLOW_IPS:-127.0.0.1}"
