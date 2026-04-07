# План: Multi-country zones + Configurable DNS

## Анализ текущего состояния

**GeoIP/Зоны:**
- Только одна зона `geoip_ru` — жёстко захардкожена в `routing.py:17`
- Модель `GeoIPSource` уже имеет поля `country_code`, `ipset_name`, `enabled`
- URL источника тоже захардкожен в `config.py:56`

**DNS:**
- Yandex DNS `77.88.8.8` — захардкожен в `dns_manager.py:29`
- Default `1.1.1.1, 8.8.8.8` — захардкожен в `dns_manager.py:30`
- Модель `DnsDomain` только выбирает `upstream: "yandex" | "default"`, без возможности задать конкретный IP

---

## План изменений

### Фича 1: Multi-country zone management

**Концепция:** все страны в "локальной" зоне объединяются в один ipset `geoip_local` (вместо `geoip_ru`). Пользователь добавляет страны по коду (RU, BY, KZ и т.д.) — URL строится автоматически из кода страны.

**Что меняется:**

| Файл | Изменение |
|------|-----------|
| `routing.py` | `geoip_ru` → `geoip_local` |
| `geoip_fetcher.py` | Fetch всех enabled источников, объединять в один ipset |
| `models/geoip.py` | Добавить поле `display_name` |
| `alembic/` | Миграция: переименовать ipset_name в существующих записях |
| `routers/geoip.py` | POST/DELETE/PUT для управления источниками |
| `GeoIP.tsx` | UI добавления/удаления стран |

### Фича 2: Configurable DNS per zone

**Концепция:** новая таблица `dns_zone_settings` хранит DNS-серверы для "local" и "vpn" зон. `dns_manager.py` читает из БД вместо хардкода.

**Что меняется:**

| Файл | Изменение |
|------|-----------|
| `models/dns_zone_settings.py` | Новая модель `DnsZoneSettings` |
| `alembic/` | Миграция + seed default значений |
| `dns_manager.py` | Читать DNS из БД, не из констант |
| `routers/dns.py` | GET/PUT `/api/dns/zones` |
| `DNS.tsx` | Секция редактирования DNS серверов для каждой зоны |

---

## Промпты для исполнения

### ПРОМПТ 1 — Backend: Multi-country GeoIP zones

```
Реализуй поддержку нескольких стран в локальной зоне маршрутизации для проекта awg-jump.

КОНТЕКСТ ПРОЕКТА:
- /opt/awg-jump/backend/services/geoip_fetcher.py — сервис загрузки GeoIP
- /opt/awg-jump/backend/services/ipset_manager.py — управление ipset
- /opt/awg-jump/backend/services/routing.py — политика маршрутизации
- /opt/awg-jump/backend/routers/geoip.py — API эндпоинты
- /opt/awg-jump/backend/models/geoip.py — модель GeoIPSource
- /opt/awg-jump/backend/alembic/ — миграции БД

ТЕКУЩЕЕ СОСТОЯНИЕ:
- Константа _GEOIP_IPSET_NAME = "geoip_ru" в routing.py — жёстко захардкожена
- GeoIPSource модель: id, name, url, country_code="ru", ipset_name="geoip_ru", last_updated, prefix_count, enabled, created_at
- geoip_fetcher.py загружает только один источник, обновляет один ipset
- iptables/ipset в routing.py ссылается на "geoip_ru"

ЧТО НУЖНО СДЕЛАТЬ:

1. ПЕРЕИМЕНОВАНИЕ: заменить "geoip_ru" на "geoip_local" везде:
   - В routing.py константа _GEOIP_IPSET_NAME = "geoip_local"
   - В ipset_manager.py если упоминается имя ipset
   - В alembic: миграция UPDATE geoip_sources SET ipset_name="geoip_local" WHERE ipset_name="geoip_ru"

2. МОДЕЛЬ GeoIPSource (models/geoip.py) — добавить поле:
   - display_name: str, nullable=False, default=""  (пример: "Russia", "Belarus")
   Alembic миграция для этого поля.

3. СЕРВИС geoip_fetcher.py — рефакторинг логики обновления:
   - Функция update_all_zones(db) — загружает все enabled GeoIPSource из БД
   - Для каждого источника: fetch URL → получить список prefix'ов
   - Объединить все prefix'ы в один список (дедуплицировать)
   - Атомарно обновить один ipset "geoip_local" через ipset_manager
   - Обновить prefix_count и last_updated для каждого источника отдельно (сколько prefix'ов принёс именно он)
   - URL автоматически строится если не задан явно: https://www.ipdeny.com/ipblocks/data/countries/{country_code}.zone

4. API роутер geoip.py — добавить CRUD для источников:

   POST /api/geoip/sources
   Body: { country_code: str, display_name: str, url: str | null }
   - country_code: 2-буквенный ISO код (lowercase), validate regex [a-z]{2}
   - url: если null — генерировать автоматически из country_code
   - Проверить что источник с таким country_code не существует
   - Создать запись, вернуть созданный объект

   DELETE /api/geoip/sources/{id}
   - Удалить источник
   - Запустить пересчёт ipset (без удалённого источника)
   - Запретить удаление если это последний источник (минимум 1)

   PUT /api/geoip/sources/{id}
   Body: { display_name: str | null, enabled: bool | null, url: str | null }
   - Обновить поля
   - Если changed enabled → запустить пересчёт ipset в фоне

   Уже существующий GET /api/geoip/sources — обновить response schema: добавить display_name, убрать ipset_name из публичного API (всегда geoip_local теперь)

5. ВАЛИДАЦИЯ country_code:
   - Только [a-z]{2} (2 буквы нижний регистр)
   - Если url не задан — проверить доступность URL до сохранения (HEAD request с timeout 5s)
   - При ошибке доступности — вернуть 422 с понятным сообщением

6. PYDANTIC SCHEMAS — обновить в routers/geoip.py или schemas/:
   GeoIPSourceCreate: country_code, display_name, url (optional)
   GeoIPSourceUpdate: display_name (optional), enabled (optional), url (optional)
   GeoIPSourceResponse: id, country_code, display_name, url, enabled, last_updated, prefix_count, created_at

Все новые endpoint'ы защитить через существующую auth зависимость (как остальные роутеры).
Не трогай фронтенд — только backend.
```

---

### ПРОМПТ 2 — Frontend: Multi-country GeoIP zones UI

```
Обнови страницу GeoIP в React SPA для управления несколькими странами в локальной зоне.

КОНТЕКСТ:
- /opt/awg-jump/frontend/src/pages/GeoIP.tsx — текущая страница
- Backend API (уже реализован):
  GET /api/geoip/sources → [{ id, country_code, display_name, url, enabled, last_updated, prefix_count }]
  POST /api/geoip/sources → { country_code, display_name, url? }
  PUT /api/geoip/sources/{id} → { display_name?, enabled?, url? }
  DELETE /api/geoip/sources/{id}
  POST /api/geoip/update → запускает обновление всех enabled источников
  GET /api/geoip/status → { total_prefixes, last_updated, sources: [...] }
  GET /api/geoip/progress → SSE stream

ТЕКУЩЕЕ СОСТОЯНИЕ GeoIP.tsx:
- Отображает список источников, кнопку "Update now", SSE прогресс
- Нет возможности добавлять/удалять/редактировать источники

ЧТО НУЖНО СДЕЛАТЬ:

1. ЗАГОЛОВОК СТРАНИЦЫ: изменить с "GeoIP Sources" на "Local Routing Zones"
   Добавить описание: "Countries routed through physical interface (eth0) instead of VPN"

2. ТАБЛИЦА ИСТОЧНИКОВ — обновить колонки:
   - Flag emoji (первые 2 буквы country_code → Regional Indicator Symbol:
     "ru" → 🇷🇺, это U+1F1F7 + U+1F1FA; формула:
     String.fromCodePoint(0x1F1E6 + cc[0].charCodeAt(0) - 97) + String.fromCodePoint(0x1F1E6 + cc[1].charCodeAt(0) - 97))
   - Country code (UPPERCASE display)
   - Display name
   - Prefix count (из этого источника)
   - Last updated (relative time)
   - Status badge: enabled/disabled
   - Actions: toggle enable, edit (display_name, url), delete

3. КНОПКА "Add Country" — открывает модальное окно:
   Форма:
   - Country code: input, placeholder "ru", pattern [a-zA-Z]{2}, автоматически lowercase
   - Display name: input, placeholder "Russia"
   - Custom URL: checkbox "Use custom URL" — по умолчанию unchecked
     Если checked: показать input для URL
     Если unchecked: показать preview "Will use: https://www.ipdeny.com/ipblocks/data/countries/{cc}.zone"
   Submit: "Add Zone"
   При ошибке 422 — показать error message из response

4. EDIT МОДАЛ — для каждого источника кнопка Edit:
   - Display name (editable)
   - URL (editable)
   - Save / Cancel

5. DELETE — confirm dialog: "Remove {display_name} ({COUNTRY_CODE}) from local routing zones?"
   После удаления — автоматически обновить список

6. SUMMARY БЛОК вверху (или внизу) страницы:
   Показать общую статистику: "X countries, Y total prefixes in local routing zone"
   Данные из GET /api/geoip/status

7. TanStack Query для всех запросов (как в остальных страницах проекта).
   Invalidate queries после мутаций.
   Оптимистичный update для toggle enable/disable.

8. Стили: использовать существующий стиль компонентов проекта (те же className паттерны что в других страницах).

Не трогай backend. Не создавай новые файлы кроме тех случаев когда это необходимо.
```

---

### ПРОМПТ 3 — Backend: Configurable DNS servers per zone

```
Реализуй настраиваемые DNS серверы для локальной и VPN зон в awg-jump.

КОНТЕКСТ:
- /opt/awg-jump/backend/services/dns_manager.py — управление dnsmasq
- /opt/awg-jump/backend/routers/dns.py — DNS API
- /opt/awg-jump/backend/models/ — модели БД
- /opt/awg-jump/backend/database.py — подключение к БД

ТЕКУЩЕЕ СОСТОЯНИЕ dns_manager.py:
- Строки 29-30:
  _LOCAL_DNS = "77.88.8.8"   (для доменов через локальный маршрут)
  _VPN_DNS = ["1.1.1.1", "8.8.8.8"]  (для остального)
- _write_config(): генерирует dnsmasq.conf с этими значениями жёстко

ЧТО НУЖНО СДЕЛАТЬ:

1. НОВАЯ МОДЕЛЬ (models/dns_zone_settings.py):

   class DnsZoneSettings(Base):
       __tablename__ = "dns_zone_settings"

       id: Mapped[int] = mapped_column(primary_key=True)
       zone: Mapped[str] = mapped_column(String(16), unique=True)  # "local" | "vpn"
       dns_servers: Mapped[str] = mapped_column(Text)  # JSON array: ["1.1.1.1", "8.8.8.8"]
       description: Mapped[str] = mapped_column(String(256), default="")
       updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)

2. ALEMBIC МИГРАЦИЯ:
   - Создать таблицу dns_zone_settings
   - INSERT seed данных:
     ("local", '["77.88.8.8"]', "DNS for local routing zone (RU/etc)")
     ("vpn", '["1.1.1.1", "8.8.8.8"]', "DNS for VPN routing zone")

3. DNS MANAGER (dns_manager.py) — рефакторинг:

   Добавить async метод get_zone_dns(db, zone: str) -> list[str]:
   - Читает из dns_zone_settings по zone name
   - Возвращает list[str] из JSON поля dns_servers

   Метод _write_config(local_dns: list[str], vpn_dns: list[str]) вместо жёстких констант:
   - local_dns: серверы для enabled доменов (upstream="yandex" legacy или новый механизм)
   - vpn_dns: серверы для остальных доменов

   Метод async reload(db) — читает настройки из БД, пересоздаёт конфиг, перезапускает dnsmasq

   При старте приложения (lifespan в main.py) — читать настройки из БД.

4. ОБРАТНАЯ СОВМЕСТИМОСТЬ DnsDomain.upstream:
   - Поле upstream="yandex" теперь означает "local zone DNS" (читается из dns_zone_settings zone="local")
   - Поле upstream="default" означает "vpn zone DNS" (читается из dns_zone_settings zone="vpn")
   - Переименовать enum значения в коде: DnsUpstream.LOCAL (было "yandex"), DnsUpstream.VPN (было "default")
   - В БД оставить строковые значения как есть (backward compat с существующими записями),
     добавить маппинг в enum: LOCAL = "yandex", VPN = "default"

5. API ЭНДПОИНТЫ (routers/dns.py) — добавить:

   GET /api/dns/zones
   Response: [
     { zone: "local", dns_servers: ["77.88.8.8"], description: "..." },
     { zone: "vpn", dns_servers: ["1.1.1.1", "8.8.8.8"], description: "..." }
   ]

   PUT /api/dns/zones/{zone}
   Body: { dns_servers: list[str], description?: str }
   Валидация dns_servers:
   - Не пустой список
   - Каждый элемент — валидный IPv4 или IPv6 адрес (использовать ipaddress.ip_address())
   - Максимум 3 сервера на зону
   После обновления: автоматически вызвать dns_manager.reload(db)
   Response: обновлённый объект зоны

   Защитить через существующую auth зависимость.

6. PYDANTIC SCHEMAS:
   DnsZoneResponse: zone, dns_servers, description, updated_at
   DnsZoneUpdate: dns_servers (list[str], min 1, max 3), description (optional)

Не трогай фронтенд. Не трогай логику управления доменами (DnsDomain) — только добавляешь новый функционал.
```

---

### ПРОМПТ 4 — Frontend: DNS zone settings UI

```
Добавь секцию настройки DNS серверов на страницу DNS в React SPA.

КОНТЕКСТ:
- /opt/awg-jump/frontend/src/pages/DNS.tsx — текущая страница
- Backend API (уже реализован):
  GET /api/dns/zones → [{ zone: "local"|"vpn", dns_servers: string[], description: string, updated_at: string }]
  PUT /api/dns/zones/{zone} → { dns_servers: string[], description?: string }
  POST /api/dns/reload → перезапуск dnsmasq

ТЕКУЩЕЕ СОСТОЯНИЕ DNS.tsx:
- Показывает статус dnsmasq
- Секция с хардкоженым текстом "Yandex 77.88.8.8" и "Default 1.1.1.1, 8.8.8.8"
- Таблица доменов split DNS
- Кнопка "Reload dnsmasq"

ЧТО НУЖНО СДЕЛАТЬ:

1. СЕКЦИЯ "DNS Zone Settings" — добавить МЕЖДУ статусом dnsmasq и таблицей доменов.

   Отображать 2 карточки/блока рядом (grid 2 cols):

   Карточка "Local Zone DNS" (zone="local"):
   - Заголовок с иконкой (home/local)
   - Описание: "Used for domains routed through physical interface"
   - Список текущих DNS серверов (pill badges)
   - Кнопка "Edit"

   Карточка "VPN Zone DNS" (zone="vpn"):
   - Заголовок с иконкой (shield/vpn)
   - Описание: "Used for all other traffic through VPN"
   - Список текущих DNS серверов (pill badges)
   - Кнопка "Edit"

2. EDIT MODE — inline (не модал):
   При клике Edit карточка переключается в режим редактирования:

   - Список IP с кнопкой удалить каждого
   - Input + кнопка Add для добавления нового IP
     Валидация: IPv4 (regex: /^(\d{1,3}\.){3}\d{1,3}$/) или IPv6
   - Кнопки: "Save" и "Cancel"
   - При Save — PUT /api/dns/zones/{zone}
   - После успешного Save — toast/notification "DNS settings updated, dnsmasq reloading..."
   - Показывать spinner пока идёт сохранение

3. ТАБЛИЦА ДОМЕНОВ — обновить labels:
   - В колонке Upstream: вместо "yandex" показывать "Local Zone" (с флажком/иконкой)
   - Вместо "default" показывать "VPN Zone"
   - В модале добавления домена: radio/select "Local Zone DNS" | "VPN Zone DNS"

4. TanStack Query:
   - useQuery для GET /api/dns/zones (queryKey: ['dns-zones'])
   - useMutation для PUT /api/dns/zones/{zone}
   - invalidateQueries(['dns-zones']) после мутации

5. Обработка ошибок:
   - Если API вернул 422 (невалидный IP) — показать inline error под input
   - Если API недоступен — показать error state в карточках

6. Стили: использовать существующий стиль компонентов проекта.
   Не создавай отдельные CSS файлы если их нет в проекте.

Не трогай backend. Не трогай логику управления доменами.
```

---

## Порядок исполнения

```
Промпт 1 ──┐
            ├──► Промпт 2 (frontend GeoIP)
            │
Промпт 3 ──┘
            └──► Промпт 4 (frontend DNS)
```

Промпты 1 и 3 независимы — можно запускать параллельно.
Промпты 2 и 4 зависят от 1 и 3 соответственно.

**Критические зависимости:**
- Промпт 1 меняет имя ipset с `geoip_ru` → `geoip_local` — после этого нужно пересоздать контейнер чтобы ipset был создан с новым именем
- Промпт 3 добавляет миграцию с seed данными — важно чтобы seed выполнился один раз при первом `upgrade head`
