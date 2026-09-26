"""Permanent deletion of one published memory.

The control row is marked deleting before any Kernel cleanup. A restart
continues from that mark. Sources that still contain the text are removed
whole; other memories that pointed at them keep their own text and show a
missing source.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from zhiwo.adapters.kernel_client import KernelHandle, read_version
from zhiwo.api.errors import ApiError
from zhiwo.services.commit_gate import commit_lock
from zhiwo.services.publish import crash_injection_armed

_lock = commit_lock


def preview_deletion(db_path, handle: KernelHandle, memory_id: str) -> dict:
    rows = _version_rows(db_path, memory_id)
    if not rows:
        raise ApiError(404, "NOT_FOUND", "memory not found")
    texts = _texts(handle, rows)
    return {
        "memory_id": memory_id,
        "version_count": len(rows),
        "sources": _matching_sources(db_path, texts),
    }


def delete_memory(db_path, handle: KernelHandle, memory_id: str, request_id: str) -> dict:
    with _lock:
        connection = _connect(db_path)
        try:
            existing = connection.execute("SELECT * FROM operations WHERE id = ?", (request_id,)).fetchone()
            if existing is not None and existing["action"] != "delete":
                raise ApiError(409, "CONFLICT", "this request id was already used for a different action")
            if existing is not None and existing["status"] == "completed":
                stored = _payload(existing["payload_json"])
                if stored.get("memory_id") != memory_id:
                    raise ApiError(409, "CONFLICT", "this request id was already used for a different memory")
                return {"memory_id": memory_id, "status": "deleted"}
            rows = _version_rows(db_path, memory_id)
            if existing is None and not rows:
                raise ApiError(404, "NOT_FOUND", "memory not found")
            texts = _texts(handle, rows) if rows else list(_payload(existing["payload_json"]).get("texts") or [])
            sources = _matching_sources(db_path, texts)
            source_ids = [item["id"] for item in sources]
            payload = {"memory_id": memory_id, "source_ids": source_ids, "texts": texts}
            now = _now()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO operations (
                        id, action, target_id, base_revision, payload_hash, payload_json,
                        revision, kernel_id, status, error_code, created_at, updated_at
                    ) VALUES (?, 'delete', ?, NULL, ?, ?, NULL, NULL, 'prepared', NULL, ?, ?)
                    """,
                    (request_id, memory_id, _digest(payload), json.dumps(payload, ensure_ascii=False), now, now),
                )
            connection.execute(
                "UPDATE memory_refs SET lifecycle = 'deleting' WHERE memory_id = ?",
                (memory_id,),
            )
            connection.commit()
        finally:
            connection.close()
        if crash_injection_armed("delete_marked"):
            import os

            os._exit(86)
        _finish(db_path, handle, request_id)
        return {"memory_id": memory_id, "status": "deleted"}


def resume_deletions(db_path, handle: KernelHandle) -> None:
    with _lock:
        connection = _connect(db_path)
        try:
            rows = connection.execute(
                "SELECT id FROM operations WHERE action = 'delete' AND status = 'prepared'"
            ).fetchall()
        finally:
            connection.close()
        for row in rows:
            _finish(db_path, handle, row["id"])


def _finish(db_path, handle: KernelHandle, request_id: str) -> None:
    connection = _connect(db_path)
    try:
        operation = connection.execute("SELECT * FROM operations WHERE id = ?", (request_id,)).fetchone()
        if operation is None or operation["status"] == "completed":
            return
        payload = _payload(operation["payload_json"])
        memory_id = str(payload.get("memory_id") or operation["target_id"] or "")
        texts = [item for item in payload.get("texts") or [] if isinstance(item, str) and item]
        if not texts:
            texts = _texts(handle, _version_rows(db_path, memory_id))
        source_ids = [item for item in payload.get("source_ids") or [] if isinstance(item, str)]
        if not source_ids:
            source_ids = [item["id"] for item in _matching_sources(db_path, texts)]
        for row in _version_rows(db_path, memory_id):
            _discard(handle, row)
        _scrub_control(connection, memory_id, texts, source_ids)
        connection.execute("DELETE FROM memory_refs WHERE memory_id = ?", (memory_id,))
        done = {"memory_id": memory_id, "source_ids": source_ids, "texts": []}
        connection.execute(
            """
            UPDATE operations
            SET status = 'completed', payload_json = ?, error_code = NULL, updated_at = ?
            WHERE id = ?
            """,
            (json.dumps(done, ensure_ascii=False), _now(), request_id),
        )
        connection.commit()
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()


def _discard(handle: KernelHandle, row) -> None:
    kernel_id = row["kernel_id"]
    if not isinstance(kernel_id, str) or not kernel_id:
        return
    from zhiwo.adapters.derived_cleanup import cleanup_derived_rows
    from zhiwo.adapters.kernel_client import discard_version

    if read_version(handle.db_path, row["kernel_session"], kernel_id) is not None:
        discard_version(handle.db_path, row["kernel_session"], kernel_id)
        return
    cleanup_derived_rows(handle.db_path, kernel_id)


def _scrub_control(connection, memory_id: str, texts: list[str], source_ids: list[str]) -> None:
    if source_ids:
        marks = ",".join("?" for _ in source_ids)
        connection.execute(f"DELETE FROM proposals WHERE source_id IN ({marks})", source_ids)
        connection.execute(f"DELETE FROM import_jobs WHERE source_id IN ({marks})", source_ids)
        connection.execute(f"DELETE FROM sources WHERE id IN ({marks})", source_ids)
    connection.execute("DELETE FROM proposals WHERE target_id = ?", (memory_id,))
    if texts:
        for row in connection.execute("SELECT id, payload_json, evidence_json FROM proposals").fetchall():
            blob = f"{row['payload_json']}\n{row['evidence_json']}"
            if any(text in blob for text in texts):
                connection.execute("DELETE FROM proposals WHERE id = ?", (row["id"],))
        for row in connection.execute(
            "SELECT memory_id, revision, approved_evidence FROM memory_refs WHERE approved_evidence IS NOT NULL"
        ).fetchall():
            evidence = row["approved_evidence"] or ""
            if any(text in evidence for text in texts):
                connection.execute(
                    "UPDATE memory_refs SET approved_evidence = NULL WHERE memory_id = ? AND revision = ?",
                    (row["memory_id"], row["revision"]),
                )
    for row in connection.execute("SELECT id, response_snapshot FROM access_events").fetchall():
        scrubbed = _scrub_json(row["response_snapshot"], memory_id, texts)
        if scrubbed != row["response_snapshot"]:
            connection.execute("UPDATE access_events SET response_snapshot = ? WHERE id = ?", (scrubbed, row["id"]))
    for row in connection.execute("SELECT id, payload_json FROM operations WHERE id != ''").fetchall():
        if not row["payload_json"]:
            continue
        scrubbed = _scrub_json(row["payload_json"], memory_id, texts)
        if scrubbed != row["payload_json"]:
            connection.execute("UPDATE operations SET payload_json = ? WHERE id = ?", (scrubbed, row["id"]))


def _scrub_json(raw: str, memory_id: str, texts: list[str]) -> str:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        cleaned = raw
        for text in texts:
            cleaned = cleaned.replace(text, "")
        return cleaned
    return json.dumps(_blank(parsed, memory_id, texts), ensure_ascii=False)


def _blank(node, memory_id: str, texts: list[str]):
    if isinstance(node, dict):
        hidden = node.get("id") == memory_id or node.get("memory_id") == memory_id
        cleaned = {}
        for key, value in node.items():
            if hidden and key in {"content", "evidence", "text"}:
                cleaned[key] = None
            else:
                cleaned[key] = _blank(value, memory_id, texts)
        return cleaned
    if isinstance(node, list):
        return [_blank(item, memory_id, texts) for item in node]
    if isinstance(node, str) and any(text and text in node for text in texts):
        return ""
    return node


def _matching_sources(db_path, texts: list[str]) -> list[dict]:
    if not texts:
        return []
    connection = _connect(db_path)
    try:
        rows = connection.execute("SELECT id, kind, name, content FROM sources").fetchall()
    finally:
        connection.close()
    found = []
    for row in rows:
        content = row["content"] or ""
        if any(text in content for text in texts):
            found.append({"id": row["id"], "kind": row["kind"], "name": row["name"]})
    return found


def _texts(handle: KernelHandle, rows) -> list[str]:
    found = []
    for row in rows:
        if not row["kernel_id"]:
            continue
        content = read_version(handle.db_path, row["kernel_session"], row["kernel_id"])
        if content and content not in found:
            found.append(content)
    return found


def _version_rows(db_path, memory_id: str):
    connection = _connect(db_path)
    try:
        return connection.execute(
            """
            SELECT memory_id, revision, kernel_id, kernel_session, lifecycle
            FROM memory_refs
            WHERE memory_id = ?
            ORDER BY revision
            """,
            (memory_id,),
        ).fetchall()
    finally:
        connection.close()


def _payload(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _digest(payload: dict) -> str:
    import hashlib

    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _connect(db_path):
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
