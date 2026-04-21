# AWG Jump — Full Documentation (EN)

## Table of Contents

1. [Project Overview](#project-overview)
2. [AWG Gateway](#awg-gateway)
3. [Architecture](#architecture)
4. [Quick Start](#quick-start)
5. [Environment Variables](#environment-variables)
6. [Web Interface](#web-interface)
7. [AmneziaWG and Obfuscation](#amneziawg-and-obfuscation)
8. [GeoIP and Routing](#geoip-and-routing)
9. [Split DNS](#split-dns)
10. [Upstream Nodes and Failover](#upstream-nodes-and-failover)
11. [Backup and Restore](#backup-and-restore)
12. [TLS and Web Access](#tls-and-web-access)
13. [Project Structure](#project-structure)
14. [Development and Debugging](#development-and-debugging)

---

## Project Overview

**AWG Jump** is a containerized jump server built on [AmneziaWG](https://github.com/amnezia-vpn/amneziawg-go) (a WireGuard fork with DPI obfuscation). It implements a split traffic routing policy:

- **Russian IP addresses** → host's physical interface (`eth0`) — direct connection, no VPN.
- **All other traffic** → `awg1` — upstream node (foreign VPS).

Additionally provides **Split DNS**: clients receive a built-in DNS server that routes queries for local-zone domains through configurable local zone DNS servers, and everything else through configurable VPN zone DNS servers.

The repository contains two Docker images:

| Image | Purpose |
|-------|---------|
| `awg-jump` | Main server: AWG + FastAPI + React SPA + GeoIP + routing + DNS + SSH deployer |
| `awg-node` | Minimalist upstream node: only `amneziawg-go` |

---

## AWG Gateway

The repository also contains a dedicated `gateway/` block for a standalone gateway deployment. It is an autonomous Linux-only gateway container that imports an entry node peer config from `awg-jump`, starts a local AWG tunnel runtime, applies policy routing and split DNS, and does not manage the upstream node directly.

Primary gateway documents:

- [AWG Gateway full documentation (RU)](../gateway/docs/README_RU.md)
- [AWG Gateway full documentation (EN)](../gateway/docs/README_EN.md)
- [Key-based telemetry/control API](../gateway/docs/api_access_ru.md)

---

## Architecture

```
Browser (WEB_PORT, default HTTPS:8080)
    │
    ▼
┌─────────────────────────────────────────────┐
│              awg-jump :WEB_PORT             │
│                                             │
│  FastAPI + SQLite + APScheduler             │
│  uvicorn (http or https)                    │
│  amneziawg-go (awg0 + awg1)                │
│  dnsmasq (split DNS)                        │
│  ipset geoip_local + iptables policy routing│
└─────────────────────────────────────────────┘
       │ awg0 UDP:51820          │ awg1 → upstream
       │                         │
  AWG Clients              ┌─────┴──────┐
  (phone, PC)              │  awg-node  │ VPS #1 (active)
                           └────────────┘
                           ┌────────────┐
                           │  awg-node  │ VPS #2 (standby)
                           └────────────┘
```

### Traffic Flows

```
Client → awg0 → iptables mangle PREROUTING:
    dst in ipset geoip_local  →  fwmark LOCAL →  table 100  →  eth0 (direct)
    dst not in geoip_local    →  fwmark VPN →  table 200  →  awg1 (VPN)

Container (dnsmasq DNS queries) → iptables mangle OUTPUT:
    dst in ipset geoip_local  →  fwmark LOCAL →  table 100  →  eth0
    dst not in geoip_local    →  fwmark VPN →  table 200  →  awg1
```

---

## Quick Start

### Requirements

- Docker Engine 24+
- docker compose v2
- Open ports: `WEB_PORT/tcp` (default `8080/tcp`) and `51820/udp` (AWG)

### Installation

```bash
# 1. Clone the repository
git clone <repo-url> awg-jump && cd awg-jump

# 2. Create configuration
cp .env.en.example .env

# 3. Required changes:
#   ADMIN_PASSWORD=<strong password>
#   SECRET_KEY=<random string 32+ chars>
#   TLS_COMMON_NAME=<server IP or hostname>
#   SERVER_HOST=<public IP for Endpoint in client configs>
nano .env

# 4. Start
docker compose up -d --build

# 5. Open web interface
https://<SERVER_HOST>:<WEB_PORT>
```

The browser will warn about a self-signed certificate — this is expected. Add an exception or install the certificate manually.

### First Start

On first launch, the container automatically:

1. Applies database migrations (Alembic).
2. Creates `awg0` (server) and `awg1` (client) interfaces.
3. Generates AWG keys for both interfaces.
4. Generates AmneziaWG obfuscation parameters.
5. Loads GeoIP cache for all enabled countries into ipset `geoip_local`.
6. Configures policy routing and iptables rules.
7. Starts dnsmasq with a set of default Russian domains.
8. Starts APScheduler (GeoIP cron, health checks, peer stats sync).

---

## Environment Variables

### Web / TLS

| Variable | Default | Description |
|---------|---------|-------------|
| `WEB_MODE` | `https` | Web server mode: `http` or `https` |
| `WEB_PORT` | `8080` | UI/API TCP port |
| `TLS_COMMON_NAME` | `localhost` | CN and SAN for self-signed cert (IP or hostname) |
| `TLS_CERT_PATH` | `/data/certs/server.crt` | Certificate path |
| `TLS_KEY_PATH` | `/data/certs/server.key` | Private key path |

### Administrator

| Variable | Default | Description |
|---------|---------|-------------|
| `ADMIN_USERNAME` | `admin` | Admin login |
| `ADMIN_PASSWORD` | `changeme` | **Must be changed** |
| `SECRET_KEY` | `insecure-default` | Token signing secret (**must be changed**) |
| `SESSION_TTL_HOURS` | `8` | Session lifetime in hours |

### AWG0 Server (accepts clients)

| Variable | Default | Description |
|---------|---------|-------------|
| `AWG0_LISTEN_PORT` | `51820` | AWG server UDP port |
| `AWG0_PRIVATE_KEY` | _(auto)_ | Private key; auto-generated if empty |
| `AWG0_ADDRESS` | `10.10.0.1/24` | awg0 interface address |
| `AWG0_DNS` | _(awg0 IP)_ | DNS for awg0 clients |
| `CLASSIC_WG` | _(empty)_ | `on` enables the optional `wg0` interface |
| `WG0_LISTEN_PORT` | _(required when `CLASSIC_WG=on`)_ | classic WireGuard server UDP port |
| `WG0_PRIVATE_KEY` | _(auto)_ | wg0 private key |
| `WG0_ADDRESS` | `10.11.0.1/24` | wg0 interface address |
| `WG0_DNS` | `10.11.0.1` | DNS for wg0 clients |
| `SERVER_HOST` | `` | Public IP/hostname for `Endpoint` in client configs |

### AWG1 Client (upstream VPN)

| Variable | Default | Description |
|---------|---------|-------------|
| `AWG1_ADDRESS` | `10.20.0.2/32` | awg1 address in the VPN subnet |
| `AWG1_ALLOWED_IPS` | `0.0.0.0/0` | Allowed IPs through awg1 |
| `AWG1_PERSISTENT_KEEPALIVE` | `25` | Keepalive interval in seconds |
| `AWG1_ENDPOINT` | _(auto)_ | Auto-filled when a node is activated |

### Routing

| Variable | Default | Description |
|---------|---------|-------------|
| `PHYSICAL_IFACE` | `eth0` | Physical interface for local-zone traffic |
| `ROUTING_TABLE_LOCAL` | `100` | Routing table for local-zone traffic |
| `ROUTING_TABLE_VPN` | `200` | Routing table for VPN traffic |
| `FWMARK_LOCAL` | `0x1` | fwmark for local-zone packets |
| `FWMARK_VPN` | `0x2` | fwmark for VPN packets |

### GeoIP

| Variable | Default | Description |
|---------|---------|-------------|
| `GEOIP_SOURCE` | ipdeny.com | Base GeoIP source URL; the final URL is built as `<base><country_code>.zone` |
| `GEOIP_UPDATE_CRON` | `0 4 * * *` | Update schedule (UTC cron) |
| `GEOIP_FETCH_TIMEOUT` | `30` | Download timeout in seconds |

### Upstream Nodes

| Variable | Default | Description |
|---------|---------|-------------|
| `NODE_HEALTH_CHECK_INTERVAL` | `30` | Health check interval (seconds) |
| `NODE_HEALTH_CHECK_TIMEOUT` | `5` | Single check timeout (seconds) |
| `NODE_FAILOVER_THRESHOLD` | `3` | Failures before node failover |
| `NODE_AWG_PORT` | `51821` | AWG UDP port on remote nodes |
| `NODE_VPN_SUBNET` | `10.20.0.0/24` | Subnet for jump ↔ node communication |

---

## Web Interface

Accessible at `https://<SERVER_HOST>:<WEB_PORT>` or `http://<SERVER_HOST>:<WEB_PORT>` depending on `WEB_MODE`. All pages require authentication.

### Dashboard

Summary panel: interface status, connected peers, active upstream node, GeoIP state, routing state, dnsmasq status.

### Interfaces

Manage tunnel interfaces `awg0`, `awg1`, and optional `wg0`:

- View keys, addresses, ports.
- Edit parameters (listen port, address, DNS, keepalive).
- **Apply** (restart interface) and **Stop** buttons.
- AmneziaWG obfuscation parameters block (Jc, Jmin, Jmax, S1–S4, H1–H4).
- **Regenerate** button — generates new obfuscation parameters. **Warning:** after regeneration, all clients must receive new configurations and reconnect.

### Peers

Manage peers for server interfaces:

- Create peers with automatic IP allocation from the awg0 subnet.
- Download client config as `.conf` file and QR code for mobile apps.
- Enable/disable peers without restarting the interface (hot reload via `wg syncconf`).
- Live statistics: last handshake, RX/TX bytes.
- Client type markers (`awg-gateway`, Android, iOS) fed by the tunnel status API.

#### Tunnel status API

Unauthenticated endpoint:

`POST /api/peers/status`

Payload:

```json
{"client_code": 1001}
```

Supported client codes:

- `1001` — `awg-gateway`
- `1002` — `awg-jump-client-android`
- `1003` — `awg-jump-client-ios`

The caller IP is taken from the request and matched against `awg0` peer `tunnel_address`. Only known tunnel clients are accepted.

### Nodes

Manage upstream nodes:

- Add a VPS with SSH credentials (not stored).
- Deploy `awg-node` over SSH: install Docker, build image, start container.
- `Add node` for manual upstream nodes imported from a standard AWG peer `.conf`.
- Streaming deploy log output (SSE).
- Switch active node, view metrics (latency, RX/TX).
- Automatic failover when the active node degrades.
- Managed nodes expose shared peers, peer `.conf` export, and apply those peer changes on `Redeploy`.
- Manual nodes do not have `Redeploy`, `Deploy history`, or peer management.

### Settings

The new **Settings** page allows:

- changing the admin password;
- uploading a custom TLS certificate/key;
- switching `http`/`https`;
- changing `WEB_PORT`.

Values are persisted into `.env`. Transport, port, and certificate changes require a container restart.

### Routing

View the current policy routing state:

- ip rules (fwmark → table mapping).
- ip routes in RU and VPN tables.
- iptables rule status (PREROUTING, OUTPUT, NAT).
- **Apply** (recreate rules) and **Reset** (delete rules) buttons.

### GeoIP

- Status of ipset `geoip_local`: prefix count and last update time.
- Manage countries in the local routing zone: RU, BY, KZ, and others.
- Add, edit, and delete enabled GeoIP sources from the UI.
- Source URL is built automatically from `country_code`, but can be overridden if needed.
- Manual trigger for updating the aggregated GeoIP ipset.

### Split DNS

- dnsmasq status: running/stopped, PID, listen address.
- DNS zone settings: `Local Zone DNS` and `VPN Zone DNS`, editable inline.
- Domain list: domain name, upstream (`Local Zone` / `VPN Zone`), enabled/disabled.
- Add domains via form (TLD, domain, or subdomain).
- Toggle individual domains without reloading.
- **Reload dnsmasq** button — force config regeneration and reload.

### Backup

- Download ZIP archive containing `config.db` (all data) + `env_snapshot.json` + `wg_configs/` + `geoip_cache/` + `certs/`.
- Upload archive to restore (drag & drop or file browser).
- After restore — restart container: `docker compose restart awg-jump`.

---

## AmneziaWG and Obfuscation

AmneziaWG is a WireGuard fork with Junk packet support and header replacement to bypass DPI inspection.

### Recommended Mode for the First Ingress Node

For the first ingress node `awg-jump` and for upstream VPS nodes, host kernel mode is the preferred option whenever the kernel module can be installed. In practice it is more stable than userspace `amneziawg-go` and avoids a class of issues around `tun`, userspace daemon lifecycle, and ambiguous kernel/userspace detection.

Before installing the module, install headers for the currently running kernel:

```bash
apt-get update
apt-get install --yes linux-headers-$(uname -r) dkms build-essential
```

Then install `amneziawg` and `amneziawg-tools`.

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

Verification after installation:

```bash
sudo modprobe amneziawg
ip link add awg_probe type amneziawg && ip link del awg_probe
```

For upstream nodes, ready-made host-side installers are stored under `node/scripts/` and are uploaded to the remote host as `/opt/awg-node/scripts/` during SSH deployment.

### Parameters

| Parameter | Side | Description |
|-----------|------|-------------|
| `Jc` | Client | Number of junk packets before handshake (4–12) |
| `Jmin` | Client | Minimum junk packet size (40–80 bytes) |
| `Jmax` | Client | Maximum junk packet size (< 1280 bytes) |
| `S1`–`S4` | Both | Padding in handshake and transport packets |
| `H1`–`H4` | Both | Replacement for standard WireGuard headers |

**Important:**

- `Jc`, `Jmin`, `Jmax` are specified only on the client side.
- `S1–S4`, `H1–H4` must match on both ends of the tunnel.
- `H1–H4` must be unique and must not equal standard WG values (1, 2, 3, 4).
- Parameters are generated automatically and stored in `config.db`. Included in backups.

### Two Parameter Sets

- **awg0** (server ↔ clients): `S*` and `H*` in server config; `Jc/Jmin/Jmax + S* + H*` in client config.
- **awg1** (jump → upstream node): `Jc/Jmin/Jmax + S* + H*` in awg1 `[Interface]` (jump is the client); only `S* + H*` in node config (node is the server).

---

## GeoIP and Routing

### How It Works

1. On first startup, no predefined GeoIP country is created; the local zone is configured by the user via UI/API.
2. On schedule per `GEOIP_UPDATE_CRON` and on manual updates, all enabled GeoIP sources are loaded from the database.
3. For each country, the source URL is built automatically from `country_code` using `GEOIP_SOURCE` unless an explicit `url` is stored.
4. All CIDR blocks are merged into a single ipset `geoip_local` (atomic swap — no connection disruption).
5. iptables mangle **PREROUTING** marks incoming packets from `awg0`:
   - dst in `geoip_local` → `fwmark LOCAL` → table 100 → `eth0`
   - dst not in `geoip_local` → `fwmark VPN` → table 200 → `awg1`
6. iptables mangle **OUTPUT** marks the container's own traffic (DNS queries etc.) by the same rules.
7. `iptables nat POSTROUTING MASQUERADE` provides NAT on both outgoing interfaces.

### Routing Inversion

The **Routing** page exposes the `invert_geoip` toggle.

- `invert_geoip = false` (Normal): the GeoIP local zone goes directly through `eth0`, while all other traffic goes through `awg1`.
- `invert_geoip = true` (Inverted): the logic is reversed, so the GeoIP local zone goes through `awg1`, while all other traffic goes directly through `eth0`.

This affects both client traffic arriving from `awg0` and the container's own outbound traffic, including `dnsmasq` DNS queries.

### Updating GeoIP

```bash
# Force update from UI: GeoIP page → Update button

# Force update from CLI:
docker exec awg-jump python -m backend.services.geoip_fetcher --force

# View ipset contents:
docker exec awg-jump ipset list geoip_local | head -20
```

---

## Split DNS

### How It Works

AWG Jump runs `dnsmasq` directly inside the container. AWG clients automatically receive the `awg0` interface IP as their DNS server (written into generated client configs).

```
Client → AWG DNS (10.10.0.1:53) → dnsmasq:
    domain in list (upstream=yandex / Local Zone) → local zone DNS servers
    everything else                               → VPN zone DNS servers
```

The container's own DNS traffic (dnsmasq queries to upstream resolvers) is routed via iptables OUTPUT using the same `geoip_local` ipset: local-zone IPs go through `eth0`, everything else goes through `awg1`.

### Default Domains

Created on first start:

```
ru, рф (Cyrillic TLD)
yandex.ru, yandex.net, yandex.com, ya.ru
vk.com, vk.ru, vkontakte.ru
mail.ru, list.ru, inbox.ru, bk.ru
ok.ru, rambler.ru
sberbank.ru, sbrf.ru, sber.ru
gosuslugi.ru, mos.ru
tinkoff.ru, avito.ru, ozon.ru, wildberries.ru
```

### Adding Domains

Via the web interface (**Split DNS** page) or API:

```bash
# Add domain via API
curl -X POST https://<host>/api/dns/domains \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"domain": "example.ru", "upstream": "yandex"}'
```

`upstream` values:
- `yandex` — use local zone DNS servers
- `default` — use VPN zone DNS servers

The string values `yandex` and `default` are kept in the database for backward compatibility, but the UI presents them as `Local Zone` and `VPN Zone`.

### Config Reload

Whenever domains or DNS zone settings change, the dnsmasq config is automatically regenerated and reloaded via `SIGHUP` (no connection interruption).

### DNS Zone Settings

Settings are stored in a dedicated `dns_zone_settings` table:

- `local` — DNS servers for local-zone domains.
- `vpn` — DNS servers for all other traffic.

API:

```bash
# Get zone settings
curl -H "Authorization: Bearer <token>" \
  https://<host>/api/dns/zones

# Update local zone DNS
curl -X PUT https://<host>/api/dns/zones/local \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"dns_servers":["77.88.8.8"],"description":"DNS for local routing zone"}'
```

### Generated dnsmasq Config

Written to `/etc/dnsmasq-awg.conf`:

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

## Upstream Nodes and Failover

### Node Model

Each node is stored in the `upstream_nodes` table:

- `host` — VPS IP or hostname
- `awg_port` — AWG UDP port on the node (default `51821`)
- `awg_address` — node IP in the VPN subnet (e.g. `10.20.0.3/32`)
- `provisioning_mode` — `managed | manual`
- `status` — `pending | deploying | online | degraded | offline | error`
- `is_active` — only one node is active at a time
- `priority` — failover order

### Node Deployment

The node is deployed from the web interface via SSH:

1. The jump server packages the `node/` directory into a tar archive and streams it to the VPS via SSH pipe.
2. Docker is installed on the VPS, and the `awg-node:local` image is built.
3. AWG keys and a node config are generated (with awg1 obfuscation parameters).
4. The node container is started.

SSH credentials (login/password) are **never stored** anywhere.

### Manual Nodes and Shared Peers

- `manual` nodes are imported from a standard AWG peer `.conf`.
- They can be activated and participate in failover like regular upstream nodes.
- Managed nodes can define extra shared peers, export them as `.conf`, and apply them to the remote `awg-node` on the next `Redeploy`.

### Failover

APScheduler checks every `NODE_HEALTH_CHECK_INTERVAL` seconds:

- If the active node's `last_handshake > 3 min` → status `degraded`, counter +1.
- When `counter >= NODE_FAILOVER_THRESHOLD` → auto-switch to the next node by `priority`.
- Switching: `wg set awg1 peer <pubkey> endpoint <new_host:port>` — no interface restart.

---

## Backup and Restore

### What's Included

| Content | Description |
|---------|-------------|
| `config.db` | All data: interfaces, keys, obfuscation params, peers, nodes, GeoIP sources, routing rules, **split DNS domains and DNS zone settings** |
| `env_snapshot.json` | Reference snapshot of public parameters (no passwords) |
| `wg_configs/` | Generated WireGuard config files |
| `geoip_cache/` | Cached GeoIP CIDR lists for fast ipset recovery after restart |
| `certs/` | TLS certificate and key from `/data/certs` |

**Not included:**
- `.env` file (environment passwords and keys)

### Export

```bash
# Via UI: Backup page → Download backup

# Via API:
curl -O -J https://<host>/api/backup/export \
  -H "Authorization: Bearer <token>"
```

### Import and Restore

1. **Backup** page → upload the `.zip` file.
2. After import, split DNS is automatically reloaded from the restored database.
3. Restart the container: `docker compose restart awg-jump`
4. Alembic migrations are applied automatically on startup.

### Automatic Backup Storage

Every export is automatically saved to `/data/backups/` inside the container. The list is available via `GET /api/backup/list`.

---

## TLS and Web Access

`awg-jump` serves UI/API directly via `uvicorn`. By default it listens on `https://0.0.0.0:${WEB_PORT}` using a self-signed certificate.

### Self-Signed Certificate

Generated by `nginx/generate-cert.sh` on first start of the main container:

```bash
openssl req -x509 -newkey rsa:4096 -sha256 -days 3650 \
  -nodes -keyout /certs/server.key -out /certs/server.crt \
  -subj "/CN=${TLS_COMMON_NAME}" \
  -addext "subjectAltName=IP:${TLS_COMMON_NAME},DNS:${TLS_COMMON_NAME}"
```

Valid for 10 years. Stored in `./data/certs/` (volume). Not regenerated if the file already exists.

### Regenerating the Certificate

```bash
rm ./data/certs/server.crt ./data/certs/server.key
docker compose restart awg-jump
```

---

## Project Structure

```
awg-jump/
├── .env.ru.example         # Configuration template (RU)
├── .env.en.example         # Configuration template (EN)
├── docker-compose.yml      # awg-jump + awg-node (optional)
├── Dockerfile              # Multi-stage: awg-builder + awg-tools + python + frontend + final
├── supervisord.conf        # Manages uvicorn inside the container
│
├── nginx/
│   └── generate-cert.sh    # Reused self-signed cert generator
│
├── node/                   # Upstream node image
│   ├── Dockerfile          # 2-stage: awg-builder + debian-slim
│   ├── entrypoint.sh       # Launch AWG from env variables
│   └── README.md
│
├── backend/
│   ├── main.py             # FastAPI app + lifespan
│   ├── config.py           # Pydantic Settings from .env
│   ├── database.py         # SQLAlchemy async + WAL mode
│   ├── scheduler.py        # APScheduler tasks
│   ├── models/
│   │   ├── interface.py    # AWG interfaces + obf_* params
│   │   ├── peer.py         # AWG peers
│   │   ├── upstream_node.py # Upstream nodes + deploy logs
│   │   ├── geoip.py        # GeoIP sources + display_name
│   │   ├── routing_rule.py # Routing rules
│   │   ├── dns_domain.py   # Split DNS domains
│   │   └── dns_zone_settings.py # DNS servers for local/vpn zones
│   ├── routers/
│   │   ├── auth.py         # JWT authentication
│   │   ├── interfaces.py   # Interfaces API
│   │   ├── peers.py        # Peers API
│   │   ├── nodes.py        # Nodes API + deploy + SSE
│   │   ├── routing.py      # Routing API
│   │   ├── geoip.py        # GeoIP API and local-zone country management
│   │   ├── dns.py          # Split DNS API and DNS zone settings
│   │   ├── system.py       # System information
│   │   └── backup.py       # Export/import
│   ├── services/
│   │   ├── awg.py          # amneziawg-go management
│   │   ├── routing.py      # ip rule/route + iptables
│   │   ├── ipset_manager.py # ipset management
│   │   ├── geoip_fetcher.py # GeoIP download and geoip_local aggregation
│   │   ├── node_deployer.py # SSH node deployment
│   │   └── dns_manager.py  # dnsmasq management
│   └── alembic/            # DB migrations
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
│   │   ├── types.ts        # TypeScript types
│   │   ├── pages/
│   │   │   ├── Dashboard.tsx
│   │   │   ├── Interfaces.tsx
│   │   │   ├── Peers.tsx
│   │   │   ├── Nodes.tsx
│   │   │   ├── Routing.tsx
│   │   │   ├── GeoIP.tsx
│   │   │   ├── DNS.tsx     # Split DNS management
│   │   │   └── Backup.tsx
│   │   └── components/
│   │       ├── Layout.tsx  # Sidebar navigation
│   │       ├── Modal.tsx
│   │       └── StatusBadge.tsx
│   └── public/
│       └── favicon.svg
│
├── scripts/
│   ├── entrypoint.sh       # Container startup script
│   └── smoke_test.sh       # Basic smoke tests
│
└── docs/
    ├── README_RU.md        # Russian documentation
    └── README_EN.md        # This document
```

---

## Development and Debugging

### Run Backend Locally

```bash
cd /opt/awg-jump
pip install -r backend/requirements.txt
cd backend && uvicorn main:app --reload --port 8080
```

### Build and Run

```bash
# Full stack
docker compose up --build

# Rebuild only awg-jump
docker compose build awg-jump && docker compose up -d awg-jump
```

### Debugging

```bash
# awg-jump logs
docker logs -f awg-jump

# Shell inside container
docker exec -it awg-jump bash

# AWG interface status
docker exec awg-jump wg show

# ipset status
docker exec awg-jump ipset list geoip_local | head -20

# ip rules and routes
docker exec awg-jump ip rule show
docker exec awg-jump ip route show table 100
docker exec awg-jump ip route show table 200

# iptables rules
docker exec awg-jump iptables -t mangle -L -v -n
docker exec awg-jump iptables -t nat -L -v -n

# dnsmasq status
docker exec awg-jump cat /etc/dnsmasq-awg.conf
docker exec awg-jump cat /etc/resolv.conf

# Force GeoIP update
docker exec awg-jump python -m backend.services.geoip_fetcher --force

# Node status
docker exec awg-jump python -m backend.services.node_deployer --status
```

### API

All endpoints are available at `https://<host>/api/`. Documentation (when enabled):

```bash
ENABLE_API_DOCS=true  # in .env
# Then: https://<host>/api/docs
```

### Database

```bash
# Browse database
docker exec awg-jump sqlite3 /data/config.db ".tables"
docker exec awg-jump sqlite3 /data/config.db \
  "SELECT domain, upstream, enabled FROM dns_domains;"

# Manual database backup
docker exec awg-jump sqlite3 /data/config.db ".backup /data/config.db.manual"
```

### Smoke Test

```bash
./scripts/smoke_test.sh
```

---

*AWG Jump uses [AmneziaWG](https://github.com/amnezia-vpn/amneziawg-go) — a WireGuard fork with traffic obfuscation support.*
