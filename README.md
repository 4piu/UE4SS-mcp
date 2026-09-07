# UE4SS-mcp

An MCP server giving a coding agent version-aware access to
[RE-UE4SS](https://github.com/UE4SS-RE/RE-UE4SS) docs, plus live
introspection and control of a UE4SS-injected game, to help develop and
debug Lua mods without hand-digging through docs or a live object graph
with hundreds of thousands of entries.

Operates an **already-installed** UE4SS. Does not help choose a
version/fork or install UE4SS — see [Non-goals](#non-goals).

## Setup

```bash
cd server
uv sync
uv run ue4ss-mcp-server   # stdio transport
```

Call `install_bridge_mod` once against your UE4SS install directory to
enable the live tools below.

## Tools

**Docs** (no game needed): `resolve_context`, `search_api`, `get_symbol`, `get_upgrade_notes`

**Mod lifecycle** (local files): `install_bridge_mod`, `enable_mod`, `disable_mod`

**Live** (need a connected game): `bridge_status`, `find_object`, `describe_object`, `call_function`, `register_hook`, `unregister_hook`, `watch_new_object`, `poll_hook_events`, `reload_mod`, `dump_and_index`, `search_dump`, `exec_lua`

**Log/crash** (no game needed): `parse_log`, `parse_crash`

Every list-shaped tool is paginated (`{items, returned, total_matched, truncated, cursor}`) — nothing dumps a whole object graph or log at once. Full usage details are in each tool's own description.

## Known limitations

- **A mod's first load needs a human.** `enable_mod` + `reload_mod` can't start a mod UE4SS has never loaded — click "Restart All Mods" in the UE4SS console (or relaunch) once, then `reload_mod` works.
- **`find_object` on a broad class** (`Actor`, `UObject`) **can stall the game** — pagination bounds the response, not the underlying scan. Use a specific class.
- **`watch_new_object` takes a full class path** (`/Script/Engine.Actor`), unlike `find_object`'s short name.
- **`dump_and_index`'s `objects`/`sdk`/`uht` kinds can be slow** and stall the game briefly; `actors` is cheap.
- **No version/fork advice.** This project assumes UE4SS already works on your game.

## Non-goals

Not a GUI, process launcher, or screen-capture tool. Not a UE4SS version/fork advisor or installer. Not a replacement for UE4SS's own bulk dumpers — wraps them.
