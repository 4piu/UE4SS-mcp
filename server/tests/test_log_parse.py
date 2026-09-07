from pathlib import Path

from ue4ss_mcp_server.log_parse import parse_log

FIXTURE = Path(__file__).parent / "fixtures" / "hogwarts_legacy_ue4ss.log"


def test_parse_log_real_clean_log():
    text = FIXTURE.read_text(encoding="utf-8")
    result = parse_log(text)

    assert result["version"] == "3.0.1"
    assert result["build_sha"] == "d935b5b"
    assert result["channel"] == "Beta"
    assert result["game"] == "HogwartsLegacy"

    mod_names = {m["name"] for m in result["mods"]}
    assert "CheatManagerEnablerMod" in mod_names
    assert "Keybinds" in mod_names
    disabled = {m["name"] for m in result["mods"] if not m["enabled"]}
    assert "ActorDumperMod" in disabled
    assert "SplitScreenMod" in disabled
    enabled_lua = [m for m in result["mods"] if m["name"] == "CheatManagerEnablerMod"][0]
    assert enabled_lua["kind"] == "lua"

    assert result["ps_scan_attempts"] == 1
    assert "GUObjectArray" in result["ps_signatures_found"]
    assert "FName::ToString" in result["ps_signatures_found"]

    assert result["warnings_count"] == 0
    assert result["errors"]["returned"] == 0
    assert result["errors"]["total_matched"] == 0


def test_parse_log_extracts_real_error_line():
    text = (
        "[2026-09-07 14:26:13.4538268] Error: [Lua::call_function] lua_pcall returned LUA_ERRRUN => boom\n"
        "[2026-09-07 14:26:14.0000000] Fatal Error: something else went wrong\n"
    )
    result = parse_log(text)

    assert result["errors"]["returned"] == 2
    assert result["errors"]["items"][0]["severity"] == "error"
    assert "lua_pcall returned LUA_ERRRUN" in result["errors"]["items"][0]["text"]
    assert result["errors"]["items"][1]["severity"] == "fatal"


def test_parse_log_warning_count():
    text = "[2026-09-07 14:31:02.8203254] [Lua] [BPModLoaderMod] [Warning] Invalid UWorld object was passed to LoadMods.\n"
    result = parse_log(text)
    assert result["warnings_count"] == 1


def test_parse_log_errors_are_paginated():
    text = "\n".join(f"[2026-09-07 00:00:00] Error: err {i}" for i in range(5))
    result = parse_log(text, limit=2)
    assert result["errors"]["returned"] == 2
    assert result["errors"]["total_matched"] == 5
    assert result["errors"]["truncated"] is True
