# Agent notes for UE4SS-mcp

## Using the ue4ss-mcp tools against a live game

- Resolve context first (`resolve_context`) — don't assume "latest".
- A mod's first load needs a human: `enable_mod`/`reload_mod` can't start
  a mod UE4SS has never loaded. Write the mod → `enable_mod` → ask a
  human to click "Restart All Mods" (or relaunch) → `reload_mod` works
  from then on.
- Don't `find_object` a broad class (`Actor`, `UObject`) — the native
  scan isn't bounded by pagination and can stall the game. Use a
  specific class or a `path` lookup.
- `watch_new_object`'s `class_name` is a full path
  (`/Script/Engine.Actor`), not `find_object`'s short form.
- `dump_and_index`'s `objects`/`sdk`/`uht` kinds can take a minute-plus
  and stall the game, or on some builds crash it outright with no
  output; `actors` is cheap.
- `call_function`/`exec_lua` mutate live state. UE4SS checks argument
  *count* but not *type* — a count-correct call with the wrong Lua value
  for a non-primitive parameter (FName/struct/object) can crash the
  whole game with no catchable error and no crash dump. Before calling
  a UFunction with any non-primitive parameter, get its handle via
  `find_object(path="/Script/Class:Function")` and `describe_object` it
  to see the real parameter types — only pass raw primitives otherwise.
- `describe_object`'s property-value reads carry the same uncatchable-
  crash risk as `call_function`, but with no argument of yours to blame:
  on a game whose engine build doesn't match UE4SS's assumptions (e.g. a
  modified/forked engine), just reading a property's value can be an
  out-of-bounds native access. `exec_lua` code that calls an
  undocumented/guessed method on any reflected wrapper object (not just
  a UFunction you invoke on purpose) carries the same risk — `pcall`
  doesn't help with any of this.
- To confirm your own mod actually ran, `search_log` for its `print()`
  output — `parse_log`'s structured fields won't show arbitrary text.
- Call `wait_running_bridge` before any live tool — connecting is an
  explicit, on-demand action now, not an always-on background retry.
  `list_running_bridge` shows every known game and whether it's live.
  Liveness is otherwise checked lazily: a connected bridge may show
  stale `connected: true` until the next live call actually fails.
- More than one agent can connect to the same running game at once (the
  bridge mod itself accepts several simultaneous connections) — only
  pass `bridge_id` to a live tool when more than one game is connected
  at the same time; omit it otherwise.
- Reinstalling the bridge mod over an already-running game can fail:
  its companion native module (`ue4ssmcp_pipe.dll`) stays locked in
  memory while loaded. Close the game before calling `install_bridge_mod`
  again, then relaunch.
- This server won't tell you which UE4SS version/fork a game needs, or
  install UE4SS. Out of scope by design.

## Working in this repo

- `dev-notes/` (gitignored) is internal scratch — `spec.md` is the spec,
  `progress.md` is the history of what was actually verified.
- Verify against a real game/log/docs before trusting a design; most
  bugs and gotchas here were found that way, not by reading UE4SS docs
  and assuming they were complete.
- New tool idea? Ask: does it operate an *installed* UE4SS, or solve a
  setup problem (version/fork/install)? Only the former is in scope.
