"""Installs the bridge mod into a target game's UE4SS Mods/ folder."""

from __future__ import annotations

import secrets
import shutil
from pathlib import Path

from ue4ss_mcp_server.cache import cache_root

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


def _enable_in_mods_txt(mods_txt: Path) -> bool:
    """Returns True if mods.txt was changed."""
    lines = mods_txt.read_text(encoding="utf-8").splitlines() if mods_txt.exists() else []

    for i, line in enumerate(lines):
        name = line.split(":", 1)[0].strip()
        if name == _MOD_NAME:
            if line.strip().endswith(": 1") or line.strip().endswith(":1"):
                return False
            lines[i] = f"{_MOD_NAME} : 1"
            mods_txt.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return True

    # Insert before a trailing "Keybinds" line if present (UE4SS ships a
    # comment warning not to move that mod down), otherwise append. Also
    # step back over a comment line directly above it (UE4SS's default
    # mods.txt has "; Built-in keybinds, do not move up!" right above
    # Keybinds) so that comment stays attached to Keybinds, not to us.
    insert_at = len(lines)
    for i, line in enumerate(lines):
        if line.split(":", 1)[0].strip() == "Keybinds":
            insert_at = i
            if insert_at > 0 and lines[insert_at - 1].lstrip().startswith(";"):
                insert_at -= 1
            break
    lines.insert(insert_at, f"{_MOD_NAME} : 1")
    mods_txt.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


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

    mods_txt_updated = _enable_in_mods_txt(mods_dir / "mods.txt")

    return {
        "installed": True,
        "mod_dir": str(mod_dir),
        "mods_txt_updated": mods_txt_updated,
    }
