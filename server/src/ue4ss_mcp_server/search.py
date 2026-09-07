"""Query logic over a docs_index.DocEntry list: search_api and get_symbol."""

from __future__ import annotations

from ue4ss_mcp_server.docs_index import DocEntry
from ue4ss_mcp_server.envelope import paginate


def search_symbols(
    entries: list[DocEntry], query: str, limit: int | None, cursor: str | None
) -> dict:
    q = query.lower()
    scored: list[tuple[int, DocEntry]] = []
    for entry in entries:
        name_hit = q in entry.symbol.lower()
        summary_hit = q in entry.summary.lower()
        if name_hit or summary_hit:
            scored.append((0 if name_hit else 1, entry))

    scored.sort(key=lambda pair: (pair[0], pair[1].symbol.lower()))
    matched = [
        {
            "symbol": e.symbol,
            "kind": e.kind,
            "parent": e.parent,
            "summary": e.summary,
        }
        for _, e in scored
    ]
    return paginate(matched, limit, cursor)


def find_symbol(
    entries: list[DocEntry], symbol: str, parent: str | None = None
) -> list[DocEntry]:
    """Find exact symbol matches, optionally scoped to a parent class/doc.

    Returns a list rather than picking one: the same method name (e.g.
    `GetFullName()`) is legitimately defined on multiple classes, so an
    unscoped lookup can be genuinely ambiguous. Never silently guess —
    the caller decides what to do with 0, 1, or multiple matches.
    """
    q = symbol.lower()
    matches = [e for e in entries if e.symbol.lower() == q]
    if parent is not None:
        p = parent.lower()
        matches = [e for e in matches if (e.parent or "").lower() == p]
    return matches
