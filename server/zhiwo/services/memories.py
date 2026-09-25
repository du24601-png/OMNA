"""Owner views of published memories and stored sources.

Content is read from the Kernel. This module does not keep a second fact store.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from zhiwo.adapters.kernel_client import KernelHandle, read_version
from zhiwo.api.errors import ApiError
from zhiwo.services.publish import publish_memory

_STATES = {"current", "expired", "history"}


def list_memories(
    db_path,
    handle: KernelHandle,
    *,
    state: str = "current",
    category: str | None = None,
    query: str | None = None,
    limit: int = 50,
) -> dict:
    if state not in _STATES:
        raise ApiError(400, "VALIDATION_ERROR", "state must be current, expired, or history")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 50:
        raise ApiError(400, "VALIDATION_ERROR", "limit must be an integer from 1 to 50")
    text = query.strip() if isinstance(query, str) else ""
    if len(text) > 2000:
        raise ApiError(400, "VALIDATION_ERROR", "query must contain at most 2000 characters")
    now = datetime.now(timezone.utc)
    items = []
    for row in _rows(db_path):
        if category and row["category"] != category:
            continue
        expired = _expired(row["valid_until"], now)
        if state == "history":
            if row["lifecycle"] != "superseded":
                continue
        elif row["lifecycle"] != "active":
            continue
        elif state == "expired" and not expired:
            continue
        elif state == "current" and expired:
            continue
        content = read_version(handle.db_path, row["kernel_session"], row["kernel_id"])
        if text and text not in (content or ""):
            continue
        items.append(_item(row, content))
    items.sort(key=lambda item: item["created_at"], reverse=True)
    return {"items": items[:limit]}


def get_memory(db_path, handle: KernelHandle, memory_id: str) -> dict:
    rows = [row for row in _rows(db_path) if row["memory_id"] == memory_id]
    if not rows:
        raise ApiError(404, "NOT_FOUND", "memory not found")
    active = [row for row in rows if row["lifecycle"] == "active"]
    row = active[0] if active else rows[-1]
    content = read_version(handle.db_path, row["kernel_session"], row["kernel_id"])
    return _item(row, content)


def update_memory(db_path, handle: KernelHandle, memory_id: str, request_id: str, payload: dict) -> dict:
    body = dict(payload)
    body["target_id"] = memory_id
    return publish_memory(db_path, handle, request_id, body)


def get_source(db_path, source_id: str) -> dict:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
    finally:
        connection.close()
    if row is None:
        raise ApiError(404, "NOT_FOUND", "source not found")
    return {
        "id": row["id"],
        "kind": row["kind"],
        "name": row["name"],
        "content": row["content"],
        "imported_at": row["imported_at"],
    }


def build_profile(db_path, handle: KernelHandle) -> dict:
    now = datetime.now(timezone.utc)
    groups = {
        "identity": [],
        "goal": [],
        "preference": [],
        "project": [],
    }
    recent = []
    for row in _rows(db_path):
        if row["lifecycle"] != "active" or _expired(row["valid_until"], now):
            continue
        content = read_version(handle.db_path, row["kernel_session"], row["kernel_id"])
        if not content:
            continue
        item = _item(row, content)
        if row["kind"] == "event" or row["category"] == "event":
            recent.append(item)
            continue
        if row["kind"] == "fact" and row["category"] in groups:
            groups[row["category"]].append(item)
    recent.sort(key=lambda item: item["created_at"], reverse=True)
    return {
        "groups": [
            {"id": "identity", "title": "身份", "cards": groups["identity"]},
            {"id": "goal", "title": "目标", "cards": groups["goal"]},
            {"id": "preference", "title": "偏好", "cards": groups["preference"]},
            {"id": "project", "title": "项目", "cards": groups["project"]},
        ],
        "recent": recent[:8],
    }


def _rows(db_path) -> list[sqlite3.Row]:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(
            """
            SELECT memory_id, revision, kernel_id, kernel_session, kind, category, scope,
                   lifecycle, share_enabled, valid_until, source_refs, created_at
            FROM memory_refs
            WHERE lifecycle IN ('active', 'superseded')
            ORDER BY created_at, memory_id, revision
            """
        ).fetchall()
    finally:
        connection.close()


def _item(row, content: str | None) -> dict:
    return {
        "id": row["memory_id"],
        "revision": row["revision"],
        "content": content,
        "kind": row["kind"],
        "category": row["category"],
        "scope": row["scope"],
        "lifecycle": row["lifecycle"],
        "share_enabled": bool(row["share_enabled"]),
        "valid_until": row["valid_until"],
        "source_ids": _source_ids(row["source_refs"]),
        "created_at": row["created_at"],
        "readable": content is not None,
    }


def _source_ids(raw) -> list[str]:
    if not raw:
        return []
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(loaded, list):
        return []
    return [item for item in loaded if isinstance(item, str)]


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
