# AWG Jump Server — CLAUDE.md

## Описание проекта

Docker-контейнер jump-сервера на базе AmneziaWG (форк WireGuard с обфускацией).
Реализует политику маршрутизации: RU-трафик → физический интерфейс хоста, остальное → awg1 (upstream VPN).
Включает удалённый деплой минималистичных upstream-нод через SSH прямо из веб-интерфейса.

## Архитектура

```
Браузер (HTTPS:443) ──► nginx ──► awg-jump:8080 (FastAPI + static SPA)
                         │
                         └── TLS: self-signed cert (10 лет, авто-генерация при первом старте)

Клиент (AWG) ──► awg0 (сервер, UDP) ──► [ipset/iptables policy routing]
                                          ├── RU CIDR (ipset:geoip_ru) ──► eth0 (физический выход)
                                          └── остальное ──► awg1 ──► активная upstream нода
                                                                          │
                                              ┌───────────────────────────┘
                                              │  failover между нодами
                                              ▼
                                    ┌──────────────────────┐  ┌──────────────────────┐
                                    │   awg-node: VPS #1   │  │   awg-node: VPS #2   │
                                    │  (минималистичный)   │  │   (резервная нода)   │
                                    └──────────────────────┘  └──────────────────────┘
                                    (деплоится через SSH из UI awg-jump)
```

### Два образа в проекте

| Образ | Назначение | Содержимое |
|-------|-----------|------------|
| `awg-jump` | основной jump-сервер | AWG + FastAPI + React + GeoIP + routing + SSH deployer |
| `awg-node` | удалённый upstream узел | только amneziawg-go, минимальный entrypoint |
| `nginx` | reverse proxy + TLS termination | nginx:alpine + self-signed cert (авто-генерация) |

### Компоненты awg-jump

| Компонент | Роль |
|-----------|------|
| `amneziawg-go` | userspace AWG (awg0 + awg1) |
| `FastAPI` | REST API + SSE |
| `SQLite` | peers, конфиги, upstream ноды, история деплоев |
| `Uvicorn` | ASGI сервер |
| `React SPA` | веб-интерфейс |
| `APScheduler` | GeoIP cron, health-check нод, failover |
| `asyncssh` | SSH-клиент для удалённого деплоя |
| `supervisor` | управление процессами |

## Стек

- **Backend**: Python 3.12, FastAPI, SQLAlchemy (async), Alembic, aiosqlite, asyncssh
- **Frontend**: React 18, Vite, TanStack Query, Recharts
- **System**: amneziawg-go, iproute2, ipset, iptables
- **Container**: Docker (три образа: awg-jump + awg-node + nginx), docker-compose

## Структура проекта

```
awg-jump/
├── CLAUDE.md
├── README.md
├── .env.example
├── docker-compose.yml
│
├── Dockerfile                 # основной образ awg-jump (3-stage)
│
├── nginx/
│   ├── nginx.conf             # reverse proxy → awg-jump:8080, HTTPS only
│   └── generate-cert.sh       # авто-генерация self-signed cert если нет
│
├── node/                      # минималистичный upstream образ
│   ├── Dockerfile             # 2-stage: awg-builder + debian-slim
│   ├── entrypoint.sh          # запуск AWG сервера из env-переменных
│   └── README.md
│
├── backend/
│   ├── main.py
│   ├── config.py
│   ├── database.py
│   ├── models/
│   │   ├── peer.py
│   │   ├── interface.py
│   │   ├── geoip.py
│   │   └── upstream_node.py   # upstream ноды, история деплоев, метрики
│   ├── routers/
│   │   ├── auth.py
│   │   ├── interfaces.py
│   │   ├── peers.py
│   │   ├── routing.py
│   │   ├── geoip.py
│   │   ├── system.py
│   │   ├── backup.py
│   │   └── nodes.py           # деплой нод, статус, переключение, метрики
│   ├── services/
│   │   ├── awg.py
│   │   ├── ipset_manager.py
│   │   ├── routing.py
│   │   ├── geoip_fetcher.py
│   │   └── node_deployer.py   # SSH деплой, health-check, failover
│   ├── scheduler.py
│   └── alembic/
│
├── frontend/
│   ├── package.json
│   ├── vite.config.ts
│   └── src/
│       ├── App.tsx
│       ├── pages/
│       │   ├── Dashboard.tsx
│       │   ├── Interfaces.tsx
│       │   ├── Peers.tsx
│       │   ├── Routing.tsx
│       │   ├── GeoIP.tsx
│       │   ├── Nodes.tsx      # страница управления upstream нодами
│       │   └── Backup.tsx
│       └── components/
│
├── scripts/
│   ├── entrypoint.sh
│   ├── update_geoip.sh
│   └── backup.sh
│
└── data/
    ├── config.db
    ├── certs/                 # TLS сертификаты (авто-генерируются, volume)
    │   ├── server.crt
    │   └── server.key
    ├── geoip/
    └── wg_configs/
```

## Переменные окружения (.env)

```bash
# Nginx / TLS
NGINX_HTTPS_PORT=443
# CN для self-signed сертификата (IP или hostname сервера)
# Если пусто — используется IP адреса контейнера / localhost
TLS_COMMON_NAME=localhost
# Сертификат живёт в volume ./data/certs/ — не пересоздаётся при перезапуске

# Администратор
ADMIN_USERNAME=admin
ADMIN_PASSWORD=changeme

# Веб-интерфейс
WEB_PORT=8080
SECRET_KEY=random-secret-key-here
SESSION_TTL_HOURS=8

# AWG интерфейс 0 (сервер для клиентов)
AWG0_LISTEN_PORT=51820
AWG0_PRIVATE_KEY=
AWG0_ADDRESS=10.10.0.1/24
AWG0_DNS=1.1.1.1

# AWG интерфейс 1 (клиент на upstream VPN)
AWG1_ENDPOINT=               # заполняется автоматически при активации ноды
AWG1_PRIVATE_KEY=
AWG1_PUBLIC_KEY=
AWG1_PRESHARED_KEY=
AWG1_ADDRESS=10.20.0.2/32
AWG1_ALLOWED_IPS=0.0.0.0/0
AWG1_PERSISTENT_KEEPALIVE=25

# Маршрутизация
PHYSICAL_IFACE=eth0
ROUTING_TABLE_RU=100
ROUTING_TABLE_VPN=200
FWMARK_RU=0x1
FWMARK_VPN=0x2

# GeoIP
GEOIP_SOURCE_RU=http://www.ipdeny.com/ipblocks/data/countries/ru.zone
GEOIP_UPDATE_CRON=0 4 * * *
GEOIP_FETCH_TIMEOUT=30

# Upstream ноды — failover
NODE_HEALTH_CHECK_INTERVAL=30      # секунды между проверками
NODE_HEALTH_CHECK_TIMEOUT=5        # таймаут ping/handshake
NODE_FAILOVER_THRESHOLD=3          # кол-во неудачных проверок до failover
NODE_AWG_PORT=51821                # порт AWG на удалённых нодах
NODE_VPN_SUBNET=10.20.0.0/24      # подсеть для связи jump ↔ nodes

# AmneziaWG обфускация
# Параметры НЕ задаются вручную в .env — генерируются автоматически при первом старте
# и сохраняются в БД (таблица interface.obfuscation_params).
# awg0 и awg1 имеют независимые наборы параметров.
# При бэкапе параметры включаются в config.db автоматически.

# Пути
DATA_DIR=/data
DB_PATH=/data/config.db
GEOIP_CACHE_DIR=/data/geoip
BACKUP_DIR=/data/backups
WG_CONFIG_DIR=/data/wg_configs
```

## Критические требования к реализации

### Сеть и безопасность
- awg-jump: `--cap-add NET_ADMIN --cap-add NET_RAW --sysctl net.ipv4.ip_forward=1`
- iptables MASQUERADE на eth0 и awg1 для NAT
- Порт 8080 awg-jump НЕ публикуется наружу — только внутри docker network (awg-jump → nginx)
- Наружу торчат только: UDP AWG0_LISTEN_PORT (awg-jump) и TCP 443 (nginx)
- Сессии в памяти (не в БД), TTL = 8 часов
- SSH credentials (login/password для деплоя) — **никогда не сохранять в БД или логах**

### Nginx и TLS
- Образ: `nginx:alpine` (без кастомного Dockerfile — конфиг монтируется как volume)
- TLS termination на nginx, бэкенд awg-jump слушает HTTP на 8080 (внутри docker network)
- Self-signed сертификат генерируется скриптом `nginx/generate-cert.sh` при первом старте:
  ```bash
  openssl req -x509 -newkey rsa:4096 -sha256 -days 3650     -nodes -keyout /certs/server.key -out /certs/server.crt     -subj "/CN=${TLS_COMMON_NAME}"     -addext "subjectAltName=IP:${TLS_COMMON_NAME},DNS:${TLS_COMMON_NAME}"
  ```
  3650 дней = ~10 лет
- Сертификат хранится в `./data/certs/` (volume), не пересоздаётся при перезапуске контейнера
- Скрипт проверяет: если `server.crt` уже существует — пропустить генерацию
- nginx.conf: только HTTPS (443), HTTP (80) редиректит на HTTPS, proxy_pass → http://awg-jump:8080
- docker-compose: nginx depends_on awg-jump, общая internal network `awg-net`

### ipset и маршрутизация
- ipset тип `hash:net`, имя `geoip_ru`
- Atomic swap при обновлении: `geoip_ru_new` → swap → destroy old
- ip rule: fwmark 0x1 → table 100 (RU→eth0), fwmark 0x2 → table 200 (VPN→awg1)
- iptables mangle FORWARD: match ipset → fwmark

### AWG управление
- amneziawg-go — userspace демон, не требует модуля ядра
- Конфиги генерируются из БД динамически
- Hot reload пиров: `wg syncconf`
- При смене активной ноды: обновить awg1 endpoint + `wg set awg1 peer ...`

### Параметры обфускации AmneziaWG

Два независимых набора параметров: для **awg0** (клиент→jump) и **awg1** (jump→нода).
Генерируются автоматически при первом старте если отсутствуют в БД.
Хранятся в таблице `interfaces` (колонки obf_*). Включаются в бэкап через config.db.

#### Параметры и правила генерации

| Параметр | Тип | Описание | Сторона | Генерация |
|----------|-----|----------|---------|-----------|
| `Jc` | int | кол-во junk-пакетов перед handshake | **только клиент** | randint(4, 12) |
| `Jmin` | int | мин. размер junk-пакета | **только клиент** | randint(40, 80) |
| `Jmax` | int | макс. размер junk-пакета (< MTU!) | **только клиент** | Jmin + randint(10, 50), но < 1280 |
| `S1` | int | padding handshake initial | оба конца | randint(15, 150) |
| `S2` | int | padding handshake response | оба конца | randint(15, 150) |
| `S3` | int | padding handshake cookie | оба конца | randint(15, 150) |
| `S4` | int | padding transport messages | оба конца | randint(15, 150) |
| `H1` | uint32 | header handshake initial | оба конца (одно значение) | random uint32, не 1/2/3/4 |
| `H2` | uint32 | header handshake response | оба конца (одно значение) | random uint32, уникальный |
| `H3` | uint32 | header cookie message | оба конца (одно значение) | random uint32, уникальный |
| `H4` | uint32 | header transport message | оба конца (одно значение) | random uint32, уникальный |

**Критически важно:**
- `Jmax` ДОЛЖЕН быть меньше системного MTU (обычно 1500). Безопасный потолок — 1280.
  Иначе система фрагментирует junk-пакеты, что выглядит подозрительно для DPI.
- `Jc`, `Jmin`, `Jmax` — указываются **только на стороне клиента** (junk не несёт данных).
  Для awg0: в конфиге клиентских пиров. Для awg1: в [Interface] секции awg-jump (он клиент).
- H1/H2/H3/H4 — должны быть **одинаковыми** на обоих концах туннеля и **уникальными** между собой.
  Стандартные значения WG: 1, 2, 3, 4 — нельзя использовать.
- S1/S2/S3/S4 — должны быть **одинаковыми** на обоих концах туннеля.

#### Распределение по туннелям

**awg0 (jump-сервер принимает клиентов):**
- [Interface] awg0: S1, S2, S3, S4, H1, H2, H3, H4 (padding + headers)
- [Peer] в клиентском конфиге: Jc, Jmin, Jmax, S1, S2, S3, S4, H1, H2, H3, H4
  (клиент несёт все параметры включая junk)

**awg1 (jump-сервер подключается к upstream ноде как клиент):**
- [Interface] awg1: Jc, Jmin, Jmax, S1, S2, S3, S4, H1, H2, H3, H4
  (jump — клиент, поэтому несёт junk)
- [Interface] awg-node (сервер): S1, S2, S3, S4, H1, H2, H3, H4
  (нода — сервер, junk не нужен)

#### Генерация в коде (awg.py)

```python
import random, secrets

def generate_obfuscation_params() -> dict:
    """Генерация независимого набора параметров обфускации для одного туннеля."""
    # Junk packets (для клиентской стороны)
    jc = random.randint(4, 12)
    jmin = random.randint(40, 80)
    jmax = min(jmin + random.randint(10, 50), 1279)  # строго < 1280

    # Padding (симметричные, одинаковые на обоих концах)
    s1 = random.randint(15, 150)
    s2 = random.randint(15, 150)
    s3 = random.randint(15, 150)
    s4 = random.randint(15, 150)

    # Headers — уникальные uint32, не равные стандартным WG (1,2,3,4)
    reserved = {1, 2, 3, 4}
    headers = set()
    while len(headers) < 4:
        val = secrets.randbits(32)
        if val not in reserved and val not in headers and val != 0:
            headers.add(val)
    h1, h2, h3, h4 = list(headers)

    return {
        "jc": jc, "jmin": jmin, "jmax": jmax,
        "s1": s1, "s2": s2, "s3": s3, "s4": s4,
        "h1": h1, "h2": h2, "h3": h3, "h4": h4,
    }
```

#### Формат в wg конфиге

```ini
[Interface]
# ... ключи, адреса ...
Jc = 7
Jmin = 50
Jmax = 90
S1 = 83
S2 = 47
S3 = 121
S4 = 33
H1 = 3928541027
H2 = 1847392610
H3 = 2938471056
H4 = 847392015
```

#### Хранение в БД (Interface модель)
Добавить колонки к таблице `interfaces`:
```
obf_jc, obf_jmin, obf_jmax          # junk (используются только клиентом)
obf_s1, obf_s2, obf_s3, obf_s4      # padding (симметричные)
obf_h1, obf_h2, obf_h3, obf_h4      # headers (симметричные)
obf_generated_at: datetime           # когда сгенерированы
```
При первом старте: если obf_h1 IS NULL → вызвать generate_obfuscation_params() и сохранить.
Параметры можно регенерировать через UI (кнопка "Regenerate obfuscation" на странице Interfaces).

### База данных
- SQLite WAL mode
- Alembic миграции, `upgrade head` при старте

### Upstream ноды — модель данных (UpstreamNode)
```
id, name, host (IP/hostname), ssh_port,
awg_port, awg_address (10.20.0.x/32),
public_key (AWG, генерируется при деплое),
preshared_key,
status: enum(pending|deploying|online|degraded|offline|error),
is_active: bool (только одна активна в awg1),
priority: int (для failover порядка),
last_seen: datetime,
last_deploy: datetime,
deploy_log: text,
rx_bytes, tx_bytes, latency_ms
```

### DeployLog — история деплоев
```
id, node_id (FK), started_at, finished_at,
status: enum(running|success|failed),
log_output: text (весь вывод SSH сессии)
```

### SSH деплой — последовательность шагов

**Стратегия**: исходники `node/` уже лежат на jump-сервере (пользователь сам кладёт).
При деплое jump-сервер упаковывает папку `node/` в tar и передаёт на удалённый сервер
через SSH pipe — никакого git, никакого registry.

```
jump-сервер (папка /app/node/) → tar | ssh → удалённый сервер → docker build → docker-compose up
```

node_deployer.py выполняет через asyncssh:
1. Подключение: `ssh user@host -p port` (пароль из запроса, не сохраняется)
2. `apt-get update && apt-get upgrade -y`
3. `apt-get install -y docker.io docker-compose curl ca-certificates`
4. `systemctl enable --now docker`
5. Генерировать AWG keypair для ноды (локально на jump-сервере)
6. Назначить уникальный awg_address из NODE_VPN_SUBNET
7. Передать исходники ноды на удалённый сервер:
   ```python
   # Локально: упаковать /app/node/ в tar bytes (in-memory, без временных файлов)
   buf = io.BytesIO()
   with tarfile.open(fileobj=buf, mode='w:gz') as tar:
       tar.add('/app/node', arcname='awg-node')
   tar_bytes = buf.getvalue()

   # Через asyncssh: mkdir + распаковка одной командой
   await conn.run('mkdir -p /opt/awg-node')
   async with conn.create_process('tar -xzf - -C /opt/awg-node --strip-components=1') as proc:
       proc.stdin.write(tar_bytes)
       proc.stdin.write_eof()
       await proc.wait()
   ```
8. Записать `/opt/awg-node/.env` через asyncssh sftp или echo:
   AWG_PRIVATE_KEY, AWG_ADDRESS, AWG_LISTEN_PORT,
   AWG_PEER_PUBLIC_KEY (public key awg1 jump-сервера),
   AWG_PEER_ALLOWED_IPS=10.20.0.2/32,
   AWG_JUNK_PACKET_COUNT, AWG_JUNK_PACKET_MIN_SIZE, AWG_JUNK_PACKET_MAX_SIZE
9. `docker build -t awg-node:local /opt/awg-node`
   ВАЖНО: вывод docker build стримить построчно (самый долгий шаг, 2-5 мин)
10. Записать `/opt/awg-node/docker-compose.yml` для запуска awg-node:local
11. `docker-compose -f /opt/awg-node/docker-compose.yml up -d`
12. sleep 5, `docker ps | grep awg-node` → проверка
13. Сохранить node.public_key, node.status=online в БД
14. Добавить ноду как peer в awg1 jump-сервера:
    `wg set awg1 peer <pubkey> endpoint <host:awg_port> allowed-ips <awg_address>`
15. Если первая нода или is_active=True → обновить routing

Весь вывод каждого шага → DeployLog.log_output + SSE стрим на фронт.

### Redeploy (обновление ноды)
При повторном деплое (кнопка Redeploy в UI):
1. SSH подключение (credentials вводятся снова)
2. Повторить шаги 7-8: передать свежие исходники + перезаписать .env
   (ключи не меняются — берутся из БД)
3. `docker build -t awg-node:local /opt/awg-node`
4. `docker-compose -f /opt/awg-node/docker-compose.yml up -d --force-recreate`
AWG peer на jump-сервере не меняется.

### Health-check и Failover
APScheduler задача каждые `NODE_HEALTH_CHECK_INTERVAL` секунд:
- Для каждой ноды со статусом online/degraded:
  - Проверить last_handshake через `wg show awg1` (если нода активна)
  - Если last_handshake > 3 минуты → статус degraded, счётчик +1
  - Если счётчик >= NODE_FAILOVER_THRESHOLD → failover:
    1. Найти следующую ноду по priority с статусом online
    2. Обновить awg1 endpoint + peer
    3. Обновить is_active флаги в БД
    4. Записать событие в лог

### awg-node образ (node/Dockerfile)
Минималистичный 2-stage:
- Stage 1: golang:1.22-alpine → сборка amneziawg-go
- Stage 2: debian:bookworm-slim → только amneziawg-go + wireguard-tools + iproute2 + iptables
- Конфигурация полностью через env-переменные
- Нет Python, нет веб-интерфейса, нет SQLite

### awg-node .env (генерируется jump-сервером при деплое)
```bash
AWG_LISTEN_PORT=51821
AWG_PRIVATE_KEY=<сгенерирован jump-сервером>
AWG_ADDRESS=10.20.0.x/32        # уникальный адрес ноды
AWG_PEER_PUBLIC_KEY=<public key awg1 интерфейса jump-сервера>
AWG_PEER_ALLOWED_IPS=10.20.0.2/32
AWG_PEER_ENDPOINT=              # пусто — нода сервер, не инициирует

# Обфускация — нода является СЕРВЕРОМ, junk не нужен (Jc/Jmin/Jmax отсутствуют)
# Только симметричные параметры — берутся из obf_* полей awg1 интерфейса jump-сервера
AWG_S1=<obf_s1 из awg1>
AWG_S2=<obf_s2 из awg1>
AWG_S3=<obf_s3 из awg1>
AWG_S4=<obf_s4 из awg1>
AWG_H1=<obf_h1 из awg1>
AWG_H2=<obf_h2 из awg1>
AWG_H3=<obf_h3 из awg1>
AWG_H4=<obf_h4 из awg1>
```

Jump-сервер берёт obf_s* и obf_h* из своей БД (awg1 интерфейс) и передаёт ноде.
Таким образом оба конца туннеля awg1 имеют одинаковые симметричные параметры.

## Команды разработки

```bash
# Локальная разработка
cd backend && uvicorn main:app --reload --port 8080

# Сборка основного образа
docker-compose up --build

# Сборка node образа отдельно
docker build -t awg-node ./node/

# Shell в контейнере
docker exec -it awg-jump bash

# Логи
docker logs -f awg-jump

# Обновление GeoIP
docker exec awg-jump python -m backend.services.geoip_fetcher --force

# Статус upstream нод
docker exec awg-jump python -m backend.services.node_deployer --status

# ipset
docker exec awg-jump ipset list geoip_ru | head -20

# AWG статус
docker exec awg-jump wg show
```

## Dockerfile — стратегия multi-stage сборки

**Хост чистый** — только `docker` и `docker-compose`. Всё остальное внутри контейнера.

```dockerfile
# Stage 1 — сборка amneziawg-go
FROM golang:1.22-alpine AS awg-builder
RUN apk add --no-cache git make
WORKDIR /build
RUN git clone https://github.com/amnezia-vpn/amneziawg-go.git .
RUN go build -o amneziawg-go ./...

# Stage 2 — сборка frontend
FROM node:20-alpine AS frontend-builder
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build

# Stage 3 — финальный образ
FROM debian:bookworm-slim AS final
RUN apt-get update && apt-get install -y --no-install-recommends \
    iproute2 iptables ipset wireguard-tools \
    curl ca-certificates openssh-client \
    python3 python3-pip supervisor procps net-tools \
    && rm -rf /var/lib/apt/lists/*

COPY --from=awg-builder /build/amneziawg-go /usr/local/bin/amneziawg-go
COPY --from=frontend-builder /frontend/dist /app/static
COPY backend/requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /app/requirements.txt
COPY backend/ /app/backend/
COPY scripts/ /app/scripts/
RUN chmod +x /app/scripts/*.sh
WORKDIR /app
EXPOSE 51820/udp 8080/tcp
ENTRYPOINT ["/app/scripts/entrypoint.sh"]
```

Обратить внимание: `openssh-client` добавлен в финальный образ — нужен для asyncssh как fallback.

### iptables legacy

```bash
# В entrypoint.sh перед настройкой правил:
update-alternatives --set iptables /usr/sbin/iptables-legacy
update-alternatives --set ip6tables /usr/sbin/ip6tables-legacy
```

### awg vs amneziawg-go

- `amneziawg-go <ifname>` — запускает TUN демон, создаёт `/var/run/wireguard/<ifname>.sock`
- `wg setconf awg0 /tmp/awg0.conf` — применить конфиг
- `wg syncconf awg0 /tmp/awg0.conf` — hot reload пиров
- `wg show` — статус всех интерфейсов

## Порядок реализации (этапы)

1. **Фундамент**: Dockerfile (3-stage), node/Dockerfile (2-stage), entrypoint, SQLite + Alembic
2. **AWG сервис**: amneziawg-go, awg0/awg1, генерация конфигов
3. **GeoIP + ipset**: fetcher, atomic swap, scheduler
4. **Маршрутизация**: ip rule/route, iptables mangle/nat
5. **FastAPI API**: все роутеры включая nodes.py
6. **Node Deployer**: asyncssh, деплой, health-check, failover
7. **Frontend**: React SPA включая страницу Nodes
8. **Бэкап**: экспорт/импорт
9. **Тестирование**: smoke tests

## Ограничения и известные нюансы

- **Хост чистый**: только docker на хосте
- nginx контейнер использует официальный `nginx:alpine` без кастомной сборки
- `generate-cert.sh` монтируется в nginx контейнер и запускается перед nginx
- Порт 8080 awg-jump НЕ должен быть в `ports:` секции compose — только внутри сети
- `asyncssh` используется вместо `paramiko` — нативный async, лучше для SSE стриминга
- SSH credentials никогда не логировать и не сохранять в БД
- awg-node образ публикуется в Docker Hub или передаётся через `docker save | ssh ... docker load`
  Рекомендуемый подход: `docker pull ghcr.io/yourname/awg-node:latest` на удалённом сервере
  Альтернатива если нет registry: передать tar через SSH, но это медленно
- При failover awg1 не перезапускается — только меняется peer endpoint через `wg set`
- ipset atomic swap обязателен (~7k RU префиксов)
- NODE_VPN_SUBNET — каждая нода получает уникальный /32 адрес из этой подсети
  awg-jump awg1: 10.20.0.2, node#1: 10.20.0.3, node#2: 10.20.0.4 и т.д.
