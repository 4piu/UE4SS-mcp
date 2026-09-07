"""Upgrade-guide lookup between two UE4SS versions.

upgrade-guide.md doesn't exist in every tagged release — confirmed it's
absent from v3.0.1 entirely. Since it's inherently a cross-version,
cumulative document (each transition gets its own section, appended over
time), the right source for it is always the most current docs, never the
specific from/to tag being asked about — an old numbered tag can't
retroactively contain guidance about transitions documented after it was
cut.

That "most current" ref is `main`, not `experimental-latest`: confirmed
via the GitHub API that `main` is the repo's actual default branch, and
that `experimental-latest` (a release tag, not main's HEAD) points to a
different, older commit — it apparently isn't re-cut on every doc change
to main, so it lagged behind and was missing upgrade-guide.md entirely
when checked. `main` is treated as a moving ref (re-fetched once per
server process) same as experimental/experimental-latest.
"""

from __future__ import annotations

import re
from pathlib import Path

DEFAULT_SOURCE_REF = "main"

_SECTION_RE = re.compile(r"^## Version (\d+)\.x to (\d+)\.x\s*$")


def _major(version: str) -> int:
    cleaned = version.lstrip("vV")
    return int(cleaned.split(".")[0])


def get_upgrade_notes(
    docs_root: Path, from_version: str, to_version: str, source_ref: str
) -> dict:
    path = docs_root / "upgrade-guide.md"
    if not path.exists():
        return {
            "sections": [],
            "source_ref": source_ref,
            "note": f"no upgrade-guide.md found at ref {source_ref!r}",
        }

    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    headings: list[tuple[int, int, int]] = []  # (line_idx, from_major, to_major)
    for i, line in enumerate(lines):
        m = _SECTION_RE.match(line)
        if m:
            headings.append((i, int(m.group(1)), int(m.group(2))))

    lo, hi = sorted((_major(from_version), _major(to_version)))

    sections = []
    for idx, (line_idx, sec_from, sec_to) in enumerate(headings):
        if sec_to < lo or sec_from > hi:
            continue
        end = headings[idx + 1][0] if idx + 1 < len(headings) else len(lines)
        sections.append(
            {
                "heading": lines[line_idx].lstrip("#").strip(),
                "content": "\n".join(lines[line_idx:end]).rstrip(),
            }
        )

    result = {"sections": sections, "source_ref": source_ref}
    if not sections:
        result["note"] = (
            f"no documented transition covers major versions {lo}-{hi} "
            f"in upgrade-guide.md at ref {source_ref!r}"
        )
    return result
