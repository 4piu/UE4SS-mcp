"""MCP server entry point and tool registration."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from ue4ss_mcp_server.cache import ensure_ref_cached
from ue4ss_mcp_server.context import resolve_context as _resolve_context
from ue4ss_mcp_server.docs_index import DocEntry, build_index
from ue4ss_mcp_server.search import find_symbol, search_symbols
from ue4ss_mcp_server.upgrade_notes import DEFAULT_SOURCE_REF
from ue4ss_mcp_server.upgrade_notes import get_upgrade_notes as _get_upgrade_notes

mcp = MCPServer("ue4ss-mcp")

# In-memory index cache keyed by (version, api): building it is cheap but
# not free, and a session will typically call search_api/get_symbol
# repeatedly against the same ref+api.
_index_cache: dict[tuple[str, str], list[DocEntry]] = {}


def _get_index(version: str, api: str) -> list[DocEntry]:
    key = (version, api)
    if key not in _index_cache:
        docs_root = ensure_ref_cached(version) / "docs"
        _index_cache[key] = build_index(docs_root, api)
    return _index_cache[key]


@mcp.tool()
def resolve_context(
    game: str | None = None,
    version: str | None = None,
    log_path: str | None = None,
) -> dict:
    """Resolve which UE4SS version + game subsequent tool calls apply to.

    Pass `game`/`version` explicitly, or `log_path` to a UE4SS.log to
    detect them from its startup banner. Explicit values win over
    anything detected from the log.
    """
    ctx = _resolve_context(game=game, version=version, log_path=log_path)
    return {
        "game": ctx.game,
        "version": ctx.version,
        "source": ctx.source,
        "build_sha": ctx.build_sha,
        "channel": ctx.channel,
    }


@mcp.tool()
def search_api(
    query: str,
    api: str,
    version: str,
    limit: int | None = None,
    cursor: str | None = None,
) -> dict:
    """Search UE4SS's Lua or C++ API docs at a specific version/tag.

    `api` must be "lua" or "cpp". Returns compact paginated
    {symbol, kind, parent, summary} entries — call get_symbol for full
    detail on a specific result. `version` is a required git ref (e.g.
    "v3.0.1") — always pass the version relevant to the game you're
    debugging rather than assuming latest.
    """
    entries = _get_index(version, api)
    return search_symbols(entries, query, limit, cursor)


@mcp.tool()
def get_symbol(
    symbol: str, api: str, version: str, parent: str | None = None
) -> dict:
    """Get the full doc entry for one Lua/C++ API symbol at a specific version.

    Some symbol names (e.g. a method like "GetFullName()") are defined on
    more than one class. If the lookup is ambiguous, this returns the list
    of candidates instead of guessing — pass `parent` (the class/doc name
    from a candidate) to disambiguate and try again.
    """
    entries = _get_index(version, api)
    matches = find_symbol(entries, symbol, parent=parent)

    if not matches:
        return {"found": False, "symbol": symbol, "api": api, "version": version}

    if len(matches) > 1:
        return {
            "found": False,
            "ambiguous": True,
            "symbol": symbol,
            "candidates": [
                {"parent": e.parent, "kind": e.kind, "file": e.file} for e in matches
            ],
        }

    entry = matches[0]
    return {
        "found": True,
        "symbol": entry.symbol,
        "kind": entry.kind,
        "parent": entry.parent,
        "file": entry.file,
        "content": entry.content,
    }


@mcp.tool()
def get_upgrade_notes(from_version: str, to_version: str) -> dict:
    """Get upgrade-guide.md section(s) covering the transition between two
    UE4SS major versions (e.g. "3.0.1" -> "4.0.0").

    Always sourced from `main` regardless of the versions asked about:
    upgrade-guide.md is a cumulative, forward-only document that doesn't
    exist at all in older tagged releases, so an old tag can never be the
    right source for it.
    """
    docs_root = ensure_ref_cached(DEFAULT_SOURCE_REF) / "docs"
    return _get_upgrade_notes(docs_root, from_version, to_version, DEFAULT_SOURCE_REF)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
