"""
orchestrator/config_loader.py
Single loader module for all three config artifacts.
Imported by every service that needs configuration values.
No numeric literals in agent source — all values come from here.

Usage:
    from config_loader import cfg, flags

    timeout = cfg.mcp.statement_timeout_ms
    if flags.memory.enabled:
        ...
"""

from __future__ import annotations

import os
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any

# ── Config directory ──────────────────────────────────────────────────────────
# Resolved from CONFIG_DIR env var; fallback to /app/config (Docker) or
# <repo_root>/config (local dev).
def _config_dir() -> Path:
    env = os.environ.get("CONFIG_DIR")
    if env:
        return Path(env)
    # Walk up from this file looking for config/
    here = Path(__file__).resolve().parent
    for candidate in [here, here.parent, here.parent.parent]:
        p = candidate / "config"
        if p.is_dir():
            return p
    raise RuntimeError(
        "Cannot locate config/ directory. Set CONFIG_DIR environment variable."
    )


# ── Typed accessor (dot-access dict wrapper) ──────────────────────────────────
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


# ── Loader ────────────────────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def _load_toml(path: Path) -> dict[str, Any]:
    with open(path, "rb") as f:
        return tomllib.load(f)


@lru_cache(maxsize=1)
def _load_cfg() -> _Namespace:
    config_dir = _config_dir()
    data = _load_toml(config_dir / "statesync.toml")
    return _Namespace(data)


@lru_cache(maxsize=1)
def _load_flags() -> _Namespace:
    config_dir = _config_dir()
    data = _load_toml(config_dir / "flags.toml")
    return _Namespace(data)


# ── Public singletons ─────────────────────────────────────────────────────────
# Import these, do not instantiate _Namespace directly.
cfg: _Namespace = _load_cfg()        # statesync.toml  — locked constants
flags: _Namespace = _load_flags()    # flags.toml      — feature flags


# ── Snapshot helper (each question's flags_snapshot) ───────────────────────────
def snapshot_flags() -> dict[str, bool | str]:
    """
    Returns a flat {key: value} snapshot of all flags for inclusion in
    StateSyncState.flags_snapshot. Called once at query entry.
    Nested sections are flattened with dot notation, e.g.
    {"memory.enabled": True, "routing.table": ["sql", "docs", "hybrid"]}.
    """
    raw = flags.as_dict()

    def _flatten(obj: dict[str, Any], prefix: str = "") -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in obj.items():
            full_key = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                out.update(_flatten(v, full_key))
            else:
                out[full_key] = v
        return out

    return _flatten(raw)
