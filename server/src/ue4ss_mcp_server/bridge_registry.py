"""Persisted per-install bridge identity.

Each game/install gets its own unique pipe name + handshake token now,
replacing the single global token this project used before the
transport flip (see dev-notes/progress.md) -- needed now that this
server can know about, and connect to, more than one game's bridge at
once, and that two installs no longer collide on one hardcoded pipe
name. `bridge_id` throughout server.py is just the resolved install
path -- naturally unique per install, and meaningful without a lookup.
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path

from ue4ss_mcp_server.cache import cache_root

_REGISTRY_PATH = cache_root().parent / "bridges.json"
_PIPE_NAME_PREFIX = r"\\.\pipe\ue4ss-mcp-"


def resolve_bridge_id(game_install_path: str) -> str:
    return str(Path(game_install_path).resolve())


def _load() -> dict:
    if not _REGISTRY_PATH.exists():
        return {}
    try:
        return json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save(registry: dict) -> None:
    _REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _REGISTRY_PATH.write_text(json.dumps(registry, indent=2), encoding="utf-8")


def get_or_create_bridge(game_install_path: str) -> dict:
    """Returns `{"pipe_name": ..., "token": ...}` for this install,
    creating and persisting a new identity the first time this path is
    seen, reusing it on every later call (same install -> same pipe).
    """
    bridge_id = resolve_bridge_id(game_install_path)
    registry = _load()
    entry = registry.get(bridge_id)
    if entry and entry.get("pipe_name") and entry.get("token"):
        return entry

    entry = {
        "pipe_name": _PIPE_NAME_PREFIX + secrets.token_hex(4),
        "token": secrets.token_hex(16),
    }
    registry[bridge_id] = entry
    _save(registry)
    return entry


def list_bridges() -> dict[str, dict]:
    """Every known install's bridge_id -> `{pipe_name, token}` identity."""
    return _load()
