from pathlib import Path

import pytest

from ue4ss_mcp_server.crash_parse import parse_crash

FIXTURES = Path(__file__).parent / "fixtures" / "crash_samples"


def test_parse_native_crash_xml_real_sample():
    text = (FIXTURES / "crash_context_sample.xml").read_text(encoding="utf-8")
    result = parse_crash(text)

    assert result["exception_info"]["error_message"] == "Unhandled exception"
    assert result["exception_info"]["crash_type"] == "Crash"
    assert result["exception_info"]["engine_version"].startswith("4.27")
    # CommandLine must never surface, redacted or otherwise -- confirmed a
    # real one contains actual auth tokens.
    assert "command_line" not in result["exception_info"]
    assert "CommandLine" not in str(result)

    assert result["thread"]["thread_name"] == "GameThread"
    assert result["thread"]["thread_id"] == "30952"

    assert len(result["callstack"]) > 0
    assert len(result["callstack"]) <= 100
    first_frame = result["callstack"][0]
    assert set(first_frame.keys()) == {"module", "base", "offset"}
    # The real recursive ClientRestart crash shows UE4SS frames repeating.
    modules = {f["module"] for f in result["callstack"]}
    assert "UE4SS" in modules


def test_parse_lua_traceback_real_sample():
    text = (FIXTURES / "ue4ss_log_crash_tail.log").read_text(encoding="utf-8")
    result = parse_crash(text)

    assert result["thread"] is None
    assert result["exception_info"]["lua_error_type"] == "LUA_ERRRUN"
    assert "C stack overflow" in result["exception_info"]["error_message"]
    assert result["exception_info"]["source"] == "call_function"  # "Lua::" is a fixed prefix, not part of the captured source

    assert len(result["callstack"]) > 0
    assert any("ClientRestart" in frame for frame in result["callstack"])
    assert any("skipping 376 levels" in frame for frame in result["callstack"])


def test_parse_lua_script_error_real_sample():
    # A second, distinct real format the original parser didn't
    # recognize at all -- found by a fresh-eyes agent test calling a
    # UFunction with the wrong argument count, which raised ValueError
    # against this exact, valid input before the fix.
    text = (FIXTURES / "ue4ss_log_script_error_tail.log").read_text(encoding="utf-8")
    result = parse_crash(text)

    assert result["thread"] is None
    assert result["exception_info"]["script_path"].endswith("MCPTestMod\\Scripts\\main.lua")
    assert result["exception_info"]["line"] == 14
    assert "UFunction expected 3 parameters, received 1" in result["exception_info"]["error_message"]
    assert "lua_error_type" not in result["exception_info"]
    assert "source" not in result["exception_info"]

    # UE4SS repeats the "stack traceback:" block verbatim for this
    # format -- only the first copy should be captured, not both.
    assert result["callstack"] == [
        "[C]: in method 'ClientMessage'",
        "...ix\\Binaries\\Win64\\ue4ss\\Mods\\MCPTestMod\\Scripts\\main.lua:14: in main chunk",
    ]


def test_parse_crash_unrecognized_input_raises():
    with pytest.raises(ValueError):
        parse_crash("this is not a crash artifact at all")


def test_parse_native_crash_xml_missing_runtime_properties_raises():
    with pytest.raises(ValueError):
        parse_crash("<?xml version=\"1.0\"?><SomethingElse/>")
