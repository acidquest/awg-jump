# AWG Gateway — Full Documentation (EN)

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Functionality](#functionality)
4. [Requirements and limitations](#requirements-and-limitations)
5. [Deployment methods](#deployment-methods)
6. [Environment variables](#environment-variables)
7. [Web UI](#web-ui)
8. [Routing, DNS, and runtime](#routing-dns-and-runtime)
9. [Entry nodes and first-node bootstrap](#entry-nodes-and-first-node-bootstrap)
10. [API](#api)
11. [Internationalization](#internationalization)
12. [Backup, restore, and diagnostics](#backup-restore-and-diagnostics)
13. [Gateway block structure](#gateway-block-structure)
14. [Verification and operations](#verification-and-operations)

## Overview

`AWG Gateway` in `gateway/` is a standalone Linux-only gateway contained inside the `awg-jump` repository. It is designed for the case where the entry node already exists and the gateway only needs to:

- import the entry node peer config from a `.conf` exported by `awg-jump`;
- start a local tunnel runtime based on AmneziaWG;
- apply policy routing between the host uplink and the AWG tunnel;
- intercept DNS for selected traffic sources and populate prefix sets from FQDNs;
- expose an operator UI and a separate key-based API for telemetry and limited remote control.

`gateway` does not modify the remote entry node and does not implement a full bidirectional control plane over the tunnel. It can still send its client status (`1001`) back to `awg-jump` after a successful connection and then every 10 minutes when the imported entry node `.conf` was exported by a recent `awg-jump` and the status endpoint is reachable inside the tunnel.

Related documents:

- [Key-based access API](api_access_ru.md)
- [i18n module format](i18n_ru.md)

## Architecture

```text
Browser
  -> http://<gateway-host>:8081
  -> FastAPI + SPA
  -> SQLite (/data/gateway.db)
  -> dnsmasq
  -> iptables/ipset or nftables/nft set
  -> amneziawg-go or kernel amneziawg
  -> host network namespace
```

Main parts:

- backend: [`gateway/backend/app`](../backend/app) built with FastAPI + SQLAlchemy + SQLite;
- frontend: [`gateway/frontend`](../frontend) built with React/Vite;
- tunnel runtime: [`services/runtime.py`](../backend/app/services/runtime.py);
- routing and firewall backend: [`services/routing.py`](../backend/app/services/routing.py);
- split DNS runtime: [`services/dns_runtime.py`](../backend/app/services/dns_runtime.py);
- failover: [`services/failover.py`](../backend/app/services/failover.py);
- backup/diagnostics: [`services/backup.py`](../backend/app/services/backup.py).

The container runs in `network_mode: host`, so it applies routing and NAT directly in the host network namespace.

## Functionality

### Core features

- operator web authentication with local password change;
- SQLite-backed storage for multiple entry nodes;
- peer `.conf` import and visual editing of node parameters;
- manual active node selection;
- automatic tunnel restore on startup if an active node was selected previously;
- `auto`, `kernel`, and `userspace` runtime modes;
- latency probing, UDP probing, and live tunnel status detection;
- policy routing for selected source CIDRs;
- kill switch;
- firewall backend switch between `iptables + ipset` and `nftables + nft set`;
- GeoIP prefixes, manual CIDRs, and FQDN prefixes;
- split DNS via local `dnsmasq`;
- external IP checks for local path and VPN path;
- automatic failover to the next entry node;
- backup/export, restore, and diagnostics bundle;
- first upstream node bootstrap over SSH;
- English and Russian UI;
- dedicated `X-API-Key` API for telemetry and limited control.
- automatic `awg-gateway -> awg-jump` client status report over the tunnel when supported by the imported config.

### Intentionally out of scope

- modifying the remote entry node after import;
- a shared `awg-jump <-> gateway` control plane over the tunnel;
- full remote configuration through the key-based API;
- non-Linux runtime support.

## Requirements and limitations

- Linux host;
- Docker Engine and `docker compose`;
- `NET_ADMIN`, `NET_RAW`;
- `/dev/net/tun`;
- `net.ipv4.ip_forward=1` enabled on the host;
- container access to host networking;
- IPv4-first behavior for routing policy and API CIDR restrictions;
- `dnsmasq`, `iptables`, `ipset`, `nftables`, `iproute2`, `ping`, `awg`, and `amneziawg-go` are bundled into the image.

Important:

- bridge networking is not suitable if you want the container to behave as the actual gateway;
- with `network_mode: host`, `net.ipv4.ip_forward=1` must be configured on the host, not through container sysctls;
- API docs (`/api/docs`, `/api/openapi.json`) are disabled by default and only enabled with `ALLOW_API_DOCS=true`.

## Deployment methods

### 1. Local build and run from the repository

```bash
cp gateway/.env.example gateway/.env
docker compose -f gateway/docker-compose.yml up -d --build
```

The UI will be available at:

```text
http://<host>:${GATEWAY_WEB_PORT:-8081}
```

This is the primary deployment path documented in the repository.

### 2. Manual standalone image build

From the repository root:

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

### 3. Publishing a gateway image to Docker Hub

The repository includes [`scripts/publish_dockerhub.sh`](../../scripts/publish_dockerhub.sh), which can build and publish the gateway image:

```bash
./scripts/publish_dockerhub.sh <namespace> <tag> --gateway
./scripts/publish_dockerhub.sh <namespace> <tag> --only-gateway
```

Published tag:

```text
docker.io/<namespace>/awg-gateway:<tag>
```

After publishing, you can use your own compose/docker run setup with the same host networking, capabilities, and volume layout as in [`gateway/docker-compose.yml`](../docker-compose.yml).

## Environment variables

Public runtime settings are defined in [`gateway/backend/app/config.py`](../backend/app/config.py). The current minimal example is in [`gateway/.env.example`](../.env.example).

### Basic

| Variable | Default | Purpose |
|---|---:|---|
| `ADMIN_USERNAME` | `admin` | Operator login |
| `ADMIN_PASSWORD` | `changeme` | Operator password used at bootstrap |
| `WEB_PORT` | `8081` | HTTP port for UI/API |
| `ALLOW_API_DOCS` | `false` | Enables `/api/docs`, `/api/redoc`, `/api/openapi.json` |
| `SESSION_TTL_HOURS` | `8` | Web session lifetime |
| `UI_DEFAULT_LANGUAGE` | `en` | Bootstrap language |

### Paths and data

| Variable | Default |
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

| Variable | Default | Purpose |
|---|---:|---|
| `TUNNEL_INTERFACE` | `awg-gw0` | Tunnel interface name |
| `AMNEZIAWG_GO_BINARY` | `amneziawg-go` | Userspace runtime binary |
| `AWG_BINARY` | `awg` | CLI used for `setconf` |
| `DEFAULT_TUNNEL_ADDRESS` | `10.44.0.2/32` | Fallback tunnel address |
| `TUNNEL_MTU` | `1380` | Tunnel MTU |
| `LATENCY_PING_COUNT` | `1` | ICMP ping count |
| `LATENCY_PING_TIMEOUT_SEC` | `2` | Latency probe timeout |

### Routing and firewall

| Variable | Default |
|---|---:|
| `ROUTING_TABLE_LOCAL` | `200` |
| `ROUTING_TABLE_VPN` | `201` |
| `FWMARK_LOCAL` | `0x1` |
| `FWMARK_VPN` | `0x2` |

### DNS and external IP

| Variable | Default |
|---|---|
| `DEFAULT_DNS_SERVERS` | `1.1.1.1,8.8.8.8` |
| `EXTERNAL_IP_LOCAL_SERVICE_URL` | `https://ipinfo.io/ip` |
| `EXTERNAL_IP_VPN_SERVICE_URL` | `https://ifconfig.me/ip` |

### GeoIP

| Variable | Default |
|---|---|
| `GEOIP_SOURCE` | `https://www.ipdeny.com/ipblocks/data/countries` |
| `GEOIP_FETCH_TIMEOUT` | `30` |

### What the current compose passes into the container

The current [`gateway/docker-compose.yml`](../docker-compose.yml) provides:

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

Other settings can be added to the compose file when needed.

## Web UI

The frontend lives in [`gateway/frontend`](../frontend), and the operator UI is served by the same FastAPI app.

### Dashboard

- runtime and active tunnel status;
- active entry node;
- active node uptime;
- active firewall stack;
- active prefix count;
- CPU/RAM;
- `local` and `vpn` traffic for the last hour and last day;
- local/vpn external IP values.

### Entry Nodes

- peer `.conf` import;
- storage for multiple entry nodes;
- raw editor and visual editor;
- manual active node selection;
- UDP endpoint probe and latency probe;
- node reordering;
- node deletion;
- manual `Start tunnel` and `Stop tunnel`.

### Prefix Policy / Routing

- GeoIP country list;
- manual IP/CIDR prefixes;
- FQDN prefixes;
- routing direction for `routing_prefixes`:
  - through the local interface;
  - through the AWG interface;
- kill switch;
- routing plan preview;
- explicit `apply`.

### Split DNS

- upstream DNS for `local` and `vpn` zones;
- local domain rule list;
- preview of the generated `dnsmasq.conf`;
- FQDN prefixes coupled with DNS interception.

### Settings

- UI language;
- runtime mode;
- list of source IPv4/CIDRs covered by gateway policy;
- global gateway enable/disable;
- DNS interception switch;
- `iptables`/`nftables` backend switch;
- external IP service URLs;
- API access key and control mode;
- administrator password change.

### Devices

- traffic-source inventory with IP/MAC correlation;
- manual `API` tag to include a device into `scope=marked`;
- `Local/VPN` device button cycling through `disabled -> local -> vpn -> disabled`;
- per-device route override for the current source IP on top of the global policy routing rules.

### Backup / Diagnostics

- backup export;
- backup restore;
- list of previously created archives;
- JSON diagnostics bundle.

## Routing, DNS, and runtime

### Traffic source selection

Gateway policy is not applied to the entire host. It only applies to selected source CIDRs. By default, bootstrap adds `127.0.0.0/8`, which means localhost-originated traffic from the host/container.

The list is stored in `gateway_settings.allowed_client_cidrs` and normalized into CIDR form.

If a device is switched to `Local` or `VPN` on the `Devices` page, the gateway adds a higher-priority override for that device's current source IP. That traffic is always marked into the local or tunnel routing table regardless of the regular prefix-based policy. The override works with both `iptables` and `nftables` backends.

### Runtime mode

Supported modes:

- `auto`: try kernel mode first, fall back to userspace;
- `kernel`: require `amneziawg` kernel interface support or fail;
- `userspace`: always start `amneziawg-go`.

Live status is not taken from the database alone. It is re-evaluated from the actual interface presence and the userspace PID when relevant.

### Firewall backend

Two backends are supported:

- `iptables + ipset`
- `nftables + nft set`

Switching backends tears down the inactive stack and rebuilds the active one in full.

### Prefix sources

The effective routing policy can combine three sources:

- GeoIP prefixes;
- manual IPv4/CIDR prefixes;
- FQDN prefixes.

If GeoIP, manual prefixes, and FQDN prefixes are all disabled, the gateway falls back to `0.0.0.0/0`.

### DNS interception

When `dns_intercept_enabled=true`, DNS from the selected source selectors is redirected to the local `dnsmasq` instance so FQDN prefixes can be resolved into the active prefix set backend.

`dnsmasq`:

- is started and restarted dynamically;
- stores runtime config in `DNS_RUNTIME_DIR`;
- runs as user `nobody`;
- can generate either `ipset` or `nft set` compatible rules.

### Kill switch

The kill switch is part of the routing plan and firewall stack. When the gateway is enabled and the plan is safe to apply, it is enforced together with the routing rules. It can also be toggled through the key-based API.

### External IP detection

The gateway periodically checks:

- external IP via the local path;
- external IP via the VPN path.

The hostnames of those services are force-injected into routing policy so the local and VPN checks actually follow different paths.

## Entry nodes and first-node bootstrap

### Entry node import

Typical flow:

1. Export a peer `.conf` from `awg-jump`.
2. Import it in the gateway UI.
3. The backend normalizes:
   - endpoint;
   - keys;
   - tunnel address;
   - DNS servers;
   - allowed IPs;
   - obfuscation parameters.
4. The node is stored in the local database and can be assigned as the active node.
5. If the `.conf` was exported from a recent `awg-jump`, it also carries metadata comments with the tunnel status API URL. After a successful connection, `gateway` will try to submit its client code `1001` there and then repeat the report every 10 minutes.

### Active node

The active node:

- is stored in `gateway_settings.active_entry_node_id`;
- is moved to the top of the failover order;
- can be restored automatically on container startup;
- is probed through the tunnel;
- is used to build the routing plan.

### Failover

Failover:

- is disabled by default;
- is evaluated every 10 seconds;
- uses a 3-minute grace period (`FAILOVER_DISCONNECT_GRACE`);
- attempts to start the next available node in order when the active node degrades;
- records state in `failover_last_error` and `failover_last_event_at`.

### First-node bootstrap

The UI can launch an SSH bootstrap of the first upstream node:

- endpoint: `POST /api/nodes/bootstrap-first`
- history: `GET /api/nodes/bootstrap-first/logs`
- live stream: `GET /api/nodes/bootstrap-first/{log_id}/stream`

Bootstrap parameters:

- `host`
- `ssh_user`
- `ssh_password`
- `ssh_port`
- `remote_dir`
- `docker_namespace`
- `image_tag`

The SSH password is only used for the current bootstrap run and is not persisted in the database.

## API

The gateway exposes two API layers:

1. Operator API with bearer session auth.
2. Separate key-based API with `X-API-Key`.

### Operator authentication

Core routes:

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/auth/login` | Sign in |
| `POST` | `/api/auth/logout` | Sign out |
| `GET` | `/api/auth/me` | Current session |
| `POST` | `/api/auth/change-password` | Change password |

Sessions are stored in process memory. After a container restart, operators must sign in again.

### System API

| Method | Path |
|---|---|
| `GET` | `/api/system/health` |
| `GET` | `/api/system/status` |
| `GET` | `/api/system/metrics?period=1h|24h` |

### Settings API

| Method | Path |
|---|---|
| `GET` | `/api/settings` |
| `PUT` | `/api/settings` |
| `PUT` | `/api/settings/api-access` |
| `POST` | `/api/settings/api-access/regenerate` |
| `PUT` | `/api/settings/gateway-enabled` |

### Entry Nodes API

| Method | Path |
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

| Method | Path |
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

| Method | Path |
|---|---|
| `GET` | `/api/dns` |
| `PUT` | `/api/dns/upstreams/{zone}` |
| `POST` | `/api/dns/domains` |
| `POST` | `/api/dns/domains/bulk` |
| `DELETE` | `/api/dns/domains/{rule_id}` |

### Backup API

| Method | Path |
|---|---|
| `GET` | `/api/backup/export` |
| `POST` | `/api/backup/restore` |
| `GET` | `/api/backup/list` |
| `GET` | `/api/backup/diagnostics` |

### Key-based API

Full details are documented in [api_access_ru.md](api_access_ru.md).

Core endpoints:

| Method | Path | Mode |
|---|---|---|
| `GET` | `/api/access/status` | read-only |
| `POST` | `/api/access/control/tunnel` | control mode |
| `POST` | `/api/access/control/kill-switch` | control mode |

Properties:

- authentication uses `X-API-Key`;
- the API can be enabled or disabled without restarting the container;
- access can be restricted by IPv4/CIDR;
- control mode is limited to toggling the gateway tunnel and the kill switch.

## Internationalization

The UI uses dictionary modules:

- [`gateway/frontend/src/locales/en.ts`](../frontend/src/locales/en.ts)
- [`gateway/frontend/src/locales/ru.ts`](../frontend/src/locales/ru.ts)

Behavior:

- English is the default language;
- Russian is an additional dictionary;
- the selected language is stored in `localStorage` as `gateway-locale`;
- the backend also stores `ui_language` in `gateway_settings`;
- missing translations fall back to the English string.

Technical format details are documented in [i18n_ru.md](i18n_ru.md).

## Backup, restore, and diagnostics

### What a backup includes

- `manifest.json`
- `gateway.db`
- `geoip_cache/`
- `wg_configs/`

### What it does not include

- current in-memory process state;
- live PID values;
- current iptables/nftables counters;
- `.env`;
- secrets stored outside SQLite and the archived backup files.

### Restore

`POST /api/backup/restore` replaces `gateway.db` and restores caches/config files from the archive. After a restore, restart the container so the runtime fully reloads state.

### Diagnostics bundle

`GET /api/backup/diagnostics` returns a JSON snapshot with:

- gateway settings;
- routing policy;
- entry node summary;
- routing plan;
- DNS preview;
- manifest.

## Gateway block structure

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

## Verification and operations

Basic commands:

```bash
python3 -m compileall gateway/backend/app
PYTHONPATH=/opt/awg-jump/gateway/backend pytest gateway/backend/tests -q
docker compose -f gateway/docker-compose.yml up -d --build
docker compose -f gateway/docker-compose.yml logs -f awg-gateway
```

Operational notes:

- verify that `net.ipv4.ip_forward=1` is enabled on the host before first use;
- if you require strict kernel mode, verify that `ip link add ... type amneziawg` works on the host/container runtime;
- after restoring a backup, restart the container;
- if key-based API access is enabled, restrict `api_allowed_client_cidrs` when the gateway is reachable from untrusted networks.
