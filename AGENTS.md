# Agent notes for UE4SS-mcp

You're either working *in* this repo, or *using* the MCP server it
builds. Either way, read this before calling any tool — every item here
was found by actually running the tool against a real UE4SS-injected
game, not inferred from documentation.

## If you're using the ue4ss-mcp tools against a live game

- **Always resolve context first** (`resolve_context`). Don't assume
  "latest" — a game's UE4SS version determines which docs/behavior
  actually apply, and getting this wrong is the exact failure mode this
  whole project exists to avoid.
- **A mod's first load needs a human.** `enable_mod` + `reload_mod`
  cannot start a mod that UE4SS has never loaded before — there's no
  Lua-callable equivalent of the GUI Console's "Restart All Mods"
  button, and `reload_mod` on a never-loaded mod silently does nothing
  useful (UE4SS logs `Could not find mod to reinstall: <name>` but
  doesn't report that back to Lua, so the tool response looks like
  success either way). Workflow: write the mod's files → `enable_mod` →
  **ask the human to click "Restart All Mods" (or relaunch) once** →
  `reload_mod` works from then on for iterating on it.
- **Never call `find_object` with a broad base class** (`"Actor"`,
  `"UObject"`, `"Pawn"`) unless you mean it. The native scan cost isn't
  bounded by `limit`/pagination — this has actually frozen a running
  game for 10+ seconds. Use the most specific class name you can, or a
  `path` lookup.
- **`watch_new_object`'s `class_name` is a full path**
  (`/Script/Engine.Actor`), not the short form `find_object` accepts.
  Passing the short form fails with a real engine error.
- **`dump_and_index`'s `"objects"`/`"sdk"`/`"uht"` kinds can take a
  minute or more** and may visibly stall the game (the dump runs on the
  game thread). Prefer `"actors"` unless you specifically need the
  others. Don't retry on a slow response — it's still running, not
  hung, up to the tool's own generous timeout.
- **`call_function`/`exec_lua` mutate live state.** UE4SS validates a
  UFunction call's argument *count* but not necessarily argument type —
  a wrong-type argument can misbehave unpredictably rather than erroring
  cleanly. Don't guess at argument values; confirm via `describe_object`
  or docs first when it matters.
- **`bridge_status` can lie slightly.** Liveness is checked lazily — a
  game that vanished without any live call in flight since won't be
  detected as disconnected until the next tool call actually fails.
- **This server won't tell you which UE4SS version/fork a game needs,
  or install UE4SS for you.** That's out of scope by design — it
  operates an install that's already there. If a game doesn't have
  UE4SS working yet, that's a task for you and the user directly, not
  this MCP.

## If you're working in this repo itself

- `dev-notes/` is internal scratch (gitignored) — `spec.md` is the
  living "what to build" doc, `progress.md` is the running history of
  what was actually verified and how. Read both before assuming
  something isn't built or a design decision wasn't already made and
  reasoned through.
- This project's rule for new work: **verify against real data before
  trusting a design.** Nearly every real bug and every gotcha above was
  found by actually running something against a live game or a real
  captured log/crash sample, not by reading UE4SS's docs and assuming
  they were complete. If you're adding a tool that touches UE4SS's own
  API, check the actual docs bundled with a real UE4SS install (or the
  versioned copy this server's cache layer fetches) before writing Lua
  against it, and treat "looks right" as provisional until it's been
  run for real.
- Before proposing a new tool, ask: does this help operate an
  *already-installed* UE4SS, or does it solve a setup-time problem
  (which version/fork, how to install)? Only the former is in scope —
  see the README's Non-goals section and `dev-notes/spec.md` §2.
