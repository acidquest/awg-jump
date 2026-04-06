# AWG Jump

`awg-jump` это контейнеризированный jump-сервер на базе AmneziaWG с веб-панелью, GeoIP policy routing и деплоем upstream-нод по SSH. В репозитории два Docker-образа:

- `awg-jump`: FastAPI + SPA + routing + failover + SSH deployer.
- `awg-node`: минималистичный upstream-узел с `amneziawg-go`.

## Быстрый старт

1. Создай `.env` из шаблона: `cp .env.example .env`
2. Измени как минимум `ADMIN_PASSWORD`, `SECRET_KEY`, `TLS_COMMON_NAME`.
3. Подними стек: `docker compose up -d --build`
4. Открой `https://<host>:${NGINX_HTTPS_PORT:-443}`

API контейнера `awg-jump` также биндуется на `127.0.0.1:8080` только для локальной диагностики и smoke-тестов. Наружу публикуются `NGINX_HTTPS_PORT`, `NGINX_HTTP_PORT` и AWG UDP-порт.

## Основные переменные

- `ADMIN_USERNAME`, `ADMIN_PASSWORD`: логин администратора UI/API.
- `SECRET_KEY`: секрет подписи токенов.
- `AWG0_LISTEN_PORT`, `AWG0_ADDRESS`, `AWG0_DNS`: серверный интерфейс для клиентов.
- `AWG1_ADDRESS`, `AWG1_ALLOWED_IPS`, `AWG1_PERSISTENT_KEEPALIVE`: клиентский интерфейс jump → upstream node.
- `PHYSICAL_IFACE`: физический интерфейс для RU-трафика.
- `NODE_AWG_PORT`, `NODE_VPN_SUBNET`: параметры сети upstream-нод.
- `GEOIP_SOURCE_RU`, `GEOIP_UPDATE_CRON`: источник и расписание обновления GeoIP.

Полный список и комментарии смотри в [`.env.example`](/opt/awg-jump/.env.example).

## Nodes и деплой

`awg-node` предназначен для удалённых VPS, которые `awg-jump` разворачивает по SSH из веб-интерфейса. Типовой поток такой:

1. На VPS должен быть установлен Docker Engine и открыт UDP-порт `NODE_AWG_PORT`.
2. В UI на странице Nodes добавляется нода с SSH host/login/password или ключом.
3. `awg-jump` собирает или доставляет образ `awg-node`, генерирует конфиг peer'а и поднимает контейнер на удалённой машине.
4. При активации ноды jump-сервер обновляет `awg1`, а health-check/failover переключает активную ноду при деградации.

Для ручного развёртывания upstream-узла смотри [node/README.md](/opt/awg-jump/node/README.md).

## Локальная проверка

- Smoke: `./scripts/smoke_test.sh`
- Backend tests: `pytest backend/tests/`
- Сборка образов:
  - `docker build -t awg-jump .`
  - `docker build -t awg-node ./node`
