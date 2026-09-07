from pathlib import Path

from ue4ss_mcp_server.context import parse_log_banner, resolve_context

FIXTURE = Path(__file__).parent / "fixtures" / "hogwarts_legacy_ue4ss.log"


def test_parse_log_banner_extracts_version_and_game():
    text = FIXTURE.read_text(encoding="utf-8")
    parsed = parse_log_banner(text)

    assert parsed["version"] == "3.0.1"
    assert parsed["build_sha"]
    assert parsed["game"] == "HogwartsLegacy"


def test_resolve_context_from_log_path():
    ctx = resolve_context(log_path=str(FIXTURE))

    assert ctx.source == "log"
    assert ctx.version == "3.0.1"
    assert ctx.game == "HogwartsLegacy"


def test_explicit_values_win_over_log():
    ctx = resolve_context(game="OverrideGame", version="9.9.9", log_path=str(FIXTURE))

    assert ctx.game == "OverrideGame"
    assert ctx.version == "9.9.9"
    # still "log" sourced since a log was provided; explicit values just
    # take precedence over what was parsed from it
    assert ctx.source == "log"


def test_resolve_context_explicit_only_no_log():
    ctx = resolve_context(game="SomeGame", version="v3.0.1")

    assert ctx.source == "explicit"
    assert ctx.game == "SomeGame"
    assert ctx.version == "v3.0.1"


def test_parse_log_banner_empty_text_returns_empty_dict():
    assert parse_log_banner("") == {}
