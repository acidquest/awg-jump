# AWG Gateway — Полная документация (RU)

## Содержание

1. [Обзор](#обзор)
2. [Архитектура](#архитектура)
3. [Функциональность](#функциональность)
4. [Требования и ограничения](#требования-и-ограничения)
5. [Способы развёртывания](#способы-развёртывания)
6. [Переменные окружения](#переменные-окружения)
7. [Веб-интерфейс](#веб-интерфейс)
8. [Маршрутизация, DNS и runtime](#маршрутизация-dns-и-runtime)
9. [Entry nodes и bootstrap первой ноды](#entry-nodes-и-bootstrap-первой-ноды)
10. [API](#api)
11. [Многоязычность](#многоязычность)
12. [Бэкап, восстановление и диагностика](#бэкап-восстановление-и-диагностика)
13. [Структура gateway-блока](#структура-gateway-блока)
14. [Проверка и эксплуатация](#проверка-и-эксплуатация)

## Обзор

`AWG Gateway` в каталоге [`gateway/`](../README.md) — это автономный Linux-only шлюз внутри репозитория `awg-jump`. Он предназначен для сценария, где сам входной узел уже существует, а шлюзу нужно:

- импортировать peer-конфиг entry node из `.conf`, экспортированного из `awg-jump`;
- поднять локальный tunnel runtime на базе AmneziaWG;
- применить policy routing между host uplink и AWG-туннелем;
- перехватывать DNS выбранных источников и наполнять prefix set по FQDN;
- отдавать операторский UI и отдельный key-based API для телеметрии и ограниченного remote control.

`gateway` не меняет входную ноду, не требует tunnel API от `awg-jump` и не строит поверх туннеля отдельный control plane.

Основные связные документы:

- [Краткий README gateway](../README.md)
- [API доступа по ключу](api_access_ru.md)
- [Формат i18n-модулей](i18n_ru.md)

## Архитектура

```text
Browser
  -> http://<gateway-host>:8081
  -> FastAPI + SPA
  -> SQLite (/data/gateway.db)
  -> dnsmasq
  -> iptables/ipset или nftables/nft set
  -> amneziawg-go или kernel amneziawg
  -> host network namespace
```

Ключевые компоненты:

- backend: [`gateway/backend/app`](../backend/app) на FastAPI + SQLAlchemy + SQLite;
- frontend: [`gateway/frontend`](../frontend) на React/Vite;
- tunnel runtime: сервис [`services/runtime.py`](../backend/app/services/runtime.py);
- routing и firewall backend: [`services/routing.py`](../backend/app/services/routing.py);
- split DNS runtime: [`services/dns_runtime.py`](../backend/app/services/dns_runtime.py);
- failover: [`services/failover.py`](../backend/app/services/failover.py);
- backup/diagnostics: [`services/backup.py`](../backend/app/services/backup.py).

Контейнер работает в `network_mode: host`, поэтому применяет маршрутизацию и NAT прямо в сетевом namespace хоста.

## Функциональность

### Базовые возможности

- операторская web-аутентификация с локальной сменой пароля;
- хранение списка entry nodes в SQLite;
- импорт peer `.conf` и визуальное редактирование node-параметров;
- ручная активация active node;
- автоподнятие туннеля при старте контейнера, если ранее была выбрана активная нода;
- работа в `auto`, `kernel` или `userspace` runtime mode;
- latency probe, UDP probe и статус live-туннеля;
- policy routing для выбранных source CIDR;
- kill switch;
- переключение firewall backend между `iptables + ipset` и `nftables + nft set`;
- GeoIP-префиксы, ручные CIDR и FQDN prefixes;
- split DNS через локальный `dnsmasq`;
- внешний IP для local path и VPN path;
- автоматический failover на следующую entry node;
- backup/export, restore и diagnostics bundle;
- bootstrap первой upstream-ноды по SSH;
- UI на английском и русском;
- отдельный `X-API-Key` API для телеметрии и ограниченного управления.

### Что не входит в scope

- изменение конфигурации удалённой entry node после импорта;
- общий control plane `awg-jump <-> gateway` поверх туннеля;
- удалённое управление всеми настройками через key-based API;
- не-Linux runtime.

## Требования и ограничения

- Linux host;
- Docker Engine и `docker compose`;
- `NET_ADMIN`, `NET_RAW`;
- устройство `/dev/net/tun`;
- включённый `net.ipv4.ip_forward=1` на хосте;
- доступ контейнера к host networking;
- IPv4-first сценарий для routing policy и API CIDR-ограничений;
- `dnsmasq`, `iptables`, `ipset`, `nftables`, `iproute2`, `ping`, `awg`, `amneziawg-go` находятся внутри образа.

Важно:

- контейнер нельзя безопасно запускать в bridge-сети, если нужен реальный gateway runtime;
- sysctl `net.ipv4.ip_forward=1` при `network_mode: host` надо выставлять на хосте, а не внутри compose;
- API docs (`/api/docs`, `/api/openapi.json`) по умолчанию выключены и включаются только через `ALLOW_API_DOCS=true`.

## Способы развёртывания

### 1. Локальная сборка и запуск из репозитория

```bash
cp gateway/.env.example gateway/.env
docker compose -f gateway/docker-compose.yml up -d --build
```

UI будет доступен на:

```text
http://<host>:${GATEWAY_WEB_PORT:-8081}
```

Это основной и полностью описанный в репозитории способ.

### 2. Сборка standalone-образа вручную

Из корня репозитория:

```bash
docker build -f gateway/Dockerfile -t awg-gateway:local .
docker run --rm \
  --network host \
  --cap-add NET_ADMIN \
  --cap-add NET_RAW \
  --device /dev/net/tun:/dev/net/tun \
  -e ADMIN_USERNAME=admin \
  -e ADMIN_PASSWORD=change-me \
  -e WEB_PORT=8081 \
  -v "$(pwd)/gateway/data:/data" \
  awg-gateway:local
```

### 3. Публикация gateway-образа в Docker Hub

В репозитории есть скрипт [`scripts/publish_dockerhub.sh`](../../scripts/publish_dockerhub.sh), который умеет собирать и публиковать gateway-образ:

```bash
./scripts/publish_dockerhub.sh <namespace> <tag> --gateway
./scripts/publish_dockerhub.sh <namespace> <tag> --only-gateway
```

Публикуемый тег:

```text
docker.io/<namespace>/awg-gateway:<tag>
```

После публикации можно использовать собственный compose/docker run с теми же capability, volume и host networking, что и в [`gateway/docker-compose.yml`](../docker-compose.yml).

## Переменные окружения

Публичные runtime settings описаны в [`gateway/backend/app/config.py`](../backend/app/config.py). Минимальный пример env сейчас лежит в [`gateway/.env.example`](../.env.example).

### Базовые

| Переменная | По умолчанию | Назначение |
|---|---:|---|
| `ADMIN_USERNAME` | `admin` | Логин оператора |
| `ADMIN_PASSWORD` | `changeme` | Пароль оператора при bootstrap |
| `WEB_PORT` | `8081` | HTTP-порт UI/API |
| `ALLOW_API_DOCS` | `false` | Включает `/api/docs`, `/api/redoc`, `/api/openapi.json` |
| `SESSION_TTL_HOURS` | `8` | Время жизни web-session |
| `UI_DEFAULT_LANGUAGE` | `en` | Язык bootstrap-настроек |

### Пути и данные

| Переменная | По умолчанию |
|---|---|
| `DATA_DIR` | `/data` |
| `DB_PATH` | `/data/gateway.db` |
| `BACKUP_DIR` | `/data/backups` |
| `GEOIP_CACHE_DIR` | `/data/geoip` |
| `WG_CONFIG_DIR` | `/data/wg` |
| `DIAGNOSTICS_DIR` | `/data/diagnostics` |
| `RUNTIME_DIR` | `/var/run/awg-gateway` |
| `DNS_RUNTIME_DIR` | `/data/dns` |

### Tunnel runtime

| Переменная | По умолчанию | Назначение |
|---|---:|---|
| `TUNNEL_INTERFACE` | `awg-gw0` | Имя tunnel-интерфейса |
| `AMNEZIAWG_GO_BINARY` | `amneziawg-go` | Userspace runtime |
| `AWG_BINARY` | `awg` | CLI для `setconf` |
| `DEFAULT_TUNNEL_ADDRESS` | `10.44.0.2/32` | Резервный tunnel address |
| `TUNNEL_MTU` | `1380` | MTU туннеля |
| `LATENCY_PING_COUNT` | `1` | Количество ICMP ping |
| `LATENCY_PING_TIMEOUT_SEC` | `2` | Таймаут latency probe |

### Routing и firewall

| Переменная | По умолчанию |
|---|---:|
| `ROUTING_TABLE_LOCAL` | `200` |
| `ROUTING_TABLE_VPN` | `201` |
| `FWMARK_LOCAL` | `0x1` |
| `FWMARK_VPN` | `0x2` |

### DNS и external IP

| Переменная | По умолчанию |
|---|---|
| `DEFAULT_DNS_SERVERS` | `1.1.1.1,8.8.8.8` |
| `EXTERNAL_IP_LOCAL_SERVICE_URL` | `https://ipinfo.io/ip` |
| `EXTERNAL_IP_VPN_SERVICE_URL` | `https://ifconfig.me/ip` |

### GeoIP

| Переменная | По умолчанию |
|---|---|
| `GEOIP_SOURCE` | `https://www.ipdeny.com/ipblocks/data/countries` |
| `GEOIP_FETCH_TIMEOUT` | `30` |

### Что реально задаётся через compose

Текущий [`gateway/docker-compose.yml`](../docker-compose.yml) передаёт в контейнер:

- `ADMIN_USERNAME`
- `ADMIN_PASSWORD`
- `WEB_PORT`
- `DATA_DIR`
- `DB_PATH`
- `BACKUP_DIR`
- `GEOIP_CACHE_DIR`
- `WG_CONFIG_DIR`
- `DIAGNOSTICS_DIR`
- `RUNTIME_DIR`

Остальные настройки можно добавлять в compose при необходимости.

## Веб-интерфейс

Frontend расположен в [`gateway/frontend`](../frontend), операторский UI монтируется тем же FastAPI-приложением.

### Dashboard

- состояние runtime и active tunnel;
- активная entry node;
- uptime active node;
- активный firewall stack;
- количество активных prefixes;
- CPU/RAM;
- трафик `local` и `vpn` за последний час и сутки;
- local/vpn external IP.

### Entry Nodes

- импорт peer `.conf`;
- хранение нескольких entry nodes;
- raw editor и visual editor;
- ручная активация active node;
- UDP probe endpoint и latency probe;
- reorder списка;
- удаление node;
- ручной `Start tunnel` и `Stop tunnel`.

### Prefix Policy / Routing

- список GeoIP countries;
- ручные IP/CIDR prefixes;
- FQDN prefixes;
- выбор направления для `routing_prefixes`:
  - через локальный интерфейс;
  - через AWG-интерфейс;
- kill switch;
- просмотр routing plan;
- принудительное `apply`.

### Split DNS

- upstream DNS для `local` и `vpn` зоны;
- список локальных доменных правил;
- preview итогового `dnsmasq.conf`;
- зависимость FQDN prefixes от DNS interception.

### Settings

- язык UI;
- runtime mode;
- список source IPv4/CIDR, к которым применяется gateway routing;
- включение/выключение gateway целиком;
- включение DNS interception;
- переключение `iptables`/`nftables`;
- настройка сервисов определения внешнего IP;
- API access key и control mode;
- смена пароля администратора.

### Backup / Diagnostics

- export backup;
- restore backup;
- список ранее созданных архивов;
- JSON diagnostics bundle.

## Маршрутизация, DNS и runtime

### Traffic source selection

Gateway policy применяется не ко всему хосту, а только к выбранным source CIDR. По умолчанию bootstrap добавляет `127.0.0.0/8`, то есть локальный трафик самого хоста/контейнера.

Список хранится в `gateway_settings.allowed_client_cidrs` и нормализуется в CIDR-вид.

### Runtime mode

Поддерживаются режимы:

- `auto`: пытаться использовать kernel mode, иначе fallback в userspace;
- `kernel`: требовать интерфейс типа `amneziawg`, иначе ошибка;
- `userspace`: всегда поднимать `amneziawg-go`.

Текущий live status вычисляется не только из БД, но и по фактическому существованию интерфейса и userspace PID.

### Firewall backend

Поддерживаются два backend:

- `iptables + ipset`
- `nftables + nft set`

Переключение очищает неактивный стек и полностью пересобирает активный.

### Prefix sources

В effective routing policy могут участвовать три источника:

- GeoIP prefixes;
- manual IPv4/CIDR prefixes;
- FQDN prefixes.

Если countries/manual/FQDN выключены одновременно, gateway создаёт fallback-маршрут через `0.0.0.0/0`.

### DNS interception

Если `dns_intercept_enabled=true`, DNS от выбранных source selectors перенаправляется в локальный `dnsmasq`, чтобы FQDN prefixes могли резолвиться и попадать в active prefix set backend.

`dnsmasq`:

- запускается и перезапускается динамически;
- хранит runtime config в `DNS_RUNTIME_DIR`;
- работает под пользователем `nobody`;
- может генерировать `ipset` или `nft set`-совместимые правила.

### Kill switch

Kill switch связан с routing plan и firewall stack. При активном gateway и безопасном плане он применяется вместе с routing rules. Отдельно его можно переключать и через key-based API.

### External IP detection

Gateway периодически проверяет:

- внешний IP через local path;
- внешний IP через VPN path.

Хостнеймы этих сервисов принудительно учитываются в routing policy, чтобы local- и vpn-проверка шли разными маршрутами.

## Entry nodes и bootstrap первой ноды

### Импорт entry node

Обычный поток:

1. В `awg-jump` экспортируется peer `.conf`.
2. В gateway этот конфиг импортируется через `Import peer .conf`.
3. Backend нормализует:
   - endpoint;
   - keys;
   - tunnel address;
   - DNS servers;
   - allowed IPs;
   - obfuscation-параметры.
4. Нода попадает в локальную БД и может быть назначена active node.

### Active node

Активная нода:

- хранится в `gateway_settings.active_entry_node_id`;
- ставится первой в failover order;
- при старте контейнера может быть автоматически восстановлена;
- участвует в latency probe через туннель;
- используется для построения routing plan.

### Failover

Failover:

- выключен по умолчанию;
- проверяется каждые 10 секунд;
- использует grace period 3 минуты (`FAILOVER_DISCONNECT_GRACE`);
- при деградации active node пытается поднять следующую доступную ноду по порядку;
- пишет результат в `failover_last_error` и `failover_last_event_at`.

### Bootstrap первой ноды

UI умеет запускать SSH bootstrap первой upstream-ноды:

- endpoint: `POST /api/nodes/bootstrap-first`
- лог-история: `GET /api/nodes/bootstrap-first/logs`
- live stream: `GET /api/nodes/bootstrap-first/{log_id}/stream`

Параметры bootstrap:

- `host`
- `ssh_user`
- `ssh_password`
- `ssh_port`
- `remote_dir`
- `docker_namespace`
- `image_tag`

SSH-пароль используется только для текущего запуска bootstrap и не сохраняется в БД.

## API

У gateway два уровня API:

1. Операторский API с bearer session.
2. Отдельный key-based API с `X-API-Key`.

### Аутентификация оператора

Базовые маршруты:

| Метод | Путь | Назначение |
|---|---|---|
| `POST` | `/api/auth/login` | Вход |
| `POST` | `/api/auth/logout` | Выход |
| `GET` | `/api/auth/me` | Текущая сессия |
| `POST` | `/api/auth/change-password` | Смена пароля |

Сессии хранятся в памяти процесса. После рестарта контейнера логин нужно выполнить повторно.

### System API

| Метод | Путь |
|---|---|
| `GET` | `/api/system/health` |
| `GET` | `/api/system/status` |
| `GET` | `/api/system/metrics?period=1h|24h` |

### Settings API

| Метод | Путь |
|---|---|
| `GET` | `/api/settings` |
| `PUT` | `/api/settings` |
| `PUT` | `/api/settings/api-access` |
| `POST` | `/api/settings/api-access/regenerate` |
| `PUT` | `/api/settings/gateway-enabled` |

### Entry Nodes API

| Метод | Путь |
|---|---|
| `GET` | `/api/nodes` |
| `POST` | `/api/nodes/import` |
| `GET` | `/api/nodes/{node_id}` |
| `PUT` | `/api/nodes/{node_id}` |
| `PUT` | `/api/nodes/{node_id}/raw-conf` |
| `PUT` | `/api/nodes/{node_id}/visual` |
| `DELETE` | `/api/nodes/{node_id}` |
| `POST` | `/api/nodes/{node_id}/move` |
| `POST` | `/api/nodes/{node_id}/activate` |
| `POST` | `/api/nodes/{node_id}/probe` |
| `POST` | `/api/nodes/runtime/start` |
| `POST` | `/api/nodes/runtime/stop` |
| `GET` | `/api/nodes/failover` |
| `PUT` | `/api/nodes/failover` |
| `POST` | `/api/nodes/bootstrap-first` |
| `GET` | `/api/nodes/bootstrap-first/logs` |
| `GET` | `/api/nodes/bootstrap-first/{log_id}/stream` |

### Routing API

| Метод | Путь |
|---|---|
| `GET` | `/api/routing` |
| `PUT` | `/api/routing` |
| `POST` | `/api/routing/countries` |
| `DELETE` | `/api/routing/countries/{country_code}` |
| `POST` | `/api/routing/manual-prefixes` |
| `POST` | `/api/routing/manual-prefixes/bulk` |
| `DELETE` | `/api/routing/manual-prefixes/{prefix}` |
| `POST` | `/api/routing/fqdn-prefixes` |
| `POST` | `/api/routing/fqdn-prefixes/bulk` |
| `DELETE` | `/api/routing/fqdn-prefixes/{fqdn}` |
| `POST` | `/api/routing/refresh-geoip` |
| `GET` | `/api/routing/plan` |
| `POST` | `/api/routing/apply` |

### DNS API

| Метод | Путь |
|---|---|
| `GET` | `/api/dns` |
| `PUT` | `/api/dns/upstreams/{zone}` |
| `POST` | `/api/dns/domains` |
| `POST` | `/api/dns/domains/bulk` |
| `DELETE` | `/api/dns/domains/{rule_id}` |

### Backup API

| Метод | Путь |
|---|---|
| `GET` | `/api/backup/export` |
| `POST` | `/api/backup/restore` |
| `GET` | `/api/backup/list` |
| `GET` | `/api/backup/diagnostics` |

### API по ключу

Полное описание вынесено в [api_access_ru.md](api_access_ru.md).

Базовые endpoints:

| Метод | Путь | Режим |
|---|---|---|
| `GET` | `/api/access/status` | read-only |
| `POST` | `/api/access/control/tunnel` | control mode |
| `POST` | `/api/access/control/kill-switch` | control mode |

Особенности:

- аутентификация через `X-API-Key`;
- API можно включать и выключать без рестарта;
- доступ можно ограничить списком IPv4/CIDR;
- control mode даёт только переключение gateway tunnel и kill switch.

## Многоязычность

UI использует словари:

- [`gateway/frontend/src/locales/en.ts`](../frontend/src/locales/en.ts)
- [`gateway/frontend/src/locales/ru.ts`](../frontend/src/locales/ru.ts)

Механика:

- английский язык дефолтный;
- русский является дополнительным словарём;
- выбранный язык сохраняется в `localStorage` как `gateway-locale`;
- backend хранит `ui_language` в `gateway_settings`;
- при отсутствии перевода UI fallback'ится на английское значение.

Технические детали формата описаны в [i18n_ru.md](i18n_ru.md).

## Бэкап, восстановление и диагностика

### Что входит в backup

- `manifest.json`
- `gateway.db`
- `geoip_cache/`
- `wg_configs/`

### Что не входит

- runtime-состояние текущего процесса;
- live PID;
- текущие iptables/nftables counters;
- `.env`;
- секреты вне SQLite и файлов backup-состава.

### Восстановление

`POST /api/backup/restore` заменяет `gateway.db` и восстанавливает кэши/конфиги из архива. После restore нужно перезапустить контейнер, чтобы runtime полностью перечитал состояние.

### Diagnostics bundle

`GET /api/backup/diagnostics` возвращает JSON-снимок:

- gateway settings;
- routing policy;
- entry nodes summary;
- routing plan;
- dns preview;
- manifest.

## Структура gateway-блока

```text
gateway/
├── README.md
├── .env.example
├── Dockerfile
├── docker-compose.yml
├── backend/
│   ├── requirements.txt
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── database.py
│   │   ├── models.py
│   │   ├── bootstrap.py
│   │   ├── security.py
│   │   ├── routers/
│   │   └── services/
│   └── tests/
├── frontend/
│   ├── src/
│   │   ├── App.tsx
│   │   ├── api.ts
│   │   ├── i18n.tsx
│   │   └── locales/
│   └── package.json
└── docs/
    ├── README_RU.md
    ├── README_EN.md
    ├── api_access_ru.md
    ├── i18n_ru.md
    └── future_integration_notes_ru.md
```

## Проверка и эксплуатация

Базовые команды:

```bash
python3 -m compileall gateway/backend/app
PYTHONPATH=/opt/awg-jump/gateway/backend pytest gateway/backend/tests -q
docker compose -f gateway/docker-compose.yml up -d --build
docker compose -f gateway/docker-compose.yml logs -f awg-gateway
```

Практические замечания:

- при первом запуске проверь, что на хосте включён `net.ipv4.ip_forward=1`;
- если нужен строго kernel mode, проверь доступность `ip link add ... type amneziawg`;
- после restore backup контейнер лучше перезапустить;
- если key-based API включён, обязательно ограничь `api_allowed_client_cidrs`, если шлюз доступен из недоверенной сети.
