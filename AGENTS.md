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
  and stall the game; `actors` is cheap.
- `call_function`/`exec_lua` mutate live state; UE4SS checks argument
  *count*, not necessarily type.
- `bridge_status` is checked lazily — may show stale "connected" until
  the next live call fails.
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
