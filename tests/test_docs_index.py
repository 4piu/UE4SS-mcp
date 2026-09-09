from pathlib import Path

from ue4ss_mcp_server.docs_index import build_index
from ue4ss_mcp_server.search import find_symbol, search_symbols

DOCS_SAMPLE = Path(__file__).parent / "fixtures" / "docs_sample"


def test_build_index_lua_finds_top_level_function_and_class():
    entries = build_index(DOCS_SAMPLE, "lua")
    symbols = {e.symbol: e for e in entries}

    assert "RegisterHook" in symbols
    assert symbols["RegisterHook"].kind == "lua-function"
    assert symbols["RegisterHook"].level == 1

    assert "UObject" in symbols
    assert symbols["UObject"].kind == "lua-class"


def test_build_index_lua_indexes_h3_methods_under_their_class():
    entries = build_index(DOCS_SAMPLE, "lua")
    symbols = {e.symbol: e for e in entries}

    assert "GetFullName()" in symbols
    method = symbols["GetFullName()"]
    assert method.kind == "lua-method"
    assert method.parent == "UObject"
    assert method.level == 3


def test_build_index_lua_does_not_index_h2_scaffolding():
    entries = build_index(DOCS_SAMPLE, "lua")
    symbols = {e.symbol for e in entries}

    # "Methods", "Parameters", "Inheritance" etc are structural, not symbols
    assert "Methods" not in symbols
    assert "Parameters" not in symbols
    assert "Inheritance" not in symbols


def test_build_index_cpp_indexes_macros_under_bp_macros_doc():
    entries = build_index(DOCS_SAMPLE, "cpp")
    symbols = {e.symbol: e for e in entries}

    assert "`UE_BEGIN_SCRIPT_FUNCTION_BODY`:" in symbols
    macro = symbols["`UE_BEGIN_SCRIPT_FUNCTION_BODY`:"]
    assert macro.kind == "cpp-entry"
    assert macro.parent == "Blueprint Macros"


def test_search_symbols_name_match_ranks_above_summary_match():
    entries = build_index(DOCS_SAMPLE, "lua")
    result = search_symbols(entries, "hook", limit=20, cursor=None)

    assert result["returned"] >= 1
    assert result["items"][0]["symbol"] == "RegisterHook"


def test_search_symbols_pagination():
    entries = build_index(DOCS_SAMPLE, "lua")
    result = search_symbols(entries, "e", limit=2, cursor=None)  # broad match

    assert result["returned"] == 2
    assert result["truncated"] is True
    assert result["cursor"] == "2"
    assert result["total_matched"] > 2


def test_find_symbol_exact_match():
    entries = build_index(DOCS_SAMPLE, "lua")
    matches = find_symbol(entries, "registerhook")  # case-insensitive

    assert len(matches) == 1
    assert matches[0].symbol == "RegisterHook"
    assert "RegisterHook" in matches[0].content


def test_find_symbol_no_match_returns_empty_list():
    entries = build_index(DOCS_SAMPLE, "lua")
    assert find_symbol(entries, "DoesNotExist") == []


def test_find_symbol_ambiguous_name_returns_all_candidates():
    # GetFullName() is a real method on both UObject and Property
    entries = build_index(DOCS_SAMPLE, "lua")
    matches = find_symbol(entries, "GetFullName()")

    assert len(matches) >= 2
    parents = {m.parent for m in matches}
    assert "UObject" in parents
    assert "Property" in parents


def test_find_symbol_parent_disambiguates():
    entries = build_index(DOCS_SAMPLE, "lua")
    matches = find_symbol(entries, "GetFullName()", parent="Property")

    assert len(matches) == 1
    assert matches[0].parent == "Property"
