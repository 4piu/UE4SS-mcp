"""MCP server entry point and tool registration."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from ue4ss_mcp_server.bridge import BridgeServer, BridgeState
from ue4ss_mcp_server.bridge_install import get_or_create_token
from ue4ss_mcp_server.bridge_install import install_bridge_mod as _install_bridge_mod
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

_bridge_state = BridgeState()
_bridge_server: BridgeServer | None = None

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


@mcp.tool()
def install_bridge_mod(game_install_path: str) -> dict:
    """Install the MCP bridge mod into a game's UE4SS Mods/ folder.

    `game_install_path` is the UE4SS install directory (containing
    UE4SS.dll and Mods/), not the game's root install folder. Explicit/
    opt-in only -- the bridge is never installed automatically. Takes
    effect on the next game launch or mod reload.
    """
    return _install_bridge_mod(game_install_path)


@mcp.tool()
def bridge_status() -> dict:
    """Check whether a game is currently connected via the bridge mod,
    and if so which UE4SS/engine version it reported on handshake.
    Always safe to call -- reports not-connected rather than erroring.
    """
    return _bridge_state.snapshot()


def _bridge_request(op: str, params: dict) -> dict:
    assert _bridge_server is not None
    response = _bridge_server.send_request(op, params)
    if not response.get("ok"):
        return {"error": response.get("error", "unknown bridge error")}
    return response.get("result", {})


@mcp.tool()
def find_object(
    class_name: str | None = None,
    name_pattern: str | None = None,
    path: str | None = None,
    limit: int | None = None,
    cursor: str | None = None,
) -> dict:
    """Find live UObject instances in the connected game, without dumping
    the whole object graph (which can have hundreds of thousands of
    entries). Pass exactly one of `path` (an exact object path, resolved
    via StaticFindObject -- cheap, single result) or `class_name` (a short
    class name, resolved via FindAllOf and optionally narrowed with a
    case-insensitive substring `name_pattern`). Requires the bridge to be
    connected (see bridge_status); returns `{"error": "not connected"}`
    otherwise.

    Returns a paginated envelope of `{handle, class, name}` -- pass a
    result's `handle` to describe_object/call_function to drill in.

    IMPORTANT: prefer a specific class over a broad base class like
    "Actor", "Pawn", or "UObject". `limit`/`cursor` only bound the
    *response size* -- the underlying FindAllOf scan still has to
    enumerate every live instance of the class first, and that scan runs
    on the game's own thread. In a large open-world game, scanning a
    near-universal base class can take long enough to freeze the game
    itself for the duration (confirmed: `FindAllOf("Actor")` froze a
    real running game for 10+ seconds). Use a concrete class name (the
    Blueprint/native class you actually care about) or a `name_pattern`-
    friendly `path` lookup instead whenever possible.
    """
    params = {"class": class_name, "name_pattern": name_pattern, "path": path, "limit": limit, "cursor": cursor}
    return _bridge_request("find_object", params)


@mcp.tool()
def describe_object(handle: str, limit: int | None = None, cursor: str | None = None) -> dict:
    """Get the property map for one live object found via find_object.

    Properties are paginated (like every list-shaped tool here) since a
    single class can have dozens to hundreds of reflected properties.
    Nested UObject-valued properties come back as new handles rather than
    being inlined, so drill into them with another describe_object call
    instead of getting a recursive dump. A `handle` that no longer refers
    to a live object (destroyed actor, unloaded level, ...) returns
    `{"error": "HANDLE_EXPIRED"}` -- re-resolve it via find_object.
    """
    return _bridge_request("describe_object", {"handle": handle, "limit": limit, "cursor": cursor})


@mcp.tool()
def call_function(handle: str, function_name: str, args: list | None = None) -> dict:
    """Call a UFunction (or any callable member) on a live object found via
    find_object, e.g. to invoke a mod's own testable Lua-callable function
    or a native UFunction. This mutates live game state -- use deliberately,
    not for read-only inspection (that's what describe_object is for).

    `args` is a plain JSON array in call order (no self/context -- that's
    supplied automatically from `handle`). Returns `{"result": {...}}` on
    success (nested UObject return values come back as a handle, same as
    describe_object) or `{"error": "..."}` on failure, including
    `HANDLE_EXPIRED` for a stale handle.
    """
    return _bridge_request("call_function", {"handle": handle, "function_name": function_name, "args": args or []})


@mcp.tool()
def register_hook(ufunction_name: str, when: str = "both") -> dict:
    """Register a callback on a UFunction so its calls can be observed
    without polling describe_object in a loop. `ufunction_name` is a full
    UFunction path (e.g. "/Script/Engine.PlayerController:ClientRestart");
    for RegisterHook to succeed the function must already exist in memory
    (usually true for anything from a loaded class). `when` is "pre",
    "post", or "both" -- for a Blueprint function (a path that doesn't
    start with "/Script/") only "post" is meaningful, since UE4SS itself
    doesn't support a pre-hook there.

    Fires are NOT pushed to you -- they're buffered on the bridge side
    and drained by calling poll_hook_events with the returned `hook_id`,
    consistent with every other tool here never sending unsolicited data.
    Call unregister_hook when done to stop buffering and free the hook.
    """
    return _bridge_request("register_hook", {"ufunction_name": ufunction_name, "when": when})


@mcp.tool()
def unregister_hook(hook_id: str) -> dict:
    """Stop a hook registered via register_hook or a watch registered via
    watch_new_object -- same tool for both, since they're both just an
    opaque id for something buffering events on the bridge side.
    """
    return _bridge_request("unregister_hook", {"hook_id": hook_id})


@mcp.tool()
def watch_new_object(class_name: str) -> dict:
    """Get notified when a new instance of `class_name` is constructed
    (inheritance-aware -- watching a base class also catches derived
    classes), without polling find_object in a loop. `class_name` doesn't
    need to exist yet when you call this.

    Fires are buffered and pulled via poll_hook_events with the returned
    `watch_id`, same pull model as register_hook. Call unregister_hook
    when done -- UE4SS's underlying NotifyOnNewObject has no direct
    cancel, so this takes effect from the class's next construction
    onward rather than immediately.
    """
    return _bridge_request("watch_new_object", {"class_name": class_name})


@mcp.tool()
def poll_hook_events(hook_id: str, limit: int | None = None, cursor: str | None = None) -> dict:
    """Drain buffered fire events for a hook (register_hook) or watch
    (watch_new_object). Unlike every other paginated tool here, `cursor`
    is never null in the response -- it's always "resume from here next
    time" for this live stream, not "no more results". Pass it back on
    your next poll to only see events fired since the last one. A
    `dropped_since_last_poll > 0` means fires happened faster than you
    polled and the oldest ones were evicted (buffer caps at 200 events
    per hook/watch) -- poll more often if that matters for your use case.
    """
    return _bridge_request("poll_hook_events", {"hook_id": hook_id, "limit": limit, "cursor": cursor})


@mcp.tool()
def reload_mod(mod_name: str) -> dict:
    """Hot-reload a Lua mod by name (e.g. after editing its script) via
    RestartMod, or this bridge's own mod via RestartCurrentMod if
    `mod_name` matches it. Queued for the next update cycle, not
    immediate. Reloading the bridge mod itself destroys its Lua state
    (and with it every handle/hook/watch from the current session) --
    this response arrives fine beforehand, but expect bridge_status to
    briefly show disconnected while it restarts, then reconnect on its
    own like any other relaunch.
    """
    return _bridge_request("reload_mod", {"mod_name": mod_name})


def main() -> None:
    global _bridge_server
    _bridge_server = BridgeServer(get_or_create_token(), _bridge_state)
    _bridge_server.start()
    mcp.run()


if __name__ == "__main__":
    main()
