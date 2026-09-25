"""Owner search over published memories.

Recall rows that are not an active memory_ref are dropped. Proposal text
is never read here.
"""

from __future__ import annotations

import sqlite3

from zhiwo.adapters.kernel_client import KernelHandle, read_version, recall_rows
from zhiwo.api.errors import ApiError

_MAX_ITEM = 2000
_MAX_TOTAL = 8000
_RECALL_WINDOW = 40


def search_memories(db_path, handle: KernelHandle, query: str, limit: int = 10) -> dict:
    text = query.strip()
    if not text or len(text) > 2000:
        raise ApiError(400, "VALIDATION_ERROR", "query must contain 1 to 2000 characters")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 20:
        raise ApiError(400, "VALIDATION_ERROR", "limit must be an integer from 1 to 20")
    published = _active_by_kernel(db_path)
    items = []
    total = 0
    truncated = False
    for row in recall_rows(handle, text, _RECALL_WINDOW):
        ref = published.get(row["id"])
        if ref is None:
            continue
        content = row["content"]
        if len(items) >= limit or len(content) > _MAX_ITEM or total + len(content) > _MAX_TOTAL:
            truncated = True
            break
        items.append(
            {
                "id": ref["memory_id"],
                "revision": ref["revision"],
                "kernel_id": ref["kernel_id"],
                "content": content,
                "kind": ref["kind"],
                "category": ref["category"],
                "scope": ref["scope"],
                "valid_until": ref["valid_until"],
                "share_enabled": bool(ref["share_enabled"]),
            }
        )
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


def _active_by_kernel(db_path) -> dict[str, sqlite3.Row]:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT memory_id, revision, kernel_id, kind, category, scope, valid_until, share_enabled
            FROM memory_refs
            WHERE lifecycle = 'active' AND kernel_id IS NOT NULL AND kernel_id != ''
            """
        ).fetchall()
    finally:
        connection.close()
    return {row["kernel_id"]: row for row in rows}
