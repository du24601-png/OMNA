"""One visibility rule for every agent read.

A row is returned only when the connection may use the tool, the category
is granted, and the row is the current shared, unexpired publication.
Filtering happens before the limit. An empty window stays empty.
"""

from __future__ import annotations

from datetime import datetime, timezone

MAX_ITEM = 2000
MAX_TOTAL = 8000
RECALL_WINDOW = 40


def memory_visible(principal, ref, now: datetime) -> bool:
    if _value(ref, "lifecycle") != "active":
        return False
    if not _value(ref, "share_enabled"):
        return False
    if _value(ref, "category") not in principal.allowed_categories:
        return False
    return not expired(_value(ref, "valid_until"), now)


def select_visible(recall: list[dict], published: dict, principal, *, limit: int, now: datetime) -> dict:
    """Keep granted rows in recall order, then apply the count and size limits.

    Rows that fail the visibility rule do not consume a slot. One oversized
    item is skipped so later granted rows can still be returned. A later
    granted row that does not fit the count or the total size sets truncated
    and is left out whole.
    """
    items = []
    total = 0
    truncated = False
    for row in recall:
        ref = published.get(str(row.get("id") or ""))
        if ref is None or not memory_visible(principal, ref, now):
            continue
        content = str(row.get("content") or "")
        if len(content) > MAX_ITEM:
            truncated = True
            continue
        if len(items) >= limit or total + len(content) > MAX_TOTAL:
            truncated = True
            break
        items.append(_item(ref, content))
        total += len(content)
    return {"items": items, "truncated": truncated}


def expired(valid_until: str | None, now: datetime) -> bool:
    if not valid_until:
        return False
    try:
        parsed = datetime.fromisoformat(valid_until)
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed <= now


def _item(ref, content: str) -> dict:
    return {
        "id": _value(ref, "memory_id"),
        "revision": int(_value(ref, "revision")),
        "content": content,
        "kind": _value(ref, "kind"),
        "category": _value(ref, "category"),
        "scope": _value(ref, "scope"),
        "valid_until": _value(ref, "valid_until"),
    }


def _value(ref, key):
    if isinstance(ref, dict):
        return ref.get(key)
    try:
        return ref[key]
    except (KeyError, IndexError):
        return None
