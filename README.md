# UE4SS-mcp

An MCP (Model Context Protocol) server that gives a coding agent
version-aware, context-bounded access to [RE-UE4SS](https://github.com/UE4SS-RE/RE-UE4SS)
knowledge — versioned Lua/C++ API docs, log/crash parsing, and, when a
UE4SS-injected game is running, live introspection and control of it —
so the agent can help develop and debug Lua mods without a human
manually digging through docs, dumps, or a live object graph that can
have hundreds of thousands of entries.

This project operates an **already-installed** UE4SS. It does not help
you decide which UE4SS version or fork a given game needs, and it does
not install UE4SS into a game for you (see [Non-goals](#non-goals)).

## Setup

Requires [uv](https://docs.astral.sh/uv/) and Python (uv will install
a matching Python automatically).

```bash
cd server
uv sync
```

Run the server (stdio transport) directly, or point your MCP client's
config at it:

```bash
uv run ue4ss-mcp-server
```

To use the live tools (everything under [Live bridge tools](#live-bridge-tools-require-a-connected-game)),
call `install_bridge_mod` once against your UE4SS install directory —
see that tool's own description for exactly what path it expects.

## Tool catalog

### Docs (no game required)

| Tool | Purpose |
|---|---|
| `resolve_context` | Figure out which UE4SS version/game subsequent calls apply to, from explicit values or a `UE4SS.log`'s startup banner. |
| `search_api` / `get_symbol` | Search and drill into UE4SS's own versioned Lua/C++ API docs. |
| `get_upgrade_notes` | Look up what changed between two UE4SS versions. |

### Mod lifecycle (local files, no live game required)

| Tool | Purpose |
|---|---|
| `install_bridge_mod` | Install this project's own bridge mod into a UE4SS `Mods/` folder. Required once before any live tool works. |
| `enable_mod` / `disable_mod` | Flip a mod on/off in `mods.txt` (handles a real UE4SS mods.txt quirk — see [Gotchas](#gotchas--known-limitations)). |

### Live bridge tools (require a connected game)

| Tool | Purpose |
|---|---|
| `bridge_status` | Check whether a game is currently connected. |
| `find_object` / `describe_object` / `call_function` | Locate, inspect, and invoke methods on live `UObject`s. |
| `register_hook` / `unregister_hook` / `watch_new_object` / `poll_hook_events` | Observe UFunction calls and object construction without polling in a loop. |
| `reload_mod` | Hot-reload a mod that's already running (see [Gotchas](#gotchas--known-limitations) — this does *not* start a mod for the first time). |
| `dump_and_index` / `search_dump` | Trigger and query UE4SS's own bulk dumpers (actors/objects/SDK headers) without ever inlining the raw dump. |
| `exec_lua` | Run arbitrary Lua in the game as a last resort. |

### Log/crash parsing (no live game required)

| Tool | Purpose |
|---|---|
| `parse_log` | Extract version, mod load status, and errors/warnings from a `UE4SS.log`. |
| `parse_crash` | Parse either a native `CrashContext.runtime-xml` or a UE4SS-log Lua traceback into a structured callstack. |

Every list-shaped tool returns a paginated envelope
(`{items, returned, total_matched, truncated, cursor}`) rather than
dumping everything at once — the whole point of this project is
avoiding exactly that kind of info-bomb against a live object graph
that can be enormous.

## Gotchas / known limitations

These are all things discovered by actually running these tools against
a real game, not theoretical caveats — see `dev-notes/progress.md` (if
you have repo access) for the full story behind each one.

- **`enable_mod` + `reload_mod` cannot load a mod for the first time.**
  UE4SS only tracks mods it discovered at startup or a later *full
  rescan*. There is no Lua-callable equivalent of the UE4SS GUI
  Console's "Restart All Mods" button, which does do a full rescan —
  confirmed live that `reload_mod` on a never-loaded mod fails silently
  from this server's point of view (UE4SS logs
  `Could not find mod to reinstall: <name>`, but doesn't report that
  failure back to the calling Lua, so the tool can't distinguish it from
  success). **For a mod's first load, a human needs to click "Restart
  All Mods" in the UE4SS GUI Console, or relaunch the game.** After
  that, `reload_mod` works fine for iterating on it.
- **`find_object` against a broad base class (e.g. `"Actor"`, `"UObject"`)
  can stall the game.** Pagination bounds the *response*, not the
  underlying native scan — confirmed this froze a real game for 10+
  seconds. Prefer a specific class name.
- **`watch_new_object`'s `class_name` needs a full path**
  (`/Script/Engine.Actor`), unlike `find_object`'s `class_name` which
  takes a bare short name (`Actor`). Confirmed via a real engine error
  when the short form was tried.
- **`dump_and_index`'s `"objects"`/`"sdk"`/`"uht"` kinds can be slow**
  (76s+ observed for `"uht"` on a real game) and may visibly stall it,
  since the dump itself runs on the game thread. `"actors"` is the
  cheapest of the four.
- **This project does not help pick a UE4SS version or fork for a game,
  or install UE4SS.** It assumes UE4SS is already installed and working.
  (An earlier version had `lookup_game_compat`/`list_known_forks` for
  this; removed as out of scope.)

## Non-goals

- Not a GUI or a replacement for UE4SS's own Live Viewer/console.
- Not a process launcher or screen-capture tool — the host agent already
  has shell tools for that.
- Not a "which UE4SS version/fork should I use" or "install UE4SS"
  advisor.
- Not trying to out-perform UE4SS's own bulk dumpers — wraps them rather
  than reimplementing bulk traversal.
