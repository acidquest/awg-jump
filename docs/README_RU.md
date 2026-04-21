# AWG Jump — Полная документация (RU)

## Содержание

1. [Обзор проекта](#обзор-проекта)
2. [AWG Gateway](#awg-gateway)
3. [Архитектура](#архитектура)
4. [Быстрый старт](#быстрый-старт)
5. [Переменные окружения](#переменные-окружения)
6. [Веб-интерфейс](#веб-интерфейс)
7. [AmneziaWG и обфускация](#amneziawg-и-обфускация)
8. [GeoIP и маршрутизация](#geoip-и-маршрутизация)
9. [Split DNS](#split-dns)
10. [Upstream-ноды и failover](#upstream-ноды-и-failover)
11. [Бэкап и восстановление](#бэкап-и-восстановление)
12. [TLS и веб-доступ](#tls-и-веб-доступ)
13. [Структура проекта](#структура-проекта)
14. [Разработка и отладка](#разработка-и-отладка)

---

## Обзор проекта

**AWG Jump** — контейнеризированный jump-сервер на базе [AmneziaWG](https://github.com/amnezia-vpn/amneziawg-go) (форк WireGuard с DPI-обфускацией). Реализует политику раздельной маршрутизации трафика:

- **Российские IP-адреса** → физический интерфейс хоста (`eth0`) — прямой выход без VPN.
- **Остальной трафик** → `awg1` — upstream-нода (зарубежный VPS).

Дополнительно предоставляет **Split DNS**: клиенты получают встроенный DNS-сервер, который направляет запросы к доменам локальной зоны через настраиваемые DNS-серверы local zone, а остальные — через настраиваемые DNS-серверы VPN zone.

В репозитории два Docker-образа:

| Образ | Назначение |
|-------|-----------|
| `awg-jump` | Основной сервер: AWG + FastAPI + React SPA + GeoIP + routing + DNS + SSH deployer |
| `awg-node` | Минималистичный upstream-узел: только `amneziawg-go` |

---

## AWG Gateway

В репозитории также есть отдельный блок `gateway/` для сценария standalone-шлюза. Это автономный Linux-only gateway-контейнер, который импортирует peer-конфиг entry node из `awg-jump`, поднимает локальный AWG tunnel runtime, применяет policy routing и split DNS, но не управляет самой upstream-нодой напрямую.

Основные документы по шлюзу:

- [Полная документация AWG Gateway (RU)](../gateway/docs/README_RU.md)
- [Full AWG Gateway documentation (EN)](../gateway/docs/README_EN.md)
- [API по ключу для телеметрии и управления](../gateway/docs/api_access_ru.md)

---

## Архитектура

```
Браузер (WEB_PORT, по умолчанию HTTPS:8080)
    │
    ▼
┌─────────────────────────────────────────────┐
│              awg-jump :WEB_PORT             │
│                                             │
│  FastAPI + SQLite + APScheduler             │
│  uvicorn (http или https)                   │
│  amneziawg-go (awg0 + awg1)                │
│  dnsmasq (split DNS)                        │
│  ipset geoip_local + iptables policy routing│
└──────────────────────────────────────────────┘
       │ awg0 UDP:51820          │ awg1 → upstream
       │                         │
  AWG-клиенты              ┌─────┴──────┐
  (телефон, ПК)            │  awg-node  │ VPS #1 (активная)
                           └────────────┘
                           ┌────────────┐
                           │  awg-node  │ VPS #2 (резервная)
                           └────────────┘
```

### Потоки трафика

```
Клиент → awg0 → iptables mangle PREROUTING:
    dst в ipset geoip_local  →  fwmark LOCAL →  table 100  →  eth0 (прямой)
    dst не в geoip_local     →  fwmark VPN →  table 200  →  awg1 (VPN)

Контейнер (DNS-запросы dnsmasq) → iptables mangle OUTPUT:
    dst в ipset geoip_local  →  fwmark LOCAL →  table 100  →  eth0
    dst не в geoip_local     →  fwmark VPN →  table 200  →  awg1
```

---

## Быстрый старт

### Требования

- Docker Engine 24+
- docker compose v2
- Открытые порты: `WEB_PORT/tcp` (по умолчанию `8080/tcp`) и `51820/udp` (AWG)

### Установка

```bash
# 1. Клонировать репозиторий
git clone <repo-url> awg-jump && cd awg-jump

# 2. Создать конфигурацию
cp .env.ru.example .env

# 3. Обязательно изменить:
#   ADMIN_PASSWORD=<сильный пароль>
#   SECRET_KEY=<случайная строка 32+ символов>
#   TLS_COMMON_NAME=<IP или hostname сервера>
#   SERVER_HOST=<публичный IP для Endpoint в конфигах клиентов>
nano .env

# 4. Запустить
docker compose up -d --build

# 5. Открыть веб-интерфейс
https://<SERVER_HOST>:<WEB_PORT>
```

Браузер предупредит о self-signed сертификате — это ожидаемо. Добавь исключение или установи сертификат вручную.

### Первый запуск

При первом старте контейнер автоматически:

1. Применяет миграции базы данных (Alembic).
2. Создаёт интерфейсы `awg0` (сервер) и `awg1` (клиент).
3. Генерирует AWG-ключи для обоих интерфейсов.
4. Генерирует параметры обфускации AmneziaWG.
5. Загружает GeoIP-кэш всех enabled стран в ipset `geoip_local`.
6. Настраивает policy routing и iptables.
7. Запускает dnsmasq со списком дефолтных RU-доменов.
8. Запускает APScheduler (GeoIP cron, health-check, peer stats).

---

## Переменные окружения

### Web / TLS

| Переменная | По умолчанию | Описание |
|-----------|-------------|----------|
| `WEB_MODE` | `https` | Режим веб-сервера: `http` или `https` |
| `WEB_PORT` | `8080` | TCP-порт UI/API |
| `TLS_COMMON_NAME` | `localhost` | CN и SAN self-signed сертификата (IP или hostname) |
| `TLS_CERT_PATH` | `/data/certs/server.crt` | Путь к сертификату |
| `TLS_KEY_PATH` | `/data/certs/server.key` | Путь к приватному ключу |

### Администратор

| Переменная | По умолчанию | Описание |
|-----------|-------------|----------|
| `ADMIN_USERNAME` | `admin` | Логин администратора |
| `ADMIN_PASSWORD` | `changeme` | **Обязательно изменить** |
| `SECRET_KEY` | `insecure-default` | Секрет подписи токенов (**обязательно изменить**) |
| `SESSION_TTL_HOURS` | `8` | Время жизни сессии в часах |

### Сервер AWG0 (принимает клиентов)

| Переменная | По умолчанию | Описание |
|-----------|-------------|----------|
| `AWG0_LISTEN_PORT` | `51820` | UDP-порт сервера |
| `AWG0_PRIVATE_KEY` | _(авто)_ | Приватный ключ; если пусто — генерируется автоматически |
| `AWG0_ADDRESS` | `10.10.0.1/24` | Адрес интерфейса awg0 (он же IP dnsmasq для клиентов) |
| `AWG0_DNS` | _(awg0 IP)_ | DNS для клиентов; автоматически выставляется в IP awg0 |
| `SERVER_HOST` | `` | Публичный IP/hostname для `Endpoint` в конфигах клиентов |

### Клиент AWG1 (upstream VPN)

| Переменная | По умолчанию | Описание |
|-----------|-------------|----------|
| `AWG1_ADDRESS` | `10.20.0.2/32` | Адрес awg1 в VPN-подсети |
| `AWG1_ALLOWED_IPS` | `0.0.0.0/0` | Разрешённые адреса через awg1 |
| `AWG1_PERSISTENT_KEEPALIVE` | `25` | Keepalive в секундах |
| `AWG1_ENDPOINT` | _(авто)_ | Автозаполняется при активации ноды |

### Маршрутизация

| Переменная | По умолчанию | Описание |
|-----------|-------------|----------|
| `PHYSICAL_IFACE` | `eth0` | Физический интерфейс для local-zone трафика |
| `ROUTING_TABLE_LOCAL` | `100` | Таблица маршрутизации для local-zone трафика |
| `ROUTING_TABLE_VPN` | `200` | Таблица маршрутизации для VPN-трафика |
| `FWMARK_LOCAL` | `0x1` | fwmark для пакетов local zone |
| `FWMARK_VPN` | `0x2` | fwmark для VPN-пакетов |

### GeoIP

| Переменная | По умолчанию | Описание |
|-----------|-------------|----------|
| `GEOIP_SOURCE` | ipdeny.com | Базовый URL источника GeoIP; итоговый URL строится как `<base><country_code>.zone` |
| `GEOIP_UPDATE_CRON` | `0 4 * * *` | Cron обновления (UTC) |
| `GEOIP_FETCH_TIMEOUT` | `30` | Таймаут загрузки в секундах |

### Upstream-ноды

| Переменная | По умолчанию | Описание |
|-----------|-------------|----------|
| `NODE_HEALTH_CHECK_INTERVAL` | `30` | Интервал проверки здоровья (сек) |
| `NODE_HEALTH_CHECK_TIMEOUT` | `5` | Таймаут одной проверки (сек) |
| `NODE_FAILOVER_THRESHOLD` | `3` | Кол-во неудач до переключения ноды |
| `NODE_AWG_PORT` | `51821` | UDP-порт AWG на удалённых нодах |
| `NODE_VPN_SUBNET` | `10.20.0.0/24` | Подсеть для связи jump ↔ ноды |

---

## Веб-интерфейс

Открывается по `https://<SERVER_HOST>:<WEB_PORT>` или `http://<SERVER_HOST>:<WEB_PORT>` в зависимости от `WEB_MODE`. Все страницы требуют авторизации.

### Dashboard

Сводная панель: статус интерфейсов, подключённые пиры, активная upstream-нода, состояние GeoIP, routing, dnsmasq.

### Interfaces

Управление AWG-интерфейсами `awg0` и `awg1`:

- Просмотр ключей, адресов, портов.
- Редактирование параметров (listen port, address, DNS, keepalive).
- Кнопки **Apply** (перезапуск интерфейса) и **Stop**.
- Блок параметров обфускации AmneziaWG (Jc, Jmin, Jmax, S1–S4, H1–H4).
- Кнопка **Regenerate** — генерирует новые параметры обфускации. **Внимание:** после регенерации все клиенты должны получить новые конфигурации.

### Peers

Управление клиентами awg0:

- Создание пиров с автоматическим выделением IP из подсети awg0.
- Скачивание конфига в формате `.conf` и QR-кода для мобильных приложений.
- Включение/отключение пиров без перезапуска интерфейса (hot reload через `wg syncconf`).
- Live-статистика: последний handshake, RX/TX байт.
- Маркировка типа клиента (`awg-gateway`, Android, iOS) по tunnel status API.

#### Tunnel status API для клиентов

Без авторизации доступен endpoint:

`POST /api/peers/status`

Payload:

```json
{"client_code": 1001}
```

Поддерживаемые коды:

- `1001` — `awg-gateway`
- `1002` — `awg-jump-client-android`
- `1003` — `awg-jump-client-ios`

IP клиента берётся из запроса и сопоставляется с `tunnel_address` peer на `awg0`. Endpoint принимает данные только от известных tunnel IP.

### Nodes

Управление upstream-нодами:

- Добавление VPS с указанием SSH credentials (не сохраняются).
- Деплой `awg-node` по SSH: установка Docker, сборка образа, запуск контейнера.
- Кнопка **Add node** для добавления manual-ноды из стандартного AWG peer `.conf`.
- Потоковый вывод лога деплоя (SSE).
- Переключение активной ноды, просмотр метрик (latency, RX/TX).
- Автоматический failover при деградации активной ноды.
- Для managed-ноды: таблица shared peers, экспорт peer в `.conf`, применение изменений при `Redeploy`.
- Для manual-ноды: нет `Redeploy`, `Deploy history` и управления peer'ами.

### Settings

Новая страница **Settings** позволяет:

- сменить пароль администратора;
- загрузить свой TLS certificate/key;
- переключить `http`/`https`;
- изменить `WEB_PORT`.

Параметры сохраняются в `.env`. Для изменения транспорта, порта и сертификата нужен рестарт контейнера.

### Routing

Просмотр текущего состояния политики маршрутизации:

- ip rules (fwmark → таблица).
- ip routes в таблицах LOCAL и VPN.
- Состояние iptables правил (PREROUTING, OUTPUT, NAT).
- Кнопки **Apply** (пересоздать правила) и **Reset** (удалить).

### GeoIP

- Статус ipset `geoip_local`: количество префиксов, дата обновления.
- Управление списком стран локальной зоны: RU, BY, KZ и т.д.
- Добавление, редактирование и удаление enabled GeoIP-источников через UI.
- URL источника для страны строится автоматически по `country_code`, при необходимости может быть переопределён.
- Ручное обновление объединённого GeoIP ipset.

### Split DNS

- Статус dnsmasq: запущен/остановлен, PID, адрес прослушивания.
- Настройки DNS зон: `Local Zone DNS` и `VPN Zone DNS`, редактирование списка DNS-серверов inline.
- Список доменов: домен, upstream (`Local Zone` / `VPN Zone`), включён/выключен.
- Добавление доменов через форму (TLD, домен или поддомен).
- Включение/отключение отдельных доменов без перезагрузки.
- Кнопка **Reload dnsmasq** — принудительная перезагрузка конфига.

### Backup

- Скачивание ZIP-архива с `config.db` (все данные) + `env_snapshot.json` + `wg_configs/` + `geoip_cache/`.
- Загрузка архива для восстановления (drag & drop или кнопка).
- После восстановления — перезапуск контейнера (`docker compose restart awg-jump`).

---

## AmneziaWG и обфускация

AmneziaWG — форк WireGuard с поддержкой Junk-пакетов и заменой заголовков для обхода DPI.

### Рекомендуемый режим для первой входящей ноды

Для первой входящей ноды `awg-jump` и для upstream-нод на VPS предпочтителен kernel mode на хостовой машине, если установка kernel module возможна. Этот режим на практике стабильнее, чем userspace `amneziawg-go`, и избавляет от части проблем с `tun`, userspace daemon и ложной детекцией поддержки ядра.

Перед установкой модуля обязательно нужно установить headers именно для текущего ядра:

```bash
apt-get update
apt-get install --yes linux-headers-$(uname -r) dkms build-essential
```

После этого можно установить `amneziawg` и `amneziawg-tools`.

Debian 13 (Trixie, deb822):

```bash
sudo apt-get install --yes gnupg2 apt-transport-https
sudo apt-get install --yes linux-headers-$(uname -r) dkms build-essential
gpg --keyserver keyserver.ubuntu.com --recv-keys 75c9dd72c799870e310542e24166f2c257290828
gpg --export 75c9dd72c799870e310542e24166f2c257290828 | sudo tee /usr/share/keyrings/amnezia.gpg > /dev/null
sudo tee /etc/apt/sources.list.d/amnezia.sources <<EOF
Types: deb deb-src
URIs: https://ppa.launchpadcontent.net/amnezia/ppa/ubuntu
Suites: focal
Components: main
Signed-By: /usr/share/keyrings/amnezia.gpg
EOF
sudo apt-get update
sudo apt-get install --yes amneziawg amneziawg-tools
```

Debian 12 (Bookworm, traditional format):

```bash
sudo apt-get install --yes gnupg2 apt-transport-https
sudo apt-get install --yes linux-headers-$(uname -r) dkms build-essential
gpg --keyserver keyserver.ubuntu.com --recv-keys 75c9dd72c799870e310542e24166f2c257290828
gpg --export 75c9dd72c799870e310542e24166f2c257290828 | sudo tee /usr/share/keyrings/amnezia.gpg > /dev/null
echo "deb [signed-by=/usr/share/keyrings/amnezia.gpg] https://ppa.launchpadcontent.net/amnezia/ppa/ubuntu focal main" | sudo tee -a /etc/apt/sources.list.d/amnezia.list
echo "deb-src [signed-by=/usr/share/keyrings/amnezia.gpg] https://ppa.launchpadcontent.net/amnezia/ppa/ubuntu focal main" | sudo tee -a /etc/apt/sources.list.d/amnezia.list
sudo apt-get update
sudo apt-get install --yes amneziawg amneziawg-tools
```

Проверка после установки:

```bash
sudo modprobe amneziawg
ip link add awg_probe type amneziawg && ip link del awg_probe
```

Для upstream-ноды готовые скрипты лежат в `node/scripts/` и при SSH-деплое попадают на хост в `/opt/awg-node/scripts/`.

### Параметры

| Параметр | Сторона | Описание |
|---------|---------|----------|
| `Jc` | Клиент | Количество junk-пакетов перед handshake (4–12) |
| `Jmin` | Клиент | Минимальный размер junk-пакета (40–80 байт) |
| `Jmax` | Клиент | Максимальный размер junk-пакета (< 1280 байт) |
| `S1`–`S4` | Оба | Padding в handshake и транспортных пакетах |
| `H1`–`H4` | Оба | Замена стандартных заголовков WireGuard |

**Важно:**

- `Jc`, `Jmin`, `Jmax` указываются только на клиентской стороне.
- `S1–S4`, `H1–H4` должны совпадать на обоих концах туннеля.
- `H1–H4` должны быть уникальными и не равными стандартным значениям WG (1, 2, 3, 4).
- Параметры генерируются автоматически и хранятся в `config.db`. Включаются в бэкап.

### Два набора параметров

- **awg0** (сервер ↔ клиенты): параметры `S*` и `H*` — в конфиге сервера; `Jc/Jmin/Jmax + S* + H*` — в конфиге клиента.
- **awg1** (jump → upstream нода): `Jc/Jmin/Jmax + S* + H*` — в `[Interface]` awg1 (jump — клиент); только `S* + H*` — в конфиге ноды (нода — сервер).

---

## GeoIP и маршрутизация

### Как работает

1. При старте преднастроенные GeoIP-страны не создаются; local zone настраивается пользователем через UI/API.
2. По расписанию `GEOIP_UPDATE_CRON` и при ручном запуске загружаются все enabled GeoIP-источники из БД.
3. Для каждой страны URL строится автоматически по `country_code` на основе `GEOIP_SOURCE`, если пользователь не задал явный `url`.
4. Все CIDR-блоки объединяются в один ipset `geoip_local` (atomic swap — без разрыва соединений).
5. iptables mangle **PREROUTING** маркирует входящие от `awg0` пакеты:
   - dst в `geoip_local` → `fwmark LOCAL` → таблица 100 → `eth0`
   - dst не в `geoip_local` → `fwmark VPN` → таблица 200 → `awg1`
6. iptables mangle **OUTPUT** маркирует трафик самого контейнера (DNS-запросы и т.д.) по тем же правилам.
7. `iptables nat POSTROUTING MASQUERADE` обеспечивает NAT на обоих исходящих интерфейсах.

### Инверсия маршрутизации

На странице **Routing** есть флаг `invert_geoip`.

- `invert_geoip = false` (Normal): GeoIP local zone идёт напрямую через `eth0`, остальной трафик уходит в `awg1`.
- `invert_geoip = true` (Inverted): логика меняется местами, то есть GeoIP local zone уходит в `awg1`, а остальной трафик идёт напрямую через `eth0`.

Это влияет и на трафик клиентов из `awg0`, и на исходящий трафик самого контейнера, включая DNS-запросы `dnsmasq`.

### Обновление GeoIP

```bash
# Принудительное обновление из UI: страница GeoIP → кнопка Update

# Принудительное обновление из CLI:
docker exec awg-jump python -m backend.services.geoip_fetcher --force

# Просмотр содержимого ipset:
docker exec awg-jump ipset list geoip_local | head -20
```

---

## Split DNS

### Принцип работы

AWG Jump запускает `dnsmasq` непосредственно в контейнере. Клиенты AWG автоматически получают IP интерфейса `awg0` как DNS-сервер (прописывается в генерируемые конфиги).

```
Клиент → AWG DNS (10.10.0.1:53) → dnsmasq:
    домен в списке (upstream=yandex / Local Zone) → DNS серверы local zone
    всё остальное                                 → DNS серверы vpn zone
```

DNS-трафик самого контейнера (запросы dnsmasq к upstream DNS) маршрутизируется через iptables OUTPUT по тому же ipset `geoip_local`: IP локальной зоны идут через `eth0`, остальные — через `awg1`.

### Дефолтные домены

При первом старте создаются:

```
ru, рф
yandex.ru, yandex.net, yandex.com, ya.ru
vk.com, vk.ru, vkontakte.ru
mail.ru, list.ru, inbox.ru, bk.ru
ok.ru, rambler.ru
sberbank.ru, sbrf.ru, sber.ru
gosuslugi.ru, mos.ru
tinkoff.ru, avito.ru, ozon.ru, wildberries.ru
```

### Добавление доменов

Через веб-интерфейс (страница **Split DNS**) или API:

```bash
# Добавить домен через API
curl -X POST https://<host>/api/dns/domains \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"domain": "example.ru", "upstream": "yandex"}'
```

Значения `upstream`:
- `yandex` — использовать DNS серверы local zone
- `default` — использовать DNS серверы vpn zone

Строковые значения `yandex` и `default` сохранены в БД для обратной совместимости, но в UI отображаются как `Local Zone` и `VPN Zone`.

### Перезагрузка конфига

При любом изменении доменов или DNS zone settings конфиг dnsmasq перегенерируется автоматически и перезагружается через `SIGHUP` (без прерывания соединений).

### DNS Zone Settings

Настройки хранятся в отдельной таблице `dns_zone_settings`:

- `local` — DNS-серверы для доменов локальной зоны.
- `vpn` — DNS-серверы для всего остального трафика.

API:

```bash
# Получить настройки зон
curl -H "Authorization: Bearer <token>" \
  https://<host>/api/dns/zones

# Обновить local zone DNS
curl -X PUT https://<host>/api/dns/zones/local \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"dns_servers":["77.88.8.8"],"description":"DNS for local routing zone"}'
```

### Конфиг dnsmasq

Генерируется в `/etc/dnsmasq-awg.conf`:

```ini
listen-address=10.10.0.1,127.0.0.1
bind-interfaces
no-resolv
cache-size=2000
server=1.1.1.1
server=8.8.8.8
# Local zone domains
server=/ru/77.88.8.8
server=/yandex.ru/77.88.8.8
# ...
```

---

## Upstream-ноды и failover

### Модель нод

Каждая нода хранится в таблице `upstream_nodes`:

- `host` — IP или hostname VPS
- `awg_port` — UDP-порт AWG на ноде (по умолчанию `51821`)
- `awg_address` — IP ноды в VPN-подсети (напр. `10.20.0.3/32`)
- `status` — `pending | deploying | online | degraded | offline | error`
- `is_active` — только одна нода активна одновременно
- `priority` — порядок failover

### Деплой ноды

Нода разворачивается из веб-интерфейса по SSH:

1. Jump-сервер упаковывает папку `node/` в tar и передаёт на VPS через SSH pipe.
2. На VPS устанавливается Docker, собирается образ `awg-node:local`.
3. Генерируются AWG-ключи и конфиг для ноды (с параметрами обфускации awg1).
4. Запускается контейнер с нодой.

SSH credentials (логин/пароль) **не сохраняются** нигде.

### Failover

APScheduler проверяет каждые `NODE_HEALTH_CHECK_INTERVAL` секунд:

- Если у активной ноды `last_handshake > 3 мин` → статус `degraded`, счётчик +1.
- При `счётчик >= NODE_FAILOVER_THRESHOLD` → автопереключение на следующую ноду по `priority`.
- Переключение: `wg set awg1 peer <pubkey> endpoint <new_host:port>` — без перезапуска интерфейса.

---

## Бэкап и восстановление

### Что включено в бэкап

| Содержимое | Описание |
|-----------|----------|
| `config.db` | Все данные: интерфейсы, ключи, обфускация, пиры, ноды, GeoIP-источники, правила маршрутизации, **split DNS домены и DNS zone settings** |
| `env_snapshot.json` | Справочный снимок публичных параметров (без паролей) |
| `wg_configs/` | Сгенерированные WireGuard конфиги |
| `geoip_cache/` | Кэшированные CIDR-списки GeoIP для быстрого восстановления ipset после рестарта |
| `certs/` | TLS-сертификаты FastAPI из `/data/certs` |

**Что НЕ включено:**
- `.env` файл (пароли и ключи окружения)

### Экспорт

```bash
# Через UI: страница Backup → кнопка Download backup

# Через API:
curl -O -J https://<host>/api/backup/export \
  -H "Authorization: Bearer <token>"
```

### Импорт и восстановление

1. Страница **Backup** → загрузить `.zip` файл.
2. После импорта split DNS перезагружается автоматически.
3. Перезапустить контейнер: `docker compose restart awg-jump`
4. Alembic миграции применятся при старте.

### Автоматический бэкап

Каждый экспорт автоматически сохраняется в `/data/backups/` внутри контейнера. Список доступен через `GET /api/backup/list`.

---

## TLS и веб-доступ

`awg-jump` сам обслуживает UI/API через `uvicorn`. По умолчанию используется `https://0.0.0.0:${WEB_PORT}` с self-signed сертификатом.

### Self-signed сертификат

Сертификат генерируется скриптом `nginx/generate-cert.sh` при первом старте основного контейнера:

```bash
openssl req -x509 -newkey rsa:4096 -sha256 -days 3650 \
  -nodes -keyout /certs/server.key -out /certs/server.crt \
  -subj "/CN=${TLS_COMMON_NAME}" \
  -addext "subjectAltName=IP:${TLS_COMMON_NAME},DNS:${TLS_COMMON_NAME}"
```

Срок действия — 10 лет. Хранится в `./data/certs/` (volume). Если файл уже существует — не пересоздаётся.

### Пересоздание сертификата

```bash
rm ./data/certs/server.crt ./data/certs/server.key
docker compose restart awg-jump
```

---

## Структура проекта

```
awg-jump/
├── .env.ru.example         # Шаблон конфигурации (RU)
├── .env.en.example         # Configuration template (EN)
├── docker-compose.yml      # awg-jump + awg-node (optional)
├── Dockerfile              # Multi-stage: awg-builder + awg-tools + python + frontend + final
├── supervisord.conf        # Управляет uvicorn внутри контейнера
│
├── nginx/
│   └── generate-cert.sh    # Переиспользуемая генерация self-signed cert
│
├── node/                   # Образ upstream-ноды
│   ├── Dockerfile          # 2-stage: awg-builder + debian-slim
│   ├── entrypoint.sh       # Запуск AWG из env-переменных
│   └── README.md
│
├── backend/
│   ├── main.py             # FastAPI app + lifespan
│   ├── config.py           # Pydantic Settings из .env
│   ├── database.py         # SQLAlchemy async + WAL mode
│   ├── scheduler.py        # APScheduler задачи
│   ├── models/
│   │   ├── interface.py    # AWG интерфейсы + obf_* параметры
│   │   ├── peer.py         # AWG пиры
│   │   ├── upstream_node.py # Upstream ноды + deploy logs
│   │   ├── geoip.py        # Источники GeoIP + display_name
│   │   ├── routing_rule.py # Правила маршрутизации
│   │   ├── dns_domain.py   # Split DNS домены
│   │   └── dns_zone_settings.py # DNS серверы local/vpn зон
│   ├── routers/
│   │   ├── auth.py         # JWT авторизация
│   │   ├── interfaces.py   # API интерфейсов
│   │   ├── peers.py        # API пиров
│   │   ├── nodes.py        # API нод + деплой + SSE
│   │   ├── routing.py      # API маршрутизации
│   │   ├── geoip.py        # API GeoIP и управление странами локальной зоны
│   │   ├── dns.py          # API split DNS и DNS zone settings
│   │   ├── system.py       # Системная информация
│   │   └── backup.py       # Экспорт/импорт
│   ├── services/
│   │   ├── awg.py          # Управление amneziawg-go
│   │   ├── routing.py      # ip rule/route + iptables
│   │   ├── ipset_manager.py # Управление ipset
│   │   ├── geoip_fetcher.py # Загрузка GeoIP и агрегация в geoip_local
│   │   ├── node_deployer.py # SSH деплой нод
│   │   └── dns_manager.py  # Управление dnsmasq
│   └── alembic/            # Миграции БД
│       └── versions/
│           ├── 0001_initial_schema.py
│           ├── 0002_peer_private_key.py
│           ├── 0003_node_private_key.py
│           ├── 0004_dns_domains.py
│           ├── 0005_geoip_local_multi_country.py
│           └── 0006_dns_zone_settings.py
│
├── frontend/
│   ├── src/
│   │   ├── App.tsx         # React Router + QueryClient
│   │   ├── api.ts          # Axios API wrapper
│   │   ├── types.ts        # TypeScript типы
│   │   ├── pages/
│   │   │   ├── Dashboard.tsx
│   │   │   ├── Interfaces.tsx
│   │   │   ├── Peers.tsx
│   │   │   ├── Nodes.tsx
│   │   │   ├── Routing.tsx
│   │   │   ├── GeoIP.tsx
│   │   │   ├── DNS.tsx     # Split DNS управление
│   │   │   └── Backup.tsx
│   │   └── components/
│   │       ├── Layout.tsx  # Sidebar навигация
│   │       ├── Modal.tsx
│   │       └── StatusBadge.tsx
│   └── public/
│       └── favicon.svg
│
├── scripts/
│   ├── entrypoint.sh       # Стартап контейнера
│   └── smoke_test.sh       # Базовые тесты
│
└── docs/
    ├── README_RU.md        # Эта документация
    └── README_EN.md        # English documentation
```

---

## Разработка и отладка

### Локальный запуск backend

```bash
cd /opt/awg-jump
pip install -r backend/requirements.txt
cd backend && uvicorn main:app --reload --port 8080
```

### Сборка и запуск

```bash
# Полный стек
docker compose up --build

# Только пересобрать awg-jump
docker compose build awg-jump && docker compose up -d awg-jump

# Пересобрать frontend
docker compose build awg-jump  # frontend собирается в Dockerfile
```

### Отладка

```bash
# Логи awg-jump
docker logs -f awg-jump

# Shell в контейнере
docker exec -it awg-jump bash

# Статус AWG интерфейсов
docker exec awg-jump wg show

# Статус ipset
docker exec awg-jump ipset list geoip_local | head -20

# Статус ip rule / routes
docker exec awg-jump ip rule show
docker exec awg-jump ip route show table 100
docker exec awg-jump ip route show table 200

# Статус iptables
docker exec awg-jump iptables -t mangle -L -v -n
docker exec awg-jump iptables -t nat -L -v -n

# Статус dnsmasq
docker exec awg-jump cat /etc/dnsmasq-awg.conf
docker exec awg-jump cat /etc/resolv.conf

# Принудительное обновление GeoIP
docker exec awg-jump python -m backend.services.geoip_fetcher --force

# Статус upstream нод
docker exec awg-jump python -m backend.services.node_deployer --status
```

### API

Все эндпоинты доступны по `https://<host>/api/`. Документация (если включена):

```bash
ENABLE_API_DOCS=true  # в .env
# Затем: https://<host>/api/docs
```

### База данных

```bash
# Просмотр БД
docker exec awg-jump sqlite3 /data/config.db ".tables"
docker exec awg-jump sqlite3 /data/config.db "SELECT domain, upstream, enabled FROM dns_domains;"

# Бэкап БД напрямую
docker exec awg-jump sqlite3 /data/config.db ".backup /data/config.db.manual"
```

### Smoke-тест

```bash
./scripts/smoke_test.sh
```

---

*AWG Jump использует [AmneziaWG](https://github.com/amnezia-vpn/amneziawg-go) — форк WireGuard с поддержкой обфускации трафика.*
