from __future__ import annotations

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
    restore_input = "\n".join(f"add {name} {prefix}" for prefix in prefixes) + "\n"
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
