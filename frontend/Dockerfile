# syntax=docker/dockerfile:1

# ── Build the SPA ────────────────────────────────────────────────────────
FROM node:22.21.1-alpine3.21 AS build
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# Same-origin by default: nginx proxies /api (and /api/ws) to the API service,
# so the browser never needs a cross-origin URL. Override for split hosting.
ARG VITE_API_BASE_URL=/api
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL

RUN npm run build

# ── Serve it ─────────────────────────────────────────────────────────────
FROM nginx:1.29.3-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -qO- http://127.0.0.1/healthz || exit 1
