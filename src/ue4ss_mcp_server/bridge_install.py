"""Installs the bridge mod into a target game's UE4SS Mods/ folder."""

from __future__ import annotations

import shutil
from pathlib import Path

from ue4ss_mcp_server.bridge_registry import get_or_create_bridge, resolve_bridge_id
from ue4ss_mcp_server.mod_management import set_mod_enabled

_TEMPLATE_DIR = Path(__file__).parent / "bridge_mod_template"
_MOD_NAME = "UE4SSMCPBridge"
_TOKEN_PLACEHOLDER = "__BRIDGE_TOKEN__"
_PIPE_NAME_PLACEHOLDER = "__BRIDGE_PIPE_NAME__"


def _lua_escape_backslashes(s: str) -> str:
    """The placeholder sits inside a Lua double-quoted string literal in
    main.lua, so each literal backslash in the pipe name needs to be
    written as an escaped `\\\\` for Lua to parse it back to one."""
    return s.replace("\\", "\\\\")


def install_bridge_mod(game_install_path: str) -> dict:
    """Install/update the bridge mod in `<game_install_path>/Mods/`.

    `game_install_path` is the UE4SS install directory (the folder
    containing UE4SS.dll and Mods/), not the game's root install. Each
    install gets its own persisted pipe name + token (see
    bridge_registry.py) -- reinstalling the same path reuses both.
    """
    install_dir = Path(game_install_path)
    mods_dir = install_dir / "Mods"
    if not mods_dir.exists():
        return {
            "installed": False,
            "error": f"no Mods/ folder found under {install_dir} -- is UE4SS installed there?",
        }

    bridge = get_or_create_bridge(game_install_path)
    mod_dir = mods_dir / _MOD_NAME
    if mod_dir.exists():
        shutil.rmtree(mod_dir)
    shutil.copytree(_TEMPLATE_DIR, mod_dir)

    main_lua = mod_dir / "scripts" / "main.lua"
    content = main_lua.read_text(encoding="utf-8")
    content = content.replace(_TOKEN_PLACEHOLDER, bridge["token"])
    content = content.replace(_PIPE_NAME_PLACEHOLDER, _lua_escape_backslashes(bridge["pipe_name"]))
    main_lua.write_text(content, encoding="utf-8")

    mods_txt_updated = set_mod_enabled(mods_dir / "mods.txt", _MOD_NAME, True)

    return {
        "installed": True,
        "mod_dir": str(mod_dir),
        "mods_txt_updated": mods_txt_updated,
        "bridge_id": resolve_bridge_id(game_install_path),
    }
