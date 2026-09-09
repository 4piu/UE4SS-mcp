"""Enable/disable arbitrary mods in a UE4SS install's mods.txt.

Placing a mod's own files under Mods/<name>/ is just normal file
editing (the same tools used for anything else) -- not UE4SS-specific,
so there's no MCP tool for that here. What *is* real UE4SS domain
knowledge worth a tool is mods.txt's insertion quirk: UE4SS ships a
"; Built-in keybinds, do not move up!" comment directly above its
built-in Keybinds mod, and a naive append would separate that comment
from what it's warning about. `set_mod_enabled` is the generalized form
of the logic `bridge_install.py` originally had only for its own mod.
"""

from __future__ import annotations

from pathlib import Path


def set_mod_enabled(mods_txt: Path, mod_name: str, enabled: bool) -> bool:
    """Returns True if mods.txt was changed."""
    lines = mods_txt.read_text(encoding="utf-8").splitlines() if mods_txt.exists() else []
    desired = f"{mod_name} : {1 if enabled else 0}"

    for i, line in enumerate(lines):
        name = line.split(":", 1)[0].strip()
        if name == mod_name:
            currently_enabled = line.strip().endswith(": 1") or line.strip().endswith(":1")
            if currently_enabled == enabled:
                return False
            lines[i] = desired
            mods_txt.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return True

    if not enabled:
        return False  # not listed at all is already effectively "off"

    # Insert before a trailing "Keybinds" line if present, otherwise
    # append. Step back over a comment line directly above it so that
    # comment stays attached to Keybinds, not to the newly inserted line.
    insert_at = len(lines)
    for i, line in enumerate(lines):
        if line.split(":", 1)[0].strip() == "Keybinds":
            insert_at = i
            if insert_at > 0 and lines[insert_at - 1].lstrip().startswith(";"):
                insert_at -= 1
            break
    lines.insert(insert_at, desired)
    mods_txt.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def enable_mod(game_install_path: str, mod_name: str) -> dict:
    """Enable a mod that already has its files under Mods/<mod_name>/.

    Errors (rather than silently enabling a name that isn't a real mod
    folder) if that folder doesn't exist yet -- place the mod's files
    there first.
    """
    install_dir = Path(game_install_path)
    mods_dir = install_dir / "Mods"
    if not mods_dir.exists():
        return {"enabled": False, "error": f"no Mods/ folder found under {install_dir} -- is UE4SS installed there?"}
    if not (mods_dir / mod_name).is_dir():
        return {
            "enabled": False,
            "error": f"no Mods/{mod_name}/ folder found -- place the mod's files there first, then enable it",
        }
    changed = set_mod_enabled(mods_dir / "mods.txt", mod_name, True)
    return {"enabled": True, "mods_txt_updated": changed}


def disable_mod(game_install_path: str, mod_name: str) -> dict:
    """Disable a mod in mods.txt. Doesn't require the mod's files to
    still exist -- also useful for turning off a mod you're about to
    remove, or one you suspect is causing a problem.
    """
    install_dir = Path(game_install_path)
    mods_dir = install_dir / "Mods"
    if not mods_dir.exists():
        return {"disabled": False, "error": f"no Mods/ folder found under {install_dir} -- is UE4SS installed there?"}
    changed = set_mod_enabled(mods_dir / "mods.txt", mod_name, False)
    return {"disabled": True, "mods_txt_updated": changed}
