# awg-node

Минималистичный upstream-образ для AmneziaWG. Нода умеет работать как через userspace `amneziawg-go`, так и через kernel module `amneziawg`. Предпочтительный вариант для VPS-хоста ноды — kernel mode: он стабильнее и лучше подходит для длительной эксплуатации.

## Рекомендуемый режим: kernel module на хосте

Если на хостовой машине доступна установка kernel module, рекомендуется использовать именно её. Контейнер `awg-node` автоматически переключится в kernel mode, если `ip link add ... type amneziawg` доступен на хосте.

Перед установкой модуля обязательно установите headers именно для текущего ядра:

```bash
apt-get update
apt-get install --yes linux-headers-$(uname -r) dkms build-essential
```

Готовые скрипты установки для хоста:

- Debian 12: [scripts/install-kernel-module-debian12.sh](/opt/awg-jump/node/scripts/install-kernel-module-debian12.sh)
- Debian 13: [scripts/install-kernel-module-debian13.sh](/opt/awg-jump/node/scripts/install-kernel-module-debian13.sh)

При SSH-деплое upstream-ноды директория `node/` целиком копируется на удалённый VPS в `/opt/awg-node`, поэтому эти скрипты будут доступны на хосте как:

- `/opt/awg-node/scripts/install-kernel-module-debian12.sh`
- `/opt/awg-node/scripts/install-kernel-module-debian13.sh`

После установки модуля на хосте проверка должна проходить так:

```bash
modprobe amneziawg
ip link add awg_probe type amneziawg && ip link del awg_probe
```

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
2. На VPS ноды по возможности сначала установи kernel module `amneziawg` на хостовой машине. Для Debian 12/13 можно использовать готовые скрипты из `scripts/` выше.
3. На VPS с нодой сгенерируй приватный ключ для `AWG_PRIVATE_KEY` и выбери адрес из `NODE_VPN_SUBNET`, например `10.20.0.3/32`.
4. Передай в контейнер `AWG_PEER_PUBLIC_KEY=<jump awg1 public key>` и `AWG_PEER_ALLOWED_IPS=<jump awg1 address>`.
5. Если в `awg-jump` включена obfuscation, передай те же `S1..S4` и `H1..H4`, которые UI/API сохранил для peer-конфига ноды.
6. Запусти контейнер и проверь handshake: `docker exec awg-node awg show awg0`.
7. В `awg-jump` добавь ноду в UI или API, укажи её публичный IP/порт `51821`, публичный ключ ноды и при необходимости preshared key.

После активации `awg-jump` обновит endpoint интерфейса `awg1` и начнёт слать не-RU трафик через эту ноду.
