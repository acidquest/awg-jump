from __future__ import annotations

from pathlib import Path
from typing import Iterable

from backend.config import reload_settings, settings


def _strip_inline_comment(value: str) -> str:
    in_single = False
    in_double = False
    for idx, ch in enumerate(value):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            if idx == 0 or value[idx - 1].isspace():
                return value[:idx].rstrip()
    return value.strip()


def parse_env_file(path: str | Path | None = None) -> tuple[list[str], dict[str, str]]:
    env_path = Path(path or settings.env_file_path)
    if not env_path.exists():
        return [], {}
    lines = env_path.read_text(encoding="utf-8").splitlines()
    parsed: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        parsed[key.strip()] = _strip_inline_comment(raw_value)
    return lines, parsed


def update_env_file(updates: dict[str, str], *, path: str | Path | None = None) -> dict[str, str]:
    env_path = Path(path or settings.env_file_path)
    lines, current = parse_env_file(env_path)
    merged = {**current, **updates}
    remaining = dict(updates)
    rendered: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            rendered.append(line)
            continue
        key, _raw_value = line.split("=", 1)
        key = key.strip()
        if key in remaining:
            rendered.append(f"{key}={remaining.pop(key)}")
        else:
            rendered.append(f"{key}={merged[key]}")

    if remaining:
        if rendered and rendered[-1].strip():
            rendered.append("")
        for key, value in remaining.items():
            rendered.append(f"{key}={value}")

    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("\n".join(rendered).rstrip() + "\n", encoding="utf-8")
    reload_settings()
    return merged


def append_env_file(lines: Iterable[str], *, path: str | Path | None = None) -> None:
    env_path = Path(path or settings.env_file_path)
    env_path.parent.mkdir(parents=True, exist_ok=True)
    existing = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    suffix = "\n".join(lines).rstrip() + "\n"
    env_path.write_text(existing + suffix, encoding="utf-8")
    reload_settings()
