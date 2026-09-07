"""Per-game compatibility lookup: CustomGameConfigs + patternsleuth status
+ the hand-curated fork registry. See dev-notes/spec.md M2 and
dev-notes/idea-brainstorm.md for why each of these three sources exists
and what it does/doesn't tell you.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

_FORKS_PATH = Path(__file__).parent / "forks.json"

# patternsleuth-games.md codenames are prefixed with an engine-version
# marker (e.g. "427_", "502_", "413X_") that CustomGameConfigs folder
# names and normal game names don't have -- confirmed against the real
# doc, not assumed.
_ENGINE_PREFIX_RE = re.compile(r"^\d{3}[A-Za-z]{0,2}_")

_SUMMARY_RE = re.compile(r"<summary>(.*?)</summary>", re.DOTALL)
_FOUND_HEADING = "## Games tested with all AOBs found by Patternsleuth"
_NOT_FOUND_HEADING = "## Games tested but with AOBs not found by Patternsleuth"


def _normalize(name: str) -> str:
    name = _ENGINE_PREFIX_RE.sub("", name)
    return re.sub(r"[^a-z0-9]", "", name.lower())


@dataclass
class CustomGameConfig:
    folder_name: str
    files: list[str]


def lookup_custom_game_config(
    cache_dir: Path, game_name: str
) -> CustomGameConfig | None:
    configs_dir = cache_dir / "assets" / "CustomGameConfigs"
    if not configs_dir.exists():
        return None

    target = _normalize(game_name)
    for entry in configs_dir.iterdir():
        if entry.is_dir() and _normalize(entry.name) == target:
            files = sorted(
                str(p.relative_to(entry)).replace("\\", "/")
                for p in entry.rglob("*")
                if p.is_file()
            )
            return CustomGameConfig(folder_name=entry.name, files=files)
    return None


def parse_patternsleuth_status(text: str) -> dict[str, str]:
    """Returns {normalized_game_name: "aob_found" | "aob_partial"}."""
    status: dict[str, str] = {}

    found_idx = text.find(_FOUND_HEADING)
    not_found_idx = text.find(_NOT_FOUND_HEADING)
    found_section = text[found_idx:not_found_idx] if found_idx != -1 else ""
    not_found_section = text[not_found_idx:] if not_found_idx != -1 else ""

    for line in found_section.splitlines()[1:]:  # skip the heading itself
        name = line.strip()
        if name:
            status[_normalize(name)] = "aob_found"

    for m in _SUMMARY_RE.finditer(not_found_section):
        name = m.group(1).strip()
        if name:
            status[_normalize(name)] = "aob_partial"

    return status


def lookup_patternsleuth_status(
    docs_root: Path, game_name: str
) -> str | None:
    path = docs_root / "patternsleuth-games.md"
    if not path.exists():
        return None
    status_map = parse_patternsleuth_status(
        path.read_text(encoding="utf-8", errors="replace")
    )
    return status_map.get(_normalize(game_name))


def _load_forks() -> list[dict]:
    return json.loads(_FORKS_PATH.read_text(encoding="utf-8")).get("games", [])


def lookup_known_fork(game_name: str) -> dict | None:
    target = _normalize(game_name)
    for entry in _load_forks():
        if _normalize(entry["game"]) == target:
            return entry
    return None


def list_known_forks() -> list[dict]:
    return _load_forks()
