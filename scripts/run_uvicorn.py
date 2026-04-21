#!/usr/bin/env python3
from __future__ import annotations

import os
import sys

from backend.config import reload_settings, settings


def main() -> int:
    reload_settings()
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "backend.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        str(settings.web_port),
        "--workers",
        "1",
    ]
    if not settings.web_access_log:
        cmd.append("--no-access-log")
    if settings.web_mode.lower() == "https":
        cmd.extend(
            [
                "--ssl-certfile",
                settings.tls_cert_path,
                "--ssl-keyfile",
                settings.tls_key_path,
            ]
        )
    print(
        f"[uvicorn] Starting web server on {settings.web_mode.lower()}://0.0.0.0:{settings.web_port} "
        f"(access_log={'on' if settings.web_access_log else 'off'})",
        flush=True,
    )
    os.execv(cmd[0], cmd)


if __name__ == "__main__":
    raise SystemExit(main())
