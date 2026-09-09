"""Standard paginated-response envelope for every list/search-shaped tool.

Contract from dev-notes/spec.md §7: cap+paginate by default, always signal
truncation explicitly with a total count, never let a tool return "all
matches". This exists so every tool implements pagination the same way
instead of each one inventing its own shape.
"""

from __future__ import annotations

DEFAULT_LIMIT = 20
MAX_LIMIT = 200


def paginate(items: list, limit: int | None, cursor: str | None) -> dict:
    limit = min(limit or DEFAULT_LIMIT, MAX_LIMIT)
    offset = int(cursor) if cursor else 0

    page = items[offset : offset + limit]
    next_offset = offset + limit
    truncated = next_offset < len(items)

    return {
        "items": page,
        "returned": len(page),
        "total_matched": len(items),
        "truncated": truncated,
        "cursor": str(next_offset) if truncated else None,
    }
