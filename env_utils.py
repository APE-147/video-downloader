"""Utilities for loading project-local environment overrides."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Optional, Tuple, Union


def _project_root() -> Path:
    return Path(__file__).resolve().parent


def _parse_line(line: str) -> Tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("export "):
        stripped = stripped[len("export "):].strip()
    if "=" not in stripped:
        return None
    key, value = stripped.split("=", 1)
    key = key.strip()
    if not key:
        return None
    value = value.strip()
    if value and value[0] in {'"', "'"} and value[-1] == value[0]:
        value = value[1:-1]
    return key, value


def load_env_file(filename: str = ".env") -> None:
    root = _project_root()
    env_path = root / filename
    if not env_path.exists():
        return
    try:
        lines: Iterable[str]
        with env_path.open("r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return

    for line in lines:
        parsed = _parse_line(line)
        if not parsed:
            continue
        key, value = parsed
        if key in os.environ:
            continue
        os.environ[key] = value


def env_value(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.getenv(name)
    if value is None:
        return default
    stripped = value.strip()
    return stripped if stripped else default


def env_path(name: str, default: Union[str, Path, None]) -> Optional[str]:
    candidate: Optional[str]
    if default is None:
        candidate = env_value(name)
    else:
        candidate = env_value(name, str(default))
    if candidate is None:
        return None
    return str(Path(candidate).expanduser())
