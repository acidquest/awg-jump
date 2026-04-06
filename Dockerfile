# ============================================================
# Stage 1 — сборка amneziawg-go
# ============================================================
FROM golang:1.22-alpine AS awg-builder

RUN apk add --no-cache git make

WORKDIR /build
RUN git clone https://github.com/amnezia-vpn/amneziawg-go.git .
RUN go build -o amneziawg-go ./...

# ============================================================
# Stage 2 — сборка frontend
# ============================================================
FROM node:20-alpine AS frontend-builder

WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build

# ============================================================
# Stage 3 — финальный образ
# ============================================================
FROM debian:bookworm-slim AS final

RUN apt-get update && apt-get install -y --no-install-recommends \
    iproute2 \
    iptables \
    ipset \
    wireguard-tools \
    curl \
    ca-certificates \
    openssh-client \
    python3 \
    python3-pip \
    supervisor \
    procps \
    net-tools \
    && rm -rf /var/lib/apt/lists/*

COPY --from=awg-builder /build/amneziawg-go /usr/local/bin/amneziawg-go
COPY --from=frontend-builder /frontend/dist /app/static

COPY backend/requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /app/requirements.txt

COPY backend/ /app/backend/
COPY node/ /app/node/
COPY scripts/ /app/scripts/
COPY supervisord.conf /etc/supervisor/conf.d/awg-jump.conf

RUN chmod +x /app/scripts/*.sh

WORKDIR /app

EXPOSE 51820/udp
EXPOSE 8080/tcp

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
