# syntax=docker/dockerfile:1.7

# ============================================================
# Stage 1 — сборка amneziawg-go
# ============================================================
FROM golang:1.24-alpine AS awg-builder

RUN apk add --no-cache git make

WORKDIR /build
RUN git clone --depth 1 https://github.com/amnezia-vpn/amneziawg-go.git .
RUN mkdir -p /out \
    && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/amneziawg-go .

# ============================================================
# Stage 2 — минималистичный upstream образ
# ============================================================
FROM alpine:3.20 AS final

RUN apk add --no-cache \
    iproute2 \
    iptables \
    wireguard-tools

COPY --from=awg-builder /out/amneziawg-go /usr/local/bin/amneziawg-go
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /usr/local/bin/amneziawg-go /entrypoint.sh \
    && mkdir -p /var/run/wireguard

EXPOSE 51821/udp

ENTRYPOINT ["/entrypoint.sh"]
