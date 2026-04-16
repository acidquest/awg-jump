# API доступа по ключу

Новый API доступен по ключу и настраивается в UI:

- `Settings` → правая панель → блок `API доступ по ключу`
- переключатель `Включить API` включает доступ динамически, без рестарта контейнера
- при первом включении автоматически создаётся случайный 32-символьный ключ
- ключ можно перевыпустить кнопкой `Regenerate` в UI
- можно ограничить доступ к API списком разрешённых IP/CIDR
- переключатель `Разрешить управление через API` включает control mode
- если control mode выключен, API работает только в режиме `read only`

## Аутентификация

Во все запросы нужно передавать заголовок:

```http
X-API-Key: <32-char-key>
```

Если API выключен, сервер отвечает `403`.
Если ключ неверный, сервер отвечает `401`.
Если IP клиента не входит в разрешённый список, сервер отвечает `403`.
Если control mode выключен, управляющие эндпоинты отвечают `403`.

## Ограничение по IP

В UI можно задать список разрешённых адресов:

- `Settings` → `API доступ по ключу` → `Разрешённые IP-адреса API`

Поддерживается:

- одиночный IPv4, например `203.0.113.10`
- CIDR, например `192.168.1.0/24`

Если список пустой, ограничение по IP не применяется.

## Базовый URL

```text
https://<gateway-host>/api/access
```

## Read only: телеметрия

### `GET /api/access/status`

Возвращает текущую телеметрию gateway.

Пример ответа:

```json
{
  "status": {
    "vpn_enabled": true,
    "tunnel_status": "running"
  },
  "active_node": {
    "name": "nl-1",
    "latency_ms": 41.7,
    "latency_target": "10.8.0.1",
    "latency_via_interface": "awg0"
  },
  "external_ip": {
    "local": "198.51.100.10",
    "vpn": "203.0.113.20"
  },
  "uptime_seconds": 8342,
  "active_stack": "iptables",
  "active_prefixes": {
    "count": 18432,
    "configured_count": 18432,
    "set_name": "routing_prefixes"
  },
  "system": {
    "cpu_usage_percent": 12.5,
    "memory_total_bytes": 2147483648,
    "memory_used_bytes": 734003200,
    "memory_free_bytes": 1413470448
  },
  "traffic": {
    "current": {
      "collected_at": "2026-04-15T18:20:00+00:00",
      "local_interface_name": "eth0",
      "vpn_interface_name": "awg-gw0",
      "local": {
        "rx_bytes": 12837412,
        "tx_bytes": 19384756
      },
      "vpn": {
        "rx_bytes": 22193847,
        "tx_bytes": 28734123
      }
    },
    "last_hour": {
      "local": {
        "rx_bytes": 884123,
        "tx_bytes": 1293456
      },
      "vpn": {
        "rx_bytes": 1532456,
        "tx_bytes": 1884321
      }
    },
    "last_day": {
      "local": {
        "rx_bytes": 18441234,
        "tx_bytes": 25993456
      },
      "vpn": {
        "rx_bytes": 31532456,
        "tx_bytes": 38884321
      }
    }
  },
  "runtime_mode": "userspace",
  "routing_mode": {
    "target": "local",
    "label": "send_to_local_interface"
  },
  "kill_switch_enabled": true,
  "api_control_enabled": false
}
```

Поля, которые сейчас отдаются:

- статус VPN
- активная нода и её latency
- локальный внешний IP
- внешний IP через VPN
- uptime
- активный firewall stack
- число активных префиксов
- CPU и память
- трафик gateway только через контейнер, отдельно по `local` и `vpn`, входящий (`rx_bytes`) и исходящий (`tx_bytes`)
- runtime mode
- routing mode
- состояние kill switch

Набор полей можно расширять дальше без изменения модели доступа.

## Нюансы по traffic counters

- API отдает абсолютные счётчики `BIGINT` в байтах, пригодные для Home Assistant и других систем мониторинга.
- Поля `traffic.current.*.rx_bytes` и `traffic.current.*.tx_bytes` монотонно растут внутри логики gateway и переживают сброс системных firewall counters.
- Если underlying-счётчик сбросился в ноль, gateway начинает новый отсчёт корректно и продолжает суммарный total без отрицательных дельт.
- Это именно трафик, прошедший через правила gateway-контейнера, а не весь трафик интерфейсов хоста.

## Ротация ключа

Ключ можно перевыпустить из UI:

- `Settings` → `API доступ по ключу` → `Regenerate`

Или через админский API с обычной web-сессией:

### `POST /api/settings/api-access/regenerate`

Возвращает новый ключ в `api_settings.api_access_key`.

## Control mode: управление

Эти эндпоинты работают только если включён `Разрешить управление через API`.

### `POST /api/access/control/tunnel`

Включает или выключает VPN.

Тело запроса:

```json
{
  "enabled": true
}
```

Примеры:

```bash
curl -X POST "https://<gateway-host>/api/access/control/tunnel" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <32-char-key>" \
  -d '{"enabled":true}'
```

```bash
curl -X POST "https://<gateway-host>/api/access/control/tunnel" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <32-char-key>" \
  -d '{"enabled":false}'
```

### `POST /api/access/control/kill-switch`

Включает или выключает kill switch.

Тело запроса:

```json
{
  "enabled": false
}
```

Пример:

```bash
curl -X POST "https://<gateway-host>/api/access/control/kill-switch" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <32-char-key>" \
  -d '{"enabled":true}'
```

## Поведение

- API по ключу не использует операторскую web-сессию
- включение и выключение API применяется сразу
- ключ сейчас хранится в настройках gateway и отображается в UI
- control mode ограничен только командами VPN и kill switch
- для остального API остаётся обычная админская авторизация
