"""Installs the bridge mod into a target game's UE4SS Mods/ folder."""

from __future__ import annotations

import secrets
import shutil
from pathlib import Path

from ue4ss_mcp_server.cache import cache_root
from ue4ss_mcp_server.mod_management import set_mod_enabled

_TEMPLATE_DIR = Path(__file__).parent / "bridge_mod_template"
_MOD_NAME = "UE4SSMCPBridge"
_TOKEN_PLACEHOLDER = "__BRIDGE_TOKEN__"
_TOKEN_PATH = cache_root().parent / "bridge_token.txt"


def get_or_create_token() -> str:
    if _TOKEN_PATH.exists():
        existing = _TOKEN_PATH.read_text(encoding="utf-8").strip()
        if existing:
            return existing

    token = secrets.token_hex(16)
    _TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    _TOKEN_PATH.write_text(token, encoding="utf-8")
    return token


def install_bridge_mod(game_install_path: str) -> dict:
    """Install/update the bridge mod in `<game_install_path>/Mods/`.

    `game_install_path` is the UE4SS install directory (the folder
    containing UE4SS.dll and Mods/), not the game's root install.
    """
    install_dir = Path(game_install_path)
    mods_dir = install_dir / "Mods"
    if not mods_dir.exists():
        return {
            "installed": False,
            "error": f"no Mods/ folder found under {install_dir} -- is UE4SS installed there?",
        }

    token = get_or_create_token()
    mod_dir = mods_dir / _MOD_NAME
    if mod_dir.exists():
        shutil.rmtree(mod_dir)
    shutil.copytree(_TEMPLATE_DIR, mod_dir)

    main_lua = mod_dir / "scripts" / "main.lua"
    content = main_lua.read_text(encoding="utf-8")
    main_lua.write_text(content.replace(_TOKEN_PLACEHOLDER, token), encoding="utf-8")

    mods_txt_updated = set_mod_enabled(mods_dir / "mods.txt", _MOD_NAME, True)

    return {
        "installed": True,
        "mod_dir": str(mod_dir),
        "mods_txt_updated": mods_txt_updated,
    }
