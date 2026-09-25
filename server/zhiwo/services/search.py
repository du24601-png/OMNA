"""Owner search over published current memories.

A recall row counts toward the limit only after it is an active memory_ref,
matches the requested category, and is still inside valid_until. Unshared
memories stay visible here: this is the owner's own list. Proposal text is
never read.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from zhiwo.adapters.kernel_client import KernelHandle, read_version, recall_rows
from zhiwo.api.errors import ApiError

_MAX_ITEM = 2000
_MAX_TOTAL = 8000
_RECALL_WINDOW = 40


def search_memories(
    db_path,
    handle: KernelHandle,
    query: str,
    limit: int = 10,
    category: str | None = None,
) -> dict:
    text = query.strip()
    if not text or len(text) > 2000:
        raise ApiError(400, "VALIDATION_ERROR", "query must contain 1 to 2000 characters")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 20:
        raise ApiError(400, "VALIDATION_ERROR", "limit must be an integer from 1 to 20")
    return qualify_hits(
        recall_rows(handle, text, _RECALL_WINDOW),
        _active_by_kernel(db_path),
        category=category,
        now=datetime.now(timezone.utc),
        limit=limit,
    )


def qualify_hits(recall: list[dict], published: dict, *, category: str | None, now: datetime, limit: int) -> dict:
    """Keep qualified rows, then apply the limit.

    Expired rows and rows outside the requested category do not consume a slot.
    A later qualified row beyond `limit` sets truncated.
    """
    selected = category.strip() if isinstance(category, str) and category.strip() else None
    items = []
    total = 0
    truncated = False
    for row in recall:
        ref = published.get(str(row.get("id") or ""))
        if ref is None or (selected and ref["category"] != selected) or _expired(ref["valid_until"], now):
            continue
        content = str(row.get("content") or "")
        if len(items) >= limit or len(content) > _MAX_ITEM or total + len(content) > _MAX_TOTAL:
            truncated = True
            break
        items.append(_hit(ref, content))
        total += len(content)
    return {"items": items, "truncated": truncated}


def list_versions(db_path, handle: KernelHandle, memory_id: str) -> dict:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT revision, lifecycle, kernel_id, kernel_session, kind, category, scope,
                   valid_until, operation_id
            FROM memory_refs
            WHERE memory_id = ?
            ORDER BY revision
            """,
            (memory_id,),
        ).fetchall()
    finally:
        connection.close()
    if not rows:
        raise ApiError(404, "NOT_FOUND", "memory not found")
    versions = []
    for row in rows:
        versions.append(
            {
                "revision": row["revision"],
                "lifecycle": row["lifecycle"],
                "kernel_id": row["kernel_id"],
                "content": read_version(handle.db_path, row["kernel_session"], row["kernel_id"]),
                "kind": row["kind"],
                "category": row["category"],
                "scope": row["scope"],
                "valid_until": row["valid_until"],
                "operation_id": row["operation_id"],
            }
        )
    return {"memory_id": memory_id, "versions": versions}


def _hit(ref, content: str) -> dict:
    return {
        "id": ref["memory_id"],
        "revision": ref["revision"],
        "kernel_id": ref["kernel_id"],
        "content": content,
        "kind": ref["kind"],
        "category": ref["category"],
        "scope": ref["scope"],
        "valid_until": ref["valid_until"],
        "share_enabled": bool(ref["share_enabled"]),
        "created_at": ref["created_at"] if "created_at" in ref.keys() else None,
    }


def _expired(valid_until: str | None, now: datetime) -> bool:
    if not valid_until:
        return False
    try:
        parsed = datetime.fromisoformat(valid_until)
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed <= now


def _active_by_kernel(db_path) -> dict[str, sqlite3.Row]:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT memory_id, revision, kernel_id, kind, category, scope, valid_until, share_enabled, created_at
            FROM memory_refs
            WHERE lifecycle = 'active' AND kernel_id IS NOT NULL AND kernel_id != ''
            """
        ).fetchall()
    finally:
        connection.close()
    return {row["kernel_id"]: row for row in rows}
