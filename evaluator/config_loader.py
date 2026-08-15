"""
evaluator/config_loader.py
Single loader module for configuration files (A0.2).

Own copy, not shared via import path, matching mcp/config_loader.py and
orchestrator/config_loader.py's existing precedent -- each service's Docker
build context is self-contained (A1), so shared utility code is duplicated
per service rather than imported across a context boundary.
"""

from __future__ import annotations

import os
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any


def _config_dir() -> Path:
    env = os.environ.get("CONFIG_DIR")
    if env:
        return Path(env)
    here = Path(__file__).resolve().parent
    for candidate in [here, here.parent, here.parent.parent]:
        p = candidate / "config"
        if p.is_dir():
            return p
    raise RuntimeError(
        "Cannot locate config/ directory. Set CONFIG_DIR environment variable."
    )


class _Namespace:
    """Wraps a nested dict for attribute access. Read-only."""

    def __init__(self, data: dict[str, Any]) -> None:
        object.__setattr__(self, "_data", data)

    def __getattr__(self, name: str) -> Any:
        data = object.__getattribute__(self, "_data")
        if name not in data:
            raise AttributeError(
                f"Config key '{name}' not found. "
                "Check that all keys are present in config/statesync.toml and config/flags.toml."
            )
        value = data[name]
        if isinstance(value, dict):
            return _Namespace(value)
        return value

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("Config is read-only. Do not mutate at runtime.")

    def __repr__(self) -> str:
        data = object.__getattribute__(self, "_data")
        return f"_Namespace({data!r})"

    def get(self, key: str, default: Any = None) -> Any:
        data = object.__getattribute__(self, "_data")
        value = data.get(key, default)
        if isinstance(value, dict):
            return _Namespace(value)
        return value

    def as_dict(self) -> dict[str, Any]:
        return dict(object.__getattribute__(self, "_data"))


@lru_cache(maxsize=1)
def _load_toml(path: Path) -> dict[str, Any]:
    with open(path, "rb") as f:
        return tomllib.load(f)


@lru_cache(maxsize=1)
def _load_cfg() -> _Namespace:
    return _Namespace(_load_toml(_config_dir() / "statesync.toml"))


@lru_cache(maxsize=1)
def _load_flags() -> _Namespace:
    return _Namespace(_load_toml(_config_dir() / "flags.toml"))


cfg: _Namespace = _load_cfg()
flags: _Namespace = _load_flags()
