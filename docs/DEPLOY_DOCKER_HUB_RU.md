# Деплой готовых Docker-образов

Этот сценарий нужен для прода, где сервер не должен собирать образы локально. Поток такой:

1. Локально собрать и запушить образы в Docker Hub.
2. На чистой Linux-нode поставить Docker.
3. Положить на ноду `docker-compose.yml`, `.env` и `.env.images`.
4. На сервере сделать `docker compose pull && docker compose up -d`.

## Какие образы публикуются

- `awg-jump`: backend + frontend + runtime
- `awg-jump-nginx`: nginx-конфиг и автогенерация self-signed TLS

## 1. Публикация образов в Docker Hub

Сначала залогинься:

```bash
docker login
```

Потом запусти скрипт публикации из корня репозитория:

```bash
./scripts/publish_dockerhub.sh <dockerhub-namespace> <tag> --latest
```

Пример:

```bash
./scripts/publish_dockerhub.sh myteam 2026-04-08 --latest
```

Скрипт пушит:

- `docker.io/myteam/awg-jump:2026-04-08`
- `docker.io/myteam/awg-jump-nginx:2026-04-08`
- и теги `latest`, если передан `--latest`

## 2. Bootstrap первой пустой ноды

Скрипт ставит Docker на удалённую машину, спрашивает директорию деплоя и раскладывает туда:

- `docker-compose.yml`
- `.env`
- `.env.ru.example`
- `.env.en.example`
- `.env.images`

Этот bootstrap предназначен для основной ноды с `awg-jump` и `awg-jump-nginx`.
Upstream-ноды через него не разворачиваются.

Запуск:

```bash
./scripts/bootstrap_first_node.sh
```

На Windows:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap_first_node.ps1
```

Скрипт спросит:

- IP/hostname ноды
- SSH user
- SSH port
- путь для деплоя
- Docker Hub namespace
  Обычно это ваш логин Docker Hub или имя Docker Hub organization.
- tag образов

После этого на ноде останется готовый каталог для старта.

Требования для Windows-машины:

- установлен OpenSSH Client (`ssh`, `scp`)
- PowerShell 5.1+ или PowerShell 7+

## 3. Первый запуск на сервере

Подключись к серверу и отредактируй как минимум:

- `ADMIN_PASSWORD`
- `SECRET_KEY`
- при необходимости `TLS_COMMON_NAME`
- при необходимости `SERVER_HOST`

Потом запусти:

```bash
cd /opt/awg-jump
docker compose --env-file .env.images pull
docker compose --env-file .env.images up -d
```

Проверка:

```bash
docker compose --env-file .env.images ps
docker compose --env-file .env.images logs --tail=100
```

## 4. Обновление без пересборки на сервере

После публикации нового тега:

1. поменяй теги в `.env.images`
2. выполни:

```bash
docker compose --env-file .env.images pull
docker compose --env-file .env.images up -d
```
