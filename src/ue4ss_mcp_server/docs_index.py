"""Symbol index over RE-UE4SS's lua-api/cpp-api docs for a cached ref.

Real doc structure (confirmed against the actual v3.0.1 and v4.0.0
docs, not assumed): each file's H1 is the thing's own name (a class, a
global function, a table) and is always indexed. Below that, heading
*level* doesn't reliably separate symbols from scaffolding: most H2s are
structural ("Parameters", "Methods", "Return Value"), but some pages put
real symbols at H2 too -- `type()` / `ForEachProperty(function
Callback)` on class pages (already in v3.0.1), and v4.0.0's
modmanagement.md lists `RestartMod`/`UninstallMod`/... as H2s. Likewise
most H3s are real sub-symbols (class methods, cpp-api macros), but
v4.0.0's delayedactions.md also uses H3 for prose like "Simple Delay"
or "Parameters". So H2/H3 are indexed by what the heading text looks
like (see `_looks_like_symbol`), not by level -- see the docs_index
tests for the concrete examples this rule is drawn from.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_HEADING_RE = re.compile(r"^(#{1,3})\s+(.*\S)\s*$")

# A call signature: identifier immediately followed by "(" -- matches
# "GetFullName()", "set(auto NewValue)", "Get() / get()", but not prose
# with a parenthetical like "Parameters (overload #1)".
_CALL_RE = re.compile(r"^[A-Za-z_][\w.:]*\(")
_IDENT_RE = re.compile(r"^[A-Za-z_][\w.:]*$")
# A bare identifier only counts if it's visibly code-shaped (snake_case,
# camelCase/PascalCase compound, or an acronym run) -- otherwise
# single-word scaffolding like "Parameters"/"Example"/"Inheritance"
# would be indistinguishable from a real name.
_CODEY_RE = re.compile(r"_|[a-z][A-Z]|[A-Z]{2}")


def _looks_like_symbol(heading_text: str) -> bool:
    """Whether an H2/H3 heading names a real symbol rather than being
    structural scaffolding or prose. See the module docstring."""
    text = heading_text.strip().strip("`:").strip()
    if _CALL_RE.match(text):
        return True
    return bool(_IDENT_RE.match(text) and _CODEY_RE.search(text))


@dataclass
class DocEntry:
    symbol: str
    kind: str
    file: str  # relative to docs/, forward-slash separated
    line: int  # 1-indexed
    level: int  # 1, 2 or 3 (see module docstring for which H2/H3s count)
    parent: str | None  # nearest enclosing H1 symbol, for H2/H3 entries
    summary: str
    content: str


def _kind_for_file(rel_path: Path, level: int) -> str:
    parts = rel_path.parts
    if parts[0] == "lua-api":
        if len(parts) >= 2 and parts[1] == "classes":
            return "lua-method" if level >= 2 else "lua-class"
        if len(parts) >= 2 and parts[1] == "global-functions":
            return "lua-function"
        if len(parts) >= 2 and parts[1] == "table-definitions":
            return "lua-table"
        return "lua-doc"
    if parts[0] == "cpp-api" or rel_path.name == "cpp-api.md":
        return "cpp-entry" if level >= 2 else "cpp-doc"
    return "doc"


def _first_summary_line(lines: list[str], start: int, end: int) -> str:
    for line in lines[start:end]:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and not stripped.startswith(">"):
            return stripped[:200]
    return ""


def _index_file(docs_root: Path, path: Path) -> list[DocEntry]:
    rel = path.relative_to(docs_root)
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    headings: list[tuple[int, int, str]] = []  # (line_idx, level, text)
    for i, line in enumerate(lines):
        m = _HEADING_RE.match(line)
        if m:
            headings.append((i, len(m.group(1)), m.group(2)))

    entries: list[DocEntry] = []
    current_h1: str | None = None
    for idx, (line_idx, level, heading_text) in enumerate(headings):
        if level == 1:
            current_h1 = heading_text
        elif not _looks_like_symbol(heading_text):
            continue  # structural scaffolding or prose, not a symbol

        # section runs until the next heading of level <= this one
        # (so an H1's content includes its nested H2/H3s; an H2/H3's
        # content stops at the next heading at its own level or above)
        end = len(lines)
        for later_idx, later_level, _ in headings[idx + 1 :]:
            if later_level <= level:
                end = later_idx
                break

        entries.append(
            DocEntry(
                symbol=heading_text,
                kind=_kind_for_file(rel, level),
                file=str(rel).replace("\\", "/"),
                line=line_idx + 1,
                level=level,
                parent=current_h1 if level >= 2 else None,
                summary=_first_summary_line(lines, line_idx + 1, end),
                content="\n".join(lines[line_idx:end]).rstrip(),
            )
        )
    return entries


def build_index(docs_root: Path, api: str) -> list[DocEntry]:
    """Index every H1 and symbol-like H2/H3 under docs_root for the given api ('lua'|'cpp')."""
    subdir_name = f"{api}-api"
    candidates: list[Path] = []

    top_level = docs_root / f"{subdir_name}.md"
    if top_level.exists():
        candidates.append(top_level)

    sub_root = docs_root / subdir_name
    if sub_root.exists():
        candidates.extend(sorted(sub_root.rglob("*.md")))

    entries: list[DocEntry] = []
    for path in candidates:
        entries.extend(_index_file(docs_root, path))
    return entries
