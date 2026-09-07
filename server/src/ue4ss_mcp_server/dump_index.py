"""Parses and indexes UE4SS's bulk dumpers (DumpAllActors/DumpAllObjects/
GenerateSDK/GenerateUHTCompatibleHeaders) so dump_and_index/search_dump
can query them without ever inlining the raw dump -- these can run to
hundreds of thousands of lines (ObjectDump.txt) or thousands of files
(CXXHeaderDump/UHTHeaderDump) for a real game.

Formats confirmed against real UE4SS docs (feature-overview/dumpers.md),
not assumed:
- DumpAllActors -> "<timestamp>-ue4ss_actor_data.csv", real CSV, header
  row defines columns (never hand-assumed column names here).
- DumpAllObjects -> "UE4SS_ObjectDump.txt", one object/property per line:
  "[<address>] <Type> <FullName> [n: ...] [c: ...] ...".
- GenerateSDK/GenerateUHTCompatibleHeaders -> a directory of .h/.hpp
  files, one per class/blueprint/package; indexed by scanning for
  class/struct declarations, same H1-style "symbol per declaration"
  approach as docs_index.py uses for markdown.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from ue4ss_mcp_server.envelope import paginate

_OBJECT_DUMP_LINE_RE = re.compile(r"^\[(?P<address>[0-9A-Fa-f]+)\]\s+(?P<obj_type>\S+)\s+(?P<full_name>\S+)\s*(?P<meta>.*)$")
_HEADER_DECL_RE = re.compile(r"^\s*(class|struct)\s+(?:[A-Z_]+_API\s+)?([A-Za-z_][A-Za-z0-9_]*)")


@dataclass
class DumpEntry:
    searchable_text: str
    summary: dict


def parse_actor_csv(path: Path) -> list[DumpEntry]:
    entries = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            entries.append(DumpEntry(searchable_text=" ".join(str(v) for v in row.values()), summary=dict(row)))
    return entries


def parse_object_dump(path: Path) -> list[DumpEntry]:
    entries = []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            m = _OBJECT_DUMP_LINE_RE.match(line)
            if not m:
                continue
            entries.append(
                DumpEntry(
                    searchable_text=f"{m.group('obj_type')} {m.group('full_name')}",
                    summary={
                        "address": m.group("address"),
                        "type": m.group("obj_type"),
                        "full_name": m.group("full_name"),
                        "meta": m.group("meta"),
                    },
                )
            )
    return entries


def parse_header_dir(path: Path) -> list[DumpEntry]:
    entries = []
    for header_file in sorted(path.rglob("*.h*")):
        try:
            lines = header_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for lineno, line in enumerate(lines, start=1):
            m = _HEADER_DECL_RE.match(line)
            if not m:
                continue
            symbol = m.group(2)
            entries.append(
                DumpEntry(
                    searchable_text=symbol,
                    summary={"symbol": symbol, "kind": m.group(1), "file": header_file.name, "line": lineno},
                )
            )
    return entries


def search_dump_entries(entries: list[DumpEntry], query: str, limit: int | None, cursor: str | None) -> dict:
    q = query.lower()
    matched = [e.summary for e in entries if q in e.searchable_text.lower()]
    return paginate(matched, limit, cursor)
