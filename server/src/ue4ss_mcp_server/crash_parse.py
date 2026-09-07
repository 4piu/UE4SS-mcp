"""Parses UE4SS-adjacent crash artifacts for parse_crash.

Three real, confirmed-distinct formats -- all captured firsthand against
a real running game (not assumed from documentation):

- A native Unreal crash report (`CrashContext.runtime-xml`), generated
  by the engine's own crash reporter for an actual unhandled exception
  that killed the process (M7's deliberate stack-overflow-via-recursive-
  RegisterHook test). Structured XML with per-thread callstacks as
  space-separated "Module 0xBASE + OFFSET" frame triples.
- Two distinct Lua-level error+traceback shapes UE4SS itself writes
  directly into UE4SS.log, both far more common than a native crash
  since most Lua mod bugs never produce one at all:
  - `Error: [Lua::<source>] lua_pcall returned <TYPE> => <message>` --
    from UE4SS's own C++ call_function/exec_lua wrapper catching a
    runtime error (M7's "C stack overflow" sample).
  - `Error executing script: <path>:<line>: <message>` followed by
    `Failed to execute main script: <path>` -- from a mod's own
    top-level script (or an unguarded hook callback) throwing an
    uncaught error. Found by a fresh-eyes agent test calling a
    UFunction with the wrong argument count; the parser originally
    only recognized the first shape and failed on this real, valid
    example of exactly what its own docstring claimed to cover.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

_FRAME_RE = re.compile(r"(\S+)\s+(0x[0-9a-fA-F]+)\s*\+\s*([0-9a-fA-F]+)")
_MAX_FRAMES = 100

# Confirmed firsthand: a real CrashContext.runtime-xml's CommandLine
# field contains the actual launch command line, auth tokens and all
# (Epic's -AUTH_PASSWORD=... session code, account id, username). Never
# surfaced even if present in the input.
_REDACTED_FIELDS = {"CommandLine", "UserActivityHint", "UserDescription"}

# Tried in order; the first to match wins. Both end where the shared
# "stack traceback:" block begins, extracted separately below since
# UE4SS sometimes repeats that block verbatim right after itself (a real,
# reproducible quirk of the second format) -- only the first copy matters.
_LUA_ERROR_FORMATS = (
    re.compile(r"Error: \[Lua::(?P<source>[\w:]+)\] lua_pcall returned (?P<lua_error_type>\w+) => (?P<error_message>.+)"),
    re.compile(r"Error executing script: (?P<script_path>.+?):(?P<line>\d+): (?P<error_message>.+)"),
)
_TRACEBACK_BLOCK_RE = re.compile(r"^stack traceback:\n((?:\t.+\n?)+)", re.MULTILINE)


def _parse_frames(callstack_text: str, max_frames: int = _MAX_FRAMES) -> list[dict]:
    frames = []
    for m in _FRAME_RE.finditer(callstack_text):
        frames.append({"module": m.group(1), "base": m.group(2), "offset": m.group(3)})
        if len(frames) >= max_frames:
            break
    return frames


def parse_native_crash_xml(text: str) -> dict:
    root = ET.fromstring(text)
    rp = root.find("RuntimeProperties")
    if rp is None:
        raise ValueError("not a recognized CrashContext.runtime-xml (missing RuntimeProperties)")

    def field(name: str) -> str | None:
        if name in _REDACTED_FIELDS:
            return None
        el = rp.find(name)
        return el.text if el is not None else None

    exception_info = {
        "error_message": field("ErrorMessage"),
        "crash_type": field("CrashType"),
        "engine_version": field("EngineVersion"),
        "game_name": field("GameName"),
        "build_configuration": field("BuildConfiguration"),
    }

    crashed_thread = None
    callstack_text = ""
    threads_el = rp.find("Threads")
    if threads_el is not None:
        for t in threads_el.findall("Thread"):
            is_crashed_el = t.find("IsCrashed")
            if is_crashed_el is not None and is_crashed_el.text == "true":
                thread_id_el = t.find("ThreadID")
                thread_name_el = t.find("ThreadName")
                crashed_thread = {
                    "thread_id": thread_id_el.text if thread_id_el is not None else None,
                    "thread_name": thread_name_el.text if thread_name_el is not None else None,
                }
                callstack_el = t.find("CallStack")
                callstack_text = (callstack_el.text or "") if callstack_el is not None else ""
                break

    return {
        "exception_info": exception_info,
        "thread": crashed_thread,
        "callstack": _parse_frames(callstack_text) if crashed_thread else [],
    }


def parse_lua_traceback(text: str) -> dict | None:
    """Returns None (not an error) if no Lua traceback block is found --
    the caller decides what an absent traceback means for its input.
    """
    match = None
    for pattern in _LUA_ERROR_FORMATS:
        match = pattern.search(text)
        if match:
            break
    if match is None:
        return None

    tb_match = _TRACEBACK_BLOCK_RE.search(text, match.end())
    if tb_match is None:
        return None

    frames = [line.strip() for line in tb_match.group(1).splitlines() if line.strip()]
    exception_info = {k: v for k, v in match.groupdict().items() if v is not None}
    if "line" in exception_info:
        exception_info["line"] = int(exception_info["line"])

    return {
        "exception_info": exception_info,
        "thread": None,  # Lua has no notion of which native OS thread it's running on
        "callstack": frames[:_MAX_FRAMES],
    }


def parse_crash(text: str) -> dict:
    """Auto-detects and parses either crash artifact format.

    NOTE: the two formats' `callstack` shapes are genuinely different
    (structured {module, base, offset} frames for the native XML vs.
    plain source-line strings for a Lua traceback) -- not unified into
    one fake-common shape, since that would lose real information either
    way. See each format's own field for what's actually mapped.
    """
    stripped = text.lstrip()
    if stripped.startswith("<?xml") or stripped.startswith("<FGenericCrashContext"):
        return parse_native_crash_xml(text)

    lua_result = parse_lua_traceback(text)
    if lua_result is not None:
        return lua_result

    raise ValueError("not a recognized crash format (neither CrashContext.runtime-xml nor a UE4SS Lua traceback)")
