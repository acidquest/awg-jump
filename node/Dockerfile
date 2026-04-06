# ============================================================
# Stage 1 — сборка amneziawg-go
# ============================================================
FROM golang:1.22-alpine AS awg-builder

RUN apk add --no-cache git make

WORKDIR /build
RUN git clone https://github.com/amnezia-vpn/amneziawg-go.git .
RUN go build -o amneziawg-go ./...

# ============================================================
# Stage 2 — минималистичный upstream образ
# ============================================================
FROM debian:bookworm-slim AS final

RUN apt-get update && apt-get install -y --no-install-recommends \
    iproute2 \
    iptables \
    wireguard-tools \
    && rm -rf /var/lib/apt/lists/*

COPY --from=awg-builder /build/amneziawg-go /usr/local/bin/amneziawg-go
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 51821/udp

ENTRYPOINT ["/entrypoint.sh"]
