"""
IPSet manager — управление ipset через subprocess.
Atomic swap для обновления без потерь пакетов.
"""
import logging
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)

_HASHSIZE = 4096
_MAXELEM = 131072  # 128k — с запасом для ~7k RU префиксов


def _run(args: list[str], input_data: Optional[str] = None) -> tuple[int, str]:
    result = subprocess.run(
        args,
        input=input_data,
        capture_output=True,
        text=True,
    )
    return result.returncode, (result.stdout + result.stderr).strip()


def exists(name: str) -> bool:
    rc, _ = _run(["ipset", "list", "-n", name])
    return rc == 0


def create(name: str) -> None:
    rc, out = _run([
        "ipset", "create", name, "hash:net",
        "family", "inet",
        "hashsize", str(_HASHSIZE),
        "maxelem", str(_MAXELEM),
    ])
    if rc != 0:
        raise RuntimeError(f"ipset create {name} failed: {out}")


def destroy(name: str) -> None:
    rc, out = _run(["ipset", "destroy", name])
    if rc != 0:
        logger.warning("ipset destroy %s: %s", name, out)


def _populate(name: str, prefixes: list[str]) -> None:
    """Batch-добавление префиксов через ipset restore."""
    if not prefixes:
        return
    # Формат ipset restore: "add <name> <prefix>" — по одной строке
    restore_input = "\n".join(f"add {name} {p}" for p in prefixes) + "\n"
    rc, out = _run(["ipset", "restore"], input_data=restore_input)
    if rc != 0:
        raise RuntimeError(f"ipset restore for {name} failed: {out}")


def create_or_update(name: str, prefixes: list[str]) -> None:
    """
    Atomic swap обновление ipset.
    Если set не существует — создаём напрямую.
    Если существует — создаём {name}_new, наполняем, swap, destroy old.
    """
    if not exists(name):
        logger.info("Creating new ipset %s with %d prefixes", name, len(prefixes))
        create(name)
        _populate(name, prefixes)
        return

    tmp_name = f"{name}_new"

    # Убрать старый tmp если вдруг остался с прошлого раза
    if exists(tmp_name):
        destroy(tmp_name)

    logger.info(
        "Atomic swap: creating %s with %d prefixes, then swap → %s",
        tmp_name, len(prefixes), name,
    )
    create(tmp_name)
    _populate(tmp_name, prefixes)

    # swap — атомарная операция, пакеты не теряются
    rc, out = _run(["ipset", "swap", tmp_name, name])
    if rc != 0:
        destroy(tmp_name)
        raise RuntimeError(f"ipset swap failed: {out}")

    destroy(tmp_name)
    logger.info("ipset %s updated successfully (%d prefixes)", name, len(prefixes))


def count(name: str) -> int:
    """Возвращает количество записей в ipset."""
    rc, out = _run(["ipset", "list", name])
    if rc != 0:
        return 0
    for line in out.splitlines():
        if line.startswith("Number of entries:"):
            try:
                return int(line.split(":")[1].strip())
            except (ValueError, IndexError):
                pass
    return 0


def list_sets() -> list[dict]:
    """Возвращает список ipset-ов с базовой информацией."""
    rc, out = _run(["ipset", "list", "-n"])
    if rc != 0:
        return []
    result = []
    for name in out.splitlines():
        name = name.strip()
        if name:
            result.append({"name": name, "count": count(name)})
    return result
