# syntax=docker/dockerfile:1.7

FROM golang:1.24-alpine AS awg-builder

RUN apk add --no-cache ca-certificates git make
WORKDIR /build
RUN git clone --depth 1 https://github.com/amnezia-vpn/amneziawg-go.git .
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/amneziawg-go .

FROM debian:bookworm-slim AS awg-tools-builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates git build-essential pkg-config libmnl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
RUN git clone --depth 1 https://github.com/amnezia-vpn/amneziawg-tools.git .
RUN make -C src -j"$(nproc)" && make -C src install DESTDIR=/out PREFIX=/usr

FROM python:3.12-slim-bookworm AS python-builder

ENV VIRTUAL_ENV=/opt/venv PATH=/opt/venv/bin:$PATH
WORKDIR /build
COPY gateway/backend/requirements.txt .
RUN python -m venv "$VIRTUAL_ENV" \
    && pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

FROM node:20-alpine AS frontend-builder

WORKDIR /frontend
COPY gateway/frontend/package*.json ./
RUN npm install
COPY gateway/frontend/ .
RUN npm run build

FROM python:3.12-slim-bookworm

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app/gateway/backend \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    conntrack \
    curl \
    dnsmasq \
    iproute2 \
    ipset \
    iptables \
    nftables \
    iputils-ping \
    procps \
    tcpdump \
    traceroute \
    wireguard-tools \
    && rm -rf /var/lib/apt/lists/*

COPY --from=awg-builder /out/amneziawg-go /usr/local/bin/amneziawg-go
COPY --from=awg-tools-builder /out/usr/bin/awg /usr/local/bin/awg
COPY --from=python-builder /opt/venv /opt/venv
COPY --from=frontend-builder /frontend/dist /app/gateway/frontend/dist
COPY gateway/backend /app/gateway/backend
COPY deploy /app/deploy
COPY .env.ru.example /app/.env.ru.example
COPY .env.en.example /app/.env.en.example

WORKDIR /app

EXPOSE 8081/tcp

HEALTHCHECK --interval=20s --timeout=3s --retries=5 CMD sh -c 'curl -fsS "http://127.0.0.1:${WEB_PORT:-8081}/api/system/health" || exit 1'

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port \"${WEB_PORT:-8081}\""]
