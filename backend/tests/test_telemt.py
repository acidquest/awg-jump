from pathlib import Path

from httpx import AsyncClient

import backend.services.telemt as telemt_svc


async def test_get_telemt_page(client: AsyncClient, auth_headers: dict, monkeypatch) -> None:
    monkeypatch.setattr(telemt_svc, "runtime_enabled", lambda: True)
    monkeypatch.setattr(
        telemt_svc,
        "get_service_status",
        lambda: {"enabled": True, "running": True, "status": "running", "message": "telemt RUNNING"},
    )

    async def fake_links():
        return {
            "alice": {
                "classic": [],
                "secure": [],
                "tls": ["tg://proxy?server=example.com&port=443&secret=eeabc"],
            }
        }

    async def fake_latest_version():
        return {
            "latest_version": "3.4.3",
            "latest_release_url": "https://github.com/telemt/telemt/releases/tag/3.4.3",
            "version_status": "latest",
        }

    monkeypatch.setattr(telemt_svc, "fetch_links", fake_links)
    monkeypatch.setattr(telemt_svc, "fetch_latest_version", fake_latest_version)

    create_resp = await client.post(
        "/api/telemt/users",
        headers=auth_headers,
        json={"username": "alice", "secret_hex": "0123456789abcdef0123456789abcdef", "enabled": True},
    )
    assert create_resp.status_code == 201, create_resp.text

    resp = await client.get("/api/telemt", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["feature_enabled"] is True
    assert data["service"]["running"] is True
    assert data["users"][0]["username"] == "alice"
    assert data["users"][0]["address"].startswith("tg://proxy?")


async def test_update_telemt_settings_marks_restart_required(
    client: AsyncClient,
    auth_headers: dict,
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(telemt_svc, "runtime_enabled", lambda: True)
    monkeypatch.setattr(
        telemt_svc,
        "get_service_status",
        lambda: {"enabled": True, "running": False, "status": "stopped", "message": "telemt STOPPED"},
    )
    monkeypatch.setattr(telemt_svc, "fetch_links", lambda: {})

    from backend.config import settings

    settings.env_file_path = str(tmp_path / ".env")
    Path(settings.env_file_path).write_text("TELEMT_ENABLED=on\nTELEMT_PORT=443\n", encoding="utf-8")

    resp = await client.put(
        "/api/telemt/settings",
        headers=auth_headers,
        json={
            "config_text": """
[general]
use_middle_proxy = true
log_level = "normal"

[general.modes]
classic = false
secure = false
tls = true

[general.links]
show = "*"

[server]
port = 444

[server.api]
enabled = true
listen = "127.0.0.1:9091"
whitelist = ["127.0.0.1/32", "::1/128"]
minimal_runtime_enabled = false
minimal_runtime_cache_ttl_ms = 1000

[[server.listeners]]
ip = "0.0.0.0"

[censorship]
tls_domain = "example.com"
mask = true
tls_emulation = true
tls_front_dir = "tlsfront"

[access.users]
""",
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["restart_required"] is True
    assert "TELEMT_PORT=444" in Path(settings.env_file_path).read_text(encoding="utf-8")


async def test_telemt_service_action(client: AsyncClient, auth_headers: dict, monkeypatch) -> None:
    monkeypatch.setattr(telemt_svc, "runtime_enabled", lambda: True)
    monkeypatch.setattr(
        telemt_svc,
        "control_service",
        lambda action: {
            "enabled": True,
            "running": action != "stop",
            "status": "running" if action != "stop" else "stopped",
            "action": action,
            "ok": True,
            "command_output": f"{action} ok",
        },
    )
    monkeypatch.setattr(telemt_svc, "get_service_status", lambda: {"enabled": True, "running": False, "status": "stopped"})

    resp = await client.post("/api/telemt/service/restart", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True
    assert data["action"] == "restart"
