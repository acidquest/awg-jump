from __future__ import annotations

import ipaddress
import logging
import re
import subprocess


logger = logging.getLogger(__name__)

TABLE_NAME = "awg_gw"


def _run(args: list[str], input_data: str | None = None) -> tuple[int, str]:
    result = subprocess.run(
        args,
        input=input_data,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode, ((result.stdout or "") + (result.stderr or "")).strip()


def table_exists() -> bool:
    rc, _ = _run(["nft", "list", "table", "ip", TABLE_NAME])
    return rc == 0


def ensure_table() -> None:
    if table_exists():
        return
    rc, out = _run(["nft", "add", "table", "ip", TABLE_NAME])
    if rc != 0:
        raise RuntimeError(f"nft add table ip {TABLE_NAME} failed: {out}")


def exists(name: str) -> bool:
    rc, _ = _run(["nft", "list", "set", "ip", TABLE_NAME, name])
    return rc == 0


def count(name: str) -> int:
    rc, out = _run(["nft", "list", "set", "ip", TABLE_NAME, name])
    if rc != 0:
        return 0
    match = re.search(r"elements\s*=\s*\{(.*)\}", out, flags=re.DOTALL)
    if not match:
        return 0
    raw = match.group(1).strip()
    if not raw:
        return 0
    return len([item for item in raw.split(",") if item.strip()])


def create(name: str) -> None:
    ensure_table()
    rc, out = _run(
        [
            "nft",
            "add",
            "set",
            "ip",
            TABLE_NAME,
            name,
            "{",
            "type",
            "ipv4_addr",
            ";",
            "flags",
            "interval",
            ";",
            "auto-merge",
            ";",
            "}",
        ]
    )
    if rc != 0 and "File exists" not in out:
        raise RuntimeError(f"nft add set ip {TABLE_NAME} {name} failed: {out}")


def destroy(name: str) -> None:
    rc, out = _run(["nft", "delete", "set", "ip", TABLE_NAME, name])
    if rc != 0 and "No such file or directory" not in out:
        logger.warning("nft delete set %s: %s", name, out)


def flush_set(name: str) -> None:
    if not exists(name):
        create(name)
    rc, out = _run(["nft", "flush", "set", "ip", TABLE_NAME, name])
    if rc != 0:
        raise RuntimeError(f"nft flush set ip {TABLE_NAME} {name} failed: {out}")


def _normalize_prefixes(name: str, prefixes: list[str]) -> list[str]:
    normalized: list[str] = []
    for prefix in prefixes:
        try:
            network = ipaddress.ip_network(prefix, strict=False)
        except ValueError:
            logger.warning("Skipping invalid prefix for nft set %s: %s", name, prefix)
            continue
        if network.version != 4:
            logger.warning("Skipping non-IPv4 prefix for nft set %s: %s", name, prefix)
            continue
        normalized.append(str(network))
    return normalized


def _populate(name: str, prefixes: list[str]) -> None:
    normalized = _normalize_prefixes(name, prefixes)
    if not normalized:
        return
    script = "\n".join(
        [
            f"add element ip {TABLE_NAME} {name} {{ {', '.join(normalized)} }}",
            "",
        ]
    )
    rc, out = _run(["nft", "-f", "-"], input_data=script)
    if rc != 0:
        raise RuntimeError(f"nft add element ip {TABLE_NAME} {name} failed: {out}")


def create_or_update(name: str, prefixes: list[str]) -> None:
    ensure_table()
    if not exists(name):
        create(name)
    flush_set(name)
    _populate(name, prefixes)


def flush_all() -> None:
    rc, out = _run(["nft", "delete", "table", "ip", TABLE_NAME])
    if rc != 0 and "No such file or directory" not in out:
        logger.warning("nft delete table ip %s: %s", TABLE_NAME, out)
