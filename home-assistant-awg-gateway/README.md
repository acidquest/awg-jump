# AWG Gateway Home Assistant Integration

Custom Home Assistant integration for AWG Gateway installed through HACS.

## Features

- Gateway telemetry from `GET /api/access/status`
- VPN tunnel control via `POST /api/access/control/tunnel`
- Kill switch control via `POST /api/access/control/kill-switch`
- `device_tracker` entities from `GET /api/access/devices`

## Installation

1. Add this repository to HACS as a custom integration repository.
2. Install `AWG Gateway`.
3. Restart Home Assistant.
4. Add the integration from the UI.

## Configuration

The config flow asks for:

- Gateway host
- Gateway port
- HTTPS on/off
- API key
- SSL verification on/off
- Poll interval

The integration uses the gateway key-based API documented in `gateway/docs/api_access_ru.md`.

## Device tracker scope

The default scope is `marked`, which matches the gateway UI recommendation for external integrations.
It can be changed later in integration options.
