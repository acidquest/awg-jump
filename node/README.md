# awg-node

Минималистичный upstream-образ для AmneziaWG. Внутри только `amneziawg-go`, `wireguard-tools`, `iptables`, `iproute2` и entrypoint, который поднимает `awg0` из переменных окружения.

## Переменные окружения

- `AWG_LISTEN_PORT`: UDP-порт сервера внутри контейнера. По умолчанию `51821`.
- `AWG_PRIVATE_KEY`: приватный ключ интерфейса ноды. Обязателен.
- `AWG_ADDRESS`: адрес интерфейса `awg0` на ноде, например `10.20.0.3/32`. Обязателен.
- `AWG_PEER_PUBLIC_KEY`: публичный ключ `awg1` с jump-сервера. Обязателен.
- `AWG_PEER_ALLOWED_IPS`: какие адреса принадлежат jump-серверу, обычно `10.20.0.2/32`.
- `AWG_PEER_PRESHARED_KEY`: preshared key peer'а, если используется.
- `AWG_PEER_PERSISTENT_KEEPALIVE`: keepalive для peer-сессии.
- `AWG_S1`, `AWG_S2`, `AWG_S3`, `AWG_S4`: параметры обфускации AmneziaWG.
- `AWG_H1`, `AWG_H2`, `AWG_H3`, `AWG_H4`: параметры obfuscation hash для ноды.

## Пример `docker-compose.yml`

```yaml
services:
  awg-node:
    build:
      context: ./node
    container_name: awg-node
    restart: unless-stopped
    cap_add:
      - NET_ADMIN
      - NET_RAW
    sysctls:
      net.ipv4.ip_forward: "1"
    environment:
      AWG_LISTEN_PORT: 51821
      AWG_PRIVATE_KEY: "<node-private-key>"
      AWG_ADDRESS: "10.20.0.3/32"
      AWG_PEER_PUBLIC_KEY: "<jump-awg1-public-key>"
      AWG_PEER_ALLOWED_IPS: "10.20.0.2/32"
      AWG_PEER_PRESHARED_KEY: "<optional-psk>"
      AWG_PEER_PERSISTENT_KEEPALIVE: 25
      AWG_S1: 83
      AWG_S2: 47
      AWG_S3: 121
      AWG_S4: 33
      AWG_H1: 3928541027
      AWG_H2: 1847392610
      AWG_H3: 2938471056
      AWG_H4: 847392015
    ports:
      - "51821:51821/udp"
```

## Подключение к `awg-jump` вручную

1. На `awg-jump` получи публичный ключ `awg1` и адрес VPN-подсети для связи jump ↔ node.
2. На VPS с нодой сгенерируй приватный ключ для `AWG_PRIVATE_KEY` и выбери адрес из `NODE_VPN_SUBNET`, например `10.20.0.3/32`.
3. Передай в контейнер `AWG_PEER_PUBLIC_KEY=<jump awg1 public key>` и `AWG_PEER_ALLOWED_IPS=<jump awg1 address>`.
4. Если в `awg-jump` включена obfuscation, передай те же `S1..S4` и `H1..H4`, которые UI/API сохранил для peer-конфига ноды.
5. Запусти контейнер и проверь handshake: `docker exec awg-node wg show`.
6. В `awg-jump` добавь ноду в UI или API, укажи её публичный IP/порт `51821`, публичный ключ ноды и при необходимости preshared key.

После активации `awg-jump` обновит endpoint интерфейса `awg1` и начнёт слать не-RU трафик через эту ноду.
