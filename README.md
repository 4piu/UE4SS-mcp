# UE4SS-mcp

An MCP server giving a coding agent version-aware access to
[RE-UE4SS](https://github.com/UE4SS-RE/RE-UE4SS) docs, plus live
introspection and control of a UE4SS-injected game, to help develop and
debug Lua mods without hand-digging through docs or a live object graph
with hundreds of thousands of entries.

## Setup

Add this to your MCP client's config (e.g. `mcp.json`) to run it
straight from GitHub via `uvx` — no clone needed:

```json
{
  "mcpServers": {
    "ue4ss-mcp": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/4piu/UE4SS-mcp#subdirectory=server",
        "ue4ss-mcp-server"
      ]
    }
  }
}
```

Or from a local clone:

```bash
cd server
uv sync
uv run ue4ss-mcp-server   # stdio transport
```

Call `install_bridge_mod` once against your UE4SS install directory to
enable the live tools below.

The bridge mod reconnects on a backoff (2s, doubling to a 30s ceiling)
whenever this server isn't reachable, so a fresh game can take up to
~30s to show connected — keep one server process alive across calls
rather than starting it fresh for a single one-shot call.

## Tools

Every list-shaped tool is paginated (`{items, returned, total_matched, truncated, cursor}`) — nothing dumps a whole object graph or log at once. Full usage details are in each tool's own description.

| Tool | Needs a game? | Description |
|---|---|---|
| `resolve_context` | no | Detect UE4SS version/game from a log or explicit values |
| `search_api` / `get_symbol` | no | Search and drill into UE4SS's versioned Lua/C++ API docs |
| `get_upgrade_notes` | no | What changed between two UE4SS versions |
| `install_bridge_mod` | no | Install this project's bridge mod into a UE4SS `Mods/` folder |
| `enable_mod` / `disable_mod` | no | Flip a mod on/off in `mods.txt` |
| `bridge_status` | yes | Whether a game is currently connected |
| `find_object` | yes | Locate a live `UObject` (or `UFunction`) by class or path |
| `describe_object` | yes | Inspect an object's properties, or a `UFunction`'s parameters |
| `call_function` | yes | Invoke a method on a live object |
| `register_hook` / `unregister_hook` | yes | Observe a UFunction's calls without polling |
| `watch_new_object` | yes | Observe object construction without polling |
| `poll_hook_events` | yes | Drain buffered events from a hook or watch |
| `reload_mod` | yes | Hot-reload a mod that's already running |
| `dump_and_index` / `search_dump` | yes | Trigger and query UE4SS's bulk dumpers (actors/objects/SDK headers) |
| `exec_lua` | yes | Run arbitrary Lua in the game, as a last resort |
| `parse_log` | no | Version, mod load status, errors/warnings from a `UE4SS.log` |
| `search_log` | no | Free-text search over a log |
| `parse_crash` | no | Parse a native crash report or a Lua traceback into a structured callstack |

## Known limitations

- **A mod's first load needs a human.** `enable_mod` + `reload_mod` can't start a mod UE4SS has never loaded — click "Restart All Mods" in the UE4SS console (or relaunch) once, then `reload_mod` works.
- **A broad live query can stall the game.** `find_object` on `Actor`/`UObject`, or `dump_and_index`'s `objects`/`sdk`/`uht` kinds, run an unbounded native scan on the game thread — pagination bounds the response, not the scan itself.
- **`call_function`/`exec_lua` can crash the game with no diagnostics.** UE4SS checks argument *count* but not *type*.
