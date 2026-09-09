"""Context resolution: which UE4SS version + game are we talking about.

Every other tool depends on this instead of defaulting to "latest" — see
dev-notes/idea-brainstorm.md for why that default is the thing this whole
project exists to avoid. See dev-notes/spec.md §6 for the contract.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# From a real captured banner line (Hogwarts Legacy, v3.0.1 dev build):
#   UE4SS - v3.0.1 Beta #0 - Git SHA #d935b5b
_VERSION_RE = re.compile(
    r"UE4SS - v(?P<version>[\d.]+)"
    r"(?: (?P<channel>\S+) #(?P<build>\d+))?"
    r" - Git SHA #(?P<sha>[0-9a-fA-F]+)"
)

# From a real captured line:
#   game executable: D:\...\HogwartsLegacy.exe (457901056 bytes)
_GAME_EXE_RE = re.compile(
    r"game executable: (?P<path>.+\.exe) \((?P<size>\d+) bytes\)"
)

# Version banner and game-executable line both appear in UE4SS's startup
# preamble; no need to scan a whole (potentially large) log for them.
_BANNER_SCAN_LINES = 30


@dataclass
class Context:
    game: str | None
    version: str | None
    source: str  # "explicit" | "log"
    build_sha: str | None = None
    channel: str | None = None


def parse_log_banner(text: str) -> dict[str, str]:
    """Extract version/game info from a UE4SS.log's startup banner."""
    result: dict[str, str] = {}
    lines = text.splitlines()[:_BANNER_SCAN_LINES]
    head = "\n".join(lines)

    version_match = _VERSION_RE.search(head)
    if version_match:
        result["version"] = version_match.group("version")
        if version_match.group("sha"):
            result["build_sha"] = version_match.group("sha")
        if version_match.group("channel"):
            result["channel"] = version_match.group("channel")

    exe_match = _GAME_EXE_RE.search(head)
    if exe_match:
        exe_path = exe_match.group("path")
        result["game"] = Path(exe_path).stem

    return result


def resolve_context(
    game: str | None = None,
    version: str | None = None,
    log_path: str | None = None,
    log_text: str | None = None,
) -> Context:
    """Resolve a version/game context.

    Explicit `game`/`version` always win over anything parsed from a log.
    Live bridge handshake context (once the bridge exists) will take
    precedence over both — not implemented yet, see spec.md M3+.
    """
    if log_text is None and log_path is not None:
        log_text = Path(log_path).read_text(encoding="utf-8", errors="replace")

    if log_text is not None:
        parsed = parse_log_banner(log_text)
        return Context(
            game=game or parsed.get("game"),
            version=version or parsed.get("version"),
            source="log",
            build_sha=parsed.get("build_sha"),
            channel=parsed.get("channel"),
        )

    return Context(game=game, version=version, source="explicit")
