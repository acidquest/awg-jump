# AWG Jump

`awg-jump` это контейнеризированный jump-сервер на базе AmneziaWG с веб-панелью, GeoIP policy routing и деплоем upstream-нод по SSH. В репозитории два Docker-образа:

- `awg-jump`: FastAPI + SPA + routing + failover + SSH deployer.
- `awg-node`: минималистичный upstream-узел с AmneziaWG. Предпочтительный режим на VPS-хосте ноды — kernel module `amneziawg`; он стабильнее, чем userspace `amneziawg-go`.

## Быстрый старт

1. Создай `.env` из шаблона:
   - RU: `cp .env.ru.example .env`
   - EN: `cp .env.en.example .env`
2. Измени как минимум `ADMIN_PASSWORD`, `SECRET_KEY`, `TLS_COMMON_NAME`.
3. Подними стек: `docker compose up -d --build`
4. Открой `https://<host>:${NGINX_HTTPS_PORT:-443}`

Для деплоя без локальной сборки через Docker Hub смотри:
- [Deploy via Docker Hub (RU)](docs/DEPLOY_DOCKER_HUB_RU.md)
- [Deploy via Docker Hub (EN)](docs/DEPLOY_DOCKER_HUB_EN.md)

API контейнера `awg-jump` также биндуется на `127.0.0.1:8080` только для локальной диагностики и smoke-тестов. Наружу публикуются `NGINX_HTTPS_PORT`, `NGINX_HTTP_PORT` и AWG UDP-порт.

## Основные переменные

- `ADMIN_USERNAME`, `ADMIN_PASSWORD`: логин администратора UI/API.
- `SECRET_KEY`: секрет подписи токенов.
- `AWG0_LISTEN_PORT`, `AWG0_ADDRESS`, `AWG0_DNS`: серверный интерфейс для клиентов.
- `AWG1_ADDRESS`, `AWG1_ALLOWED_IPS`, `AWG1_PERSISTENT_KEEPALIVE`: клиентский интерфейс jump → upstream node.
- `PHYSICAL_IFACE`, `ROUTING_TABLE_LOCAL`: физический интерфейс и routing table для local-zone трафика.
- `NODE_AWG_PORT`, `NODE_VPN_SUBNET`: параметры сети upstream-нод.
- `GEOIP_SOURCE`, `GEOIP_UPDATE_CRON`: базовый URL источника и расписание обновления GeoIP для локальной зоны.

Полный список и комментарии смотри в [`.env.ru.example`](.env.ru.example) или [`.env.en.example`](.env.en.example).

## Nodes и деплой

`awg-node` предназначен для удалённых VPS, которые `awg-jump` разворачивает по SSH из веб-интерфейса. Типовой поток такой:

1. На VPS должен быть установлен Docker Engine и открыт UDP-порт `NODE_AWG_PORT`.
2. Для более стабильной работы upstream-ноды рекомендуется установить на хосте kernel module `amneziawg` вместе с `amneziawg-tools`. Перед установкой модуля обязательно установи headers именно для текущего ядра: `linux-headers-$(uname -r)`.
3. Готовые host-side скрипты лежат в [node/scripts/install-kernel-module-debian12.sh](/opt/awg-jump/node/scripts/install-kernel-module-debian12.sh) и [node/scripts/install-kernel-module-debian13.sh](/opt/awg-jump/node/scripts/install-kernel-module-debian13.sh). При SSH-деплое ноды директория `node/` целиком доставляется на удалённый хост в `/opt/awg-node`, поэтому эти скрипты будут доступны там же: `/opt/awg-node/scripts/`.
4. В UI на странице Nodes добавляется нода с SSH host/login/password или ключом.
5. `awg-jump` собирает или доставляет образ `awg-node`, генерирует конфиг peer'а и поднимает контейнер на удалённой машине.
6. При активации ноды jump-сервер обновляет `awg1`, а health-check/failover переключает активную ноду при деградации.

Для ручного развёртывания upstream-узла смотри [node/README.md](node/README.md).

## Split DNS

`awg-jump` запускает встроенный DNS-сервер (`dnsmasq`) прямо в контейнере.

**Как это работает:**

- Клиенты получают IP интерфейса `awg0` в качестве DNS-сервера (автоматически).
- Домены из списка в веб-интерфейсе (страница **Split DNS**) могут быть направлены в `Local Zone` или `VPN Zone`.
- Для каждой зоны задаётся свой список DNS-серверов в UI и хранится в БД.
- DNS-запросы самого контейнера маршрутизируются по тем же правилам GeoIP: IP из `geoip_local` идут через `eth0`, остальные — через `awg1` (upstream VPN).

**Управление доменами:**

Страница **Split DNS** в веб-интерфейсе позволяет добавлять, отключать и удалять домены, а также редактировать DNS-серверы для `Local Zone` и `VPN Zone`. При старте создаётся набор дефолтных RU-доменов (`ru`, `рф`, `yandex.ru`, `vk.com`, `mail.ru`, `sber.ru`, `gosuslugi.ru` и др.).

Список доменов и DNS zone settings сохраняются в `config.db` и включаются в бэкап автоматически.

## Документация

- [Полная документация (RU)](docs/README_RU.md)
- [Full documentation (EN)](docs/README_EN.md)
- [Gateway-only block](gateway/README.md)
- [Deploy via Docker Hub (RU)](docs/DEPLOY_DOCKER_HUB_RU.md)
- [Deploy via Docker Hub (EN)](docs/DEPLOY_DOCKER_HUB_EN.md)

## Локальная проверка

- Smoke: `./scripts/smoke_test.sh`
- Backend tests: `pytest backend/tests/`
- Сборка образов:
  - `docker build -t awg-jump .`
  - `docker build -t awg-node ./node`
- Публикация и image-based deploy:
  - `./scripts/publish_dockerhub.sh <namespace> <tag> --latest`
  - `./scripts/publish_dockerhub.sh <namespace> <tag> --latest --with-node`
  - `./scripts/bootstrap_first_node.sh`
  - `powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap_first_node.ps1`
