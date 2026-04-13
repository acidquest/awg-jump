# AWG Gateway

`gateway/` — автономный Linux-only gateway-контейнер внутри репозитория `awg-jump`. Он не меняет входную ноду и не требует tunnel API: конфигурация entry node импортируется из обычного peer `.conf`, экспортированного из `awg-jump`.

## Что уже есть

- отдельный backend на `FastAPI` и `sqlite`;
- bootstrap auth из env и локальная смена пароля администратора;
- хранение нескольких entry nodes и ручной выбор active node;
- импорт peer `.conf` с нормализацией параметров;
- runtime foundation под `amneziawg-go` внутри контейнера;
- `latency` probe;
- `geoip`, routing plan, kill switch и `dns split`;
- backup/restore и diagnostics bundle;
- UI на английском по умолчанию, с модульной i18n и примером русского перевода;
- docker build / compose scaffold с healthcheck.

## Что сознательно не входит

- любые изменения входной ноды;
- tunnel API, command channel, telemetry services;
- auto-failover и remote override;
- control plane поверх туннеля.

## Структура

- [backend](backend) — автономный gateway backend.
- [frontend](frontend) — операторский UI.
- [docs/session_log_ru.md](docs/session_log_ru.md) — отчёт по сессиям 1-9.
- [docs/i18n_ru.md](docs/i18n_ru.md) — формат языковых модулей.
- [docs/future_integration_notes_ru.md](docs/future_integration_notes_ru.md) — только точки расширения, без интеграции.

## Локальный запуск

1. Создай env: `cp gateway/.env.example gateway/.env`
2. Собери и запусти: `docker compose -f gateway/docker-compose.yml up -d --build`
3. Открой `http://<host>:8081`

Для runtime-режимов требуются Linux, `NET_ADMIN`, `NET_RAW`, `/dev/net/tun` и `net.ipv4.ip_forward=1` внутри контейнера.

## Backend checks

- `python3 -m compileall gateway/backend/app`
- `PYTHONPATH=/opt/awg-jump/gateway/backend pytest gateway/backend/tests -q`
