"""Owner views of published memories and stored sources.

Content is read from the Kernel. This module does not keep a second fact store.
"""

from __future__ import annotations

import base64
import json
import sqlite3
from datetime import datetime, timezone

from zhiwo.adapters.kernel_client import KernelHandle, read_version
from zhiwo.api.errors import ApiError
from zhiwo.services.publish import publish_memory

_STATES = {"all", "current", "expired", "history"}
_SORTS = {"newest", "oldest"}


def list_memories(
    db_path,
    handle: KernelHandle,
    *,
    state: str = "current",
    category: str | None = None,
    origin: str | None = None,
    query: str | None = None,
    limit: int = 50,
    sort: str = "newest",
    cursor: str | None = None,
) -> dict:
    if state not in _STATES:
        raise ApiError(400, "VALIDATION_ERROR", "state must be all, current, expired, or history")
    if sort not in _SORTS:
        raise ApiError(400, "VALIDATION_ERROR", "sort must be newest or oldest")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 50:
        raise ApiError(400, "VALIDATION_ERROR", "limit must be an integer from 1 to 50")
    selected = parse_origin(origin)
    text = query.strip() if isinstance(query, str) else ""
    if len(text) > 2000:
        raise ApiError(400, "VALIDATION_ERROR", "query must contain at most 2000 characters")
    mark = _decode_cursor(cursor)
    sources, proposals, agents, clients = _origin_context(db_path)
    now = datetime.now(timezone.utc)
    matched: list[tuple] = []
    for row in _rows(db_path):
        if category and row["category"] != category:
            continue
        expired = _expired(row["valid_until"], now)
        if state == "all":
            pass
        elif state == "history":
            if row["lifecycle"] != "superseded":
                continue
        elif row["lifecycle"] != "active":
            continue
        elif state == "expired" and not expired:
            continue
        elif state == "current" and expired:
            continue
        label = _origin(row["source_refs"], sources, proposals, agents, clients)
        if selected and origin_key(label) != selected:
            continue
        matched.append((row, label))
    newest = sort == "newest"
    ordered = _ordered([row for row, _label in matched], newest)
    labels = {(row["memory_id"], row["revision"]): label for row, label in matched}
    total = len(ordered)
    if text:
        readable = {
            (row["memory_id"], row["revision"]): read_version(handle.db_path, row["kernel_session"], row["kernel_id"])
            for row in ordered
        }
        ordered = [row for row in ordered if text in (readable[(row["memory_id"], row["revision"])] or "")]
        total = len(ordered)
    else:
        readable = {}
    window = [row for row in ordered if mark is None or _follows(row, mark, newest)]
    page = window[:limit]
    if not text:
        readable = {
            (row["memory_id"], row["revision"]): read_version(handle.db_path, row["kernel_session"], row["kernel_id"])
            for row in page
        }
    items = []
    for row in page:
        item = _item(row, readable[(row["memory_id"], row["revision"])])
        item["origin"] = labels[(row["memory_id"], row["revision"])]
        items.append(item)
    next_cursor = _encode_cursor(page[-1]) if len(window) > limit else None
    return {
        "items": items,
        "origins": list_origin_choices(db_path),
        "total": total,
        "next_cursor": next_cursor,
    }


def _ordered(rows: list, newest: bool) -> list:
    """created_at follows the requested direction. Equal times stay in id, then revision, order."""
    rows = sorted(rows, key=lambda row: (row["memory_id"], int(row["revision"])))
    return sorted(rows, key=lambda row: row["created_at"] or "", reverse=newest)


def _follows(row, mark: tuple[str, str, int], newest: bool) -> bool:
    created = row["created_at"] or ""
    mark_created, mark_id, mark_revision = mark
    if created != mark_created:
        return created < mark_created if newest else created > mark_created
    if row["memory_id"] != mark_id:
        return row["memory_id"] > mark_id
    return int(row["revision"]) > mark_revision


def _encode_cursor(row) -> str:
    payload = json.dumps(
        {"t": row["created_at"] or "", "id": row["memory_id"], "r": int(row["revision"])},
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_cursor(value: str | None) -> tuple[str, str, int] | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ApiError(400, "VALIDATION_ERROR", "cursor is invalid")
    token = value.strip()
    if not token:
        return None
    if len(token) > 512:
        raise ApiError(400, "VALIDATION_ERROR", "cursor is invalid")
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        loaded = json.loads(raw)
    except (ValueError, json.JSONDecodeError, UnicodeError):
        raise ApiError(400, "VALIDATION_ERROR", "cursor is invalid") from None
    if not isinstance(loaded, dict):
        raise ApiError(400, "VALIDATION_ERROR", "cursor is invalid")
    created = loaded.get("t")
    memory_id = loaded.get("id")
    revision = loaded.get("r")
    if not isinstance(created, str) or len(created) > 80:
        raise ApiError(400, "VALIDATION_ERROR", "cursor is invalid")
    if not isinstance(memory_id, str) or not memory_id or len(memory_id) > 80:
        raise ApiError(400, "VALIDATION_ERROR", "cursor is invalid")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise ApiError(400, "VALIDATION_ERROR", "cursor is invalid")
    return created, memory_id, revision


def get_memory(db_path, handle: KernelHandle, memory_id: str) -> dict:
    rows = [row for row in _rows(db_path) if row["memory_id"] == memory_id]
    if not rows:
        raise ApiError(404, "NOT_FOUND", "memory not found")
    active = [row for row in rows if row["lifecycle"] == "active"]
    row = active[0] if active else rows[-1]
    content = read_version(handle.db_path, row["kernel_session"], row["kernel_id"])
    item = _item(row, content)
    item["evidence"] = _approved_evidence(db_path, row["memory_id"], row["revision"])
    return item


def _approved_evidence(db_path, memory_id: str, revision: int) -> str | None:
    """The evidence fragment the owner approved for this version, for the detail view."""
    connection = sqlite3.connect(db_path)
    try:
        found = connection.execute(
            "SELECT approved_evidence FROM memory_refs WHERE memory_id = ? AND revision = ?",
            (memory_id, revision),
        ).fetchone()
    finally:
        connection.close()
    if found is None or not found[0]:
        return None
    try:
        loaded = json.loads(found[0])
    except json.JSONDecodeError:
        return None
    text = loaded.get("text") if isinstance(loaded, dict) else None
    return text.strip() or None if isinstance(text, str) else None


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


_CLIENT_NAMES = {
    "WorkBuddy": "workbuddy",
    "ZCode": "zcode",
    "OpenCode": "opencode",
    "ChatGPT": "codex",
    "Codex": "codex",
    "Claude": "claude",
    "Claude Code": "claude-code",
}
_KNOWN_CLIENTS = set(_CLIENT_NAMES.values())
_ORIGIN_IDS = ("omna", "workbuddy", "zcode", "opencode", "codex", "claude", "claude-code")


def parse_origin(value: str | None) -> str | None:
    """Accept a known client id, or agent:<name> for a custom connection."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ApiError(400, "VALIDATION_ERROR", "来源筛选无效。")
    cleaned = value.strip()
    if not cleaned:
        return None
    if cleaned in _ORIGIN_IDS:
        return cleaned
    name = cleaned[6:] if cleaned.startswith("agent:") else ""
    if name and len(name) <= 200 and "\n" not in name and "\r" not in name and "\x00" not in name:
        return cleaned
    raise ApiError(400, "VALIDATION_ERROR", "来源筛选无效。")


def origin_key(origin: dict | None) -> str:
    if not isinstance(origin, dict):
        return "omna"
    client = origin.get("client")
    if client in _ORIGIN_IDS:
        return str(client)
    name = origin.get("name") if isinstance(origin.get("name"), str) else ""
    return "agent:" + (name or "Agent 提案")


def list_origin_choices(db_path) -> list[dict]:
    """Distinct sources across the library, in the same order as the filter."""
    sources, proposals, agents, clients = _origin_context(db_path)
    seen: dict[str, dict] = {}
    for row in _rows(db_path):
        label = _origin(row["source_refs"], sources, proposals, agents, clients)
        key = origin_key(label)
        if key not in seen:
            seen[key] = {"id": key, "name": label["name"]}
    ordered = [seen[key] for key in _ORIGIN_IDS if key in seen]
    rest = [seen[key] for key in seen if key not in _ORIGIN_IDS]
    rest.sort(key=lambda item: item["name"])
    return ordered + rest


def attach_origins(db_path, items: list[dict]) -> None:
    """Label each listed memory with the client that proposed it, or OMNA."""
    if not items:
        return
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        ids = [item["id"] for item in items if isinstance(item.get("id"), str)]
        refs = []
        if ids:
            marks = ",".join("?" for _ in ids)
            refs = connection.execute(
                f"SELECT memory_id, revision, source_refs FROM memory_refs WHERE memory_id IN ({marks})",
                ids,
            ).fetchall()
    finally:
        connection.close()
    sources, proposals, agents, clients = _origin_context(db_path)
    by_version = {(row["memory_id"], row["revision"]): row["source_refs"] for row in refs}
    for item in items:
        item["origin"] = _origin(by_version.get((item.get("id"), item.get("revision"))), sources, proposals, agents, clients)


def _origin_context(db_path):
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        sources = {row["id"]: row["kind"] for row in connection.execute("SELECT id, kind FROM sources")}
        proposals = {
            row["source_id"]: row["agent_id"]
            for row in connection.execute("SELECT source_id, agent_id FROM proposals WHERE agent_id IS NOT NULL")
        }
        agents = {row["id"]: row["name"] for row in connection.execute("SELECT id, name FROM agents")}
        clients: dict[str, str] = {}
        for row in connection.execute("SELECT key, value FROM settings WHERE key LIKE 'client_agent:%'"):
            client_id = row["key"].split(":", 1)[1]
            if client_id in _KNOWN_CLIENTS and isinstance(row["value"], str):
                clients[row["value"]] = client_id
        return sources, proposals, agents, clients
    finally:
        connection.close()


def requester_labels(db_path) -> tuple[dict, dict]:
    """Agent id to display name, and agent id to a known client id."""
    _, _, agents, clients = _origin_context(db_path)
    return agents, clients


def requester_of(agent_id: str | None, agents: dict, clients: dict) -> dict:
    """The client mark for a proposal. Imports and unknown agents stay OMNA or a letter."""
    if not agent_id:
        return {"client": "omna", "name": "OMNA"}
    name = agents.get(agent_id)
    if not name:
        return {"client": "agent", "name": "Agent 提案"}
    client = clients.get(agent_id) or _CLIENT_NAMES.get(name, "agent")
    if client not in _KNOWN_CLIENTS:
        client = "agent"
    return {"client": client, "name": name}


def _origin(raw, sources: dict, proposals: dict, agents: dict, clients: dict) -> dict:
    for source_id in _source_ids(raw):
        if sources.get(source_id) != "agent_claim":
            continue
        agent_id = proposals.get(source_id)
        name = agents.get(agent_id) if agent_id else None
        if not name:
            return {"client": "agent", "name": "Agent 提案"}
        client = clients.get(agent_id) or _CLIENT_NAMES.get(name, "agent")
        if client not in _KNOWN_CLIENTS:
            client = "agent"
        return {"client": client, "name": name}
    return {"client": "omna", "name": "OMNA"}


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
