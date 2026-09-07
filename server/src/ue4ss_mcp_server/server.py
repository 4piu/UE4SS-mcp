"""MCP server entry point and tool registration."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from ue4ss_mcp_server.cache import ensure_ref_cached
from ue4ss_mcp_server.compat import lookup_custom_game_config, lookup_known_fork
from ue4ss_mcp_server.compat import lookup_patternsleuth_status
from ue4ss_mcp_server.compat import list_known_forks as _list_known_forks
from ue4ss_mcp_server.context import resolve_context as _resolve_context
from ue4ss_mcp_server.docs_index import DocEntry, build_index
from ue4ss_mcp_server.envelope import paginate
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


@mcp.tool()
def lookup_game_compat(game_name: str, version: str) -> dict:
    """Check a game's known UE4SS compatibility signals: whether it has a
    CustomGameConfig (a lighter-weight override that works with upstream
    UE4SS directly), its Patternsleuth AOB-scan status, and any known
    dedicated fork (a heavier, separate UE4SS build for games upstream
    doesn't support at all).

    CustomGameConfigs are versioned and looked up at `version`. The
    Patternsleuth compatibility list doesn't exist in older tags, so this
    falls back to `main` when missing from `version`'s docs -- the
    response's `patternsleuth_source_ref` says which ref it actually came
    from.
    """
    cache_dir = ensure_ref_cached(version)
    config = lookup_custom_game_config(cache_dir, game_name)

    ps_source = version
    ps_status = lookup_patternsleuth_status(cache_dir / "docs", game_name)
    ps_file_missing = not (cache_dir / "docs" / "patternsleuth-games.md").exists()
    if ps_status is None and ps_file_missing:
        fallback_dir = ensure_ref_cached(DEFAULT_SOURCE_REF)
        ps_status = lookup_patternsleuth_status(fallback_dir / "docs", game_name)
        ps_source = DEFAULT_SOURCE_REF

    return {
        "game": game_name,
        "has_custom_config": config is not None,
        "custom_config": (
            {"folder_name": config.folder_name, "files": config.files}
            if config
            else None
        ),
        "patternsleuth_status": ps_status,
        "patternsleuth_source_ref": ps_source,
        "known_fork": lookup_known_fork(game_name),
    }


@mcp.tool()
def list_known_forks(limit: int | None = None, cursor: str | None = None) -> dict:
    """List the hand-curated registry of games with a known dedicated
    UE4SS fork. Sparse by design -- only individually-verified entries are
    added, not assumed from community reputation (see forks.json's note).
    """
    return paginate(_list_known_forks(), limit, cursor)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
