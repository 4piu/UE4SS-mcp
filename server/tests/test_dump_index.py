"""Tests for dump_index.py.

The object-dump fixture is copied verbatim from UE4SS's own bundled
`feature-overview/dumpers.md` example output, so that format is real.
The actor-CSV and CXXHeaderDump fixtures are constructed (no real
DumpAllActors/GenerateSDK sample has been captured yet) -- they exercise
the parsers' *mechanism* (DictReader's column-agnostic CSV parsing, the
class/struct declaration regex) rather than a verified real column
layout or header style. Revisit once a real sample exists.
"""

from pathlib import Path

from ue4ss_mcp_server.dump_index import (
    parse_actor_csv,
    parse_header_dir,
    parse_object_dump,
    search_dump_entries,
)

FIXTURES = Path(__file__).parent / "fixtures" / "dump_samples"


def test_parse_object_dump_real_format_sample():
    entries = parse_object_dump(FIXTURES / "object_dump_sample.txt")

    assert len(entries) == 3
    first = entries[0].summary
    assert first["address"] == "000002A70F57E5C0"
    assert first["type"] == "Function"
    assert first["full_name"] == "/Game/UI/Art/WidgetParts/Basic_ButtonScalable2.Basic_ButtonScalable2_C:BndEvt__Button_0_K2Node_ComponentBoundEvent_0_OnButtonClickedEvent__DelegateSignature"
    assert "[n: 5343AA]" in first["meta"]

    third = entries[2].summary
    assert third["type"] == "BoolProperty"
    assert "IsDesignTime" in third["full_name"]


def test_parse_actor_csv_is_column_agnostic():
    entries = parse_actor_csv(FIXTURES / "actor_data_sample.csv")

    assert len(entries) == 2
    assert entries[0].summary["Name"] == "BP_Phoenix_Player_Controller_C_2147474846"
    assert entries[0].summary["Class"] == "BP_Phoenix_Player_Controller_C"
    assert "BP_Phoenix_Player_Controller_C_2147474846" in entries[0].searchable_text


def test_parse_header_dir_finds_class_and_struct():
    entries = parse_header_dir(FIXTURES / "CXXHeaderDump")

    symbols = {e.summary["symbol"]: e.summary for e in entries}
    assert "UEngine" in symbols
    assert symbols["UEngine"]["kind"] == "class"
    assert symbols["UEngine"]["file"] == "Engine.hpp"
    assert "FEngineStruct" in symbols
    assert symbols["FEngineStruct"]["kind"] == "struct"


def test_search_dump_entries_case_insensitive_and_paginated():
    entries = parse_object_dump(FIXTURES / "object_dump_sample.txt")

    result = search_dump_entries(entries, "isdesigntime", limit=10, cursor=None)
    assert result["returned"] == 1
    assert result["items"][0]["type"] == "BoolProperty"

    # All 3 sample lines are about the same widget, so this matches all of them.
    result = search_dump_entries(entries, "basic_buttonscalable2", limit=1, cursor=None)
    assert result["returned"] == 1
    assert result["truncated"] is True
    assert result["total_matched"] == 3

    result = search_dump_entries(entries, "does-not-exist", limit=10, cursor=None)
    assert result["returned"] == 0
