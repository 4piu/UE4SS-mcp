"""Parses UE4SS.log for parse_log.

Every pattern here is confirmed against real captured logs from this
project's own build-up (M0's clean bring-up log, the M6 live dump
tests, and the M7 crash test), not invented from documentation alone:
"Starting Lua/C++ mod 'X'", "Mod 'X' disabled in mods.txt.",
"PS Scan attempt N", "[PS] Found X: ADDR", "[Warning]"-tagged lines, and
timestamped "Error:"/"Fatal Error:" lines.

Note: the ORIGINAL spec draft called this field "signature_failures" --
renamed to ps_scan_attempts/ps_signatures_found once real logs were
available to check against, since there's no real log sample of a named
signature actually failing (only ever a scan needing more than one
attempt, which is the real, checkable signal this project has actually
observed -- see dev-notes/progress.md's M7 section).
"""

from __future__ import annotations

import re

from ue4ss_mcp_server.context import parse_log_banner
from ue4ss_mcp_server.envelope import paginate

_MOD_START_RE = re.compile(r"Starting (Lua|C\+\+) mod '([^']+)'")
_MOD_DISABLED_RE = re.compile(r"Mod '([^']+)' disabled in mods\.txt\.")
_PS_ATTEMPT_RE = re.compile(r"PS Scan attempt \d+")
# Signature names can themselves contain "::" (e.g. "FName::ToString")
# or even "(...)" (e.g. "FName::FName(wchar_t*)") -- greedy .+ up to the
# LAST ": 0x" is what correctly keeps the whole name intact; a naive
# "up to the first colon" match (tried first, wrong) truncates these.
_PS_FOUND_RE = re.compile(r"\[PS\] Found (.+): 0x[0-9a-fA-F]+")
_WARNING_RE = re.compile(r"\[Warning\]")
_ERROR_LINE_RE = re.compile(r"^\[[\d\- :.]+\]\s*(Fatal Error|Error):\s*(.*)$")


def parse_log(text: str, limit: int | None = None, cursor: str | None = None) -> dict:
    banner = parse_log_banner(text)

    mods = [
        {"name": m.group(2), "kind": "lua" if m.group(1) == "Lua" else "cpp", "enabled": True}
        for m in _MOD_START_RE.finditer(text)
    ]
    mods.extend({"name": m.group(1), "kind": "unknown", "enabled": False} for m in _MOD_DISABLED_RE.finditer(text))

    errors = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        m = _ERROR_LINE_RE.match(line)
        if m:
            errors.append({"line": lineno, "severity": "fatal" if m.group(1) == "Fatal Error" else "error", "text": m.group(2).strip()})

    return {
        "version": banner.get("version"),
        "build_sha": banner.get("build_sha"),
        "channel": banner.get("channel"),
        "game": banner.get("game"),
        "mods": mods,
        "ps_scan_attempts": len(_PS_ATTEMPT_RE.findall(text)),
        "ps_signatures_found": [m.group(1) for m in _PS_FOUND_RE.finditer(text)],
        "warnings_count": len(_WARNING_RE.findall(text)),
        "errors": paginate(errors, limit, cursor),
    }
