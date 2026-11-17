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

_SETTINGS_FILE = "UE4SS-settings.ini"


def _read_overrides_section(settings_ini: Path) -> list[tuple[str, str]]:
    """Raw (key, value) pairs from UE4SS-settings.ini's [Overrides]
    section. Parsed by hand rather than with configparser: UE4SS's own
    ini format repeats keys (`+ModsFolderPaths = ...` once per extra
    folder), which configparser rejects as duplicates."""
    if not settings_ini.exists():
        return []
    pairs: list[tuple[str, str]] = []
    in_overrides = False
    for raw in settings_ini.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            in_overrides = line[1:-1].strip().lower() == "overrides"
            continue
        if in_overrides and "=" in line:
            key, value = line.split("=", 1)
            pairs.append((key.strip(), value.strip()))
    return pairs


def mods_config_warnings(install_dir: Path) -> list[str]:
    """Warn when UE4SS-settings.ini points UE4SS somewhere other than
    the `<install_dir>/Mods/mods.txt` these tools edit.

    UE4SS v4.0.0 added `ControllingModsTxt` (when set, ONLY that file is
    read, so edits to Mods/mods.txt are silently ignored) and
    `+ModsFolderPaths` (extra mod folders, each with its own mods.txt);
    `ModsFolderPath` (relocates the main Mods folder) predates v4. All
    three confirmed against v4.0.0's SettingsManager.cpp/UE4SSProgram.cpp.
    Supporting them fully is deliberately not attempted -- a warning is
    enough to stop an agent trusting a mods.txt edit that won't take
    effect.
    """
    warnings: list[str] = []
    for key, value in _read_overrides_section(install_dir / _SETTINGS_FILE):
        if not value:
            continue
        if key == "ControllingModsTxt":
            warnings.append(
                f"UE4SS-settings.ini sets ControllingModsTxt = {value} -- UE4SS reads ONLY that file, "
                "so changes to Mods/mods.txt here have no effect; edit that file instead"
            )
        elif key == "ModsFolderPath":
            warnings.append(
                f"UE4SS-settings.ini sets ModsFolderPath = {value} -- UE4SS loads mods from there, "
                "not from Mods/ here"
            )
        elif key.startswith("+") and key[1:].strip() == "ModsFolderPaths":
            warnings.append(
                f"UE4SS-settings.ini adds an extra mods folder ({value}) -- mods in it, and its own "
                "mods.txt, aren't managed by these tools"
            )
    return warnings


def _with_warnings(result: dict, install_dir: Path) -> dict:
    warnings = mods_config_warnings(install_dir)
    if warnings:
        result["warnings"] = warnings
    return result


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
        return _with_warnings(
            {
                "enabled": False,
                "error": f"no Mods/{mod_name}/ folder found -- place the mod's files there first, then enable it",
            },
            install_dir,
        )
    changed = set_mod_enabled(mods_dir / "mods.txt", mod_name, True)
    return _with_warnings({"enabled": True, "mods_txt_updated": changed}, install_dir)


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
    return _with_warnings({"disabled": True, "mods_txt_updated": changed}, install_dir)
