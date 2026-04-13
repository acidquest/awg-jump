from __future__ import annotations

import ipaddress
import logging
import subprocess


logger = logging.getLogger(__name__)

_HASHSIZE = 4096
_MAXELEM = 131072


def _run(args: list[str], input_data: str | None = None) -> tuple[int, str]:
    result = subprocess.run(
        args,
        input=input_data,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode, ((result.stdout or "") + (result.stderr or "")).strip()


def exists(name: str) -> bool:
    rc, _ = _run(["ipset", "list", "-n", name])
    return rc == 0


def count(name: str) -> int:
    try:
        rc, out = _run(["ipset", "list", name])
    except FileNotFoundError:
        return 0
    if rc != 0:
        return 0
    for line in out.splitlines():
        if line.startswith("Number of entries:"):
            try:
                return int(line.split(":", 1)[1].strip())
            except (ValueError, IndexError):
                return 0
    return 0


def create(name: str) -> None:
    rc, out = _run(
        [
            "ipset",
            "create",
            name,
            "hash:net",
            "family",
            "inet",
            "hashsize",
            str(_HASHSIZE),
            "maxelem",
            str(_MAXELEM),
        ]
    )
    if rc != 0:
        raise RuntimeError(f"ipset create {name} failed: {out}")


def destroy(name: str) -> None:
    rc, out = _run(["ipset", "destroy", name])
    if rc != 0:
        logger.warning("ipset destroy %s: %s", name, out)


def _populate(name: str, prefixes: list[str]) -> None:
    if not prefixes:
        return
    expanded: list[str] = []
    for prefix in prefixes:
        try:
            network = ipaddress.ip_network(prefix, strict=False)
        except ValueError:
            logger.warning("Skipping invalid prefix for ipset %s: %s", name, prefix)
            continue
        if network.version != 4:
            logger.warning("Skipping non-IPv4 prefix for ipset %s: %s", name, prefix)
            continue
        # ipset hash:net family inet rejects 0.0.0.0/0, so expand it into two /1 blocks.
        if str(network) == "0.0.0.0/0":
            expanded.extend(["0.0.0.0/1", "128.0.0.0/1"])
            continue
        expanded.append(str(network))
    if not expanded:
        return
    restore_input = "\n".join(f"add {name} {prefix}" for prefix in expanded) + "\n"
    rc, out = _run(["ipset", "restore"], input_data=restore_input)
    if rc != 0:
        raise RuntimeError(f"ipset restore for {name} failed: {out}")


def create_or_update(name: str, prefixes: list[str]) -> None:
    if not exists(name):
        create(name)
        _populate(name, prefixes)
        return

    temp_name = f"{name}_new"
    if exists(temp_name):
        destroy(temp_name)

    create(temp_name)
    _populate(temp_name, prefixes)

    rc, out = _run(["ipset", "swap", temp_name, name])
    if rc != 0:
        destroy(temp_name)
        raise RuntimeError(f"ipset swap failed: {out}")

    destroy(temp_name)
