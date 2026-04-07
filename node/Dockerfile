# syntax=docker/dockerfile:1.7

# ============================================================
# Stage 1 — сборка amneziawg-go (userspace демон)
# ============================================================
FROM golang:1.24-alpine AS awg-builder

RUN apk add --no-cache ca-certificates git make

WORKDIR /build
RUN git clone --depth 1 https://github.com/amnezia-vpn/amneziawg-go.git .
RUN mkdir -p /out \
    && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/amneziawg-go .

# ============================================================
# Stage 2 — сборка amneziawg-tools (команда awg) для Alpine/musl
# Нужна для работы с kernel module: awg setconf/syncconf/show
# ============================================================
FROM alpine:3.20 AS awg-tools-builder

RUN apk add --no-cache ca-certificates git build-base libmnl-dev pkgconf bash

WORKDIR /build
RUN git clone --depth 1 https://github.com/amnezia-vpn/amneziawg-tools.git .
RUN make -C src -j$(nproc) && make -C src install DESTDIR=/out PREFIX=/usr

# ============================================================
# Stage 3 — минималистичный upstream образ
# ============================================================
FROM alpine:3.20 AS final

RUN apk add --no-cache \
    iproute2 \
    iptables \
    tcpdump \
    wireguard-tools \
    libmnl

COPY --from=awg-builder /out/amneziawg-go /usr/local/bin/amneziawg-go
COPY --from=awg-tools-builder /out/usr/bin/awg /usr/local/bin/awg
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /usr/local/bin/amneziawg-go /usr/local/bin/awg /entrypoint.sh \
    && mkdir -p /var/run/amneziawg

EXPOSE 51821/udp

ENTRYPOINT ["/entrypoint.sh"]
