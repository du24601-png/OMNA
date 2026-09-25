"""The only official publish entry.

Manual save and review both call this. An active memory_ref is written only
after the Kernel row and its local embedding exist. The same operation id
returns the original memory. An update adds a new Kernel row, then makes
that row current and the previous row historical in one control transaction.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from zhiwo.adapters.kernel_client import (
    KernelHandle,
    discard_version,
    has_embedding,
    read_version,
    session_rows,
    write_version,
)
from zhiwo.api.errors import ApiError
from zhiwo.services.commit_gate import commit_lock

_lock = commit_lock
_MAX_CONTENT = 2000
_KINDS = {"fact", "event"}
_CATEGORIES = {"identity", "goal", "preference", "project", "event", "other"}
VERSION_CONFLICT = "the memory changed since this review; the proposal is still pending"


def publish_lock():
    """Lock shared with agent control changes and tool responses."""
    return _lock


def canonical_record(payload: dict) -> dict:
    return _record(payload)


def publish_memory(db_path, handle: KernelHandle, request_id: str, payload: dict, *, finalize=None) -> dict:
    record = _record(payload)
    digest = _digest(record)
    with _lock:
        return _publish(db_path, handle, request_id, record, digest, finalize)


def operation_result(db_path, handle: KernelHandle, operation_id: str) -> dict:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        operation = connection.execute("SELECT * FROM operations WHERE id = ?", (operation_id,)).fetchone()
        if operation is None or operation["status"] != "completed":
            raise ApiError(503, "KERNEL_UNAVAILABLE", "the completed operation has no active memory", retryable=True)
        return _completed(connection, handle, operation)
    finally:
        connection.close()


def operation_status(db_path, operation_id: str) -> dict:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute("SELECT * FROM operations WHERE id = ?", (operation_id,)).fetchone()
    finally:
        connection.close()
    if row is None:
        raise ApiError(404, "NOT_FOUND", "operation not found")
    return {
        "operation_id": row["id"],
        "action": row["action"],
        "status": row["status"],
        "target_id": row["target_id"],
        "base_revision": row["base_revision"],
        "revision": row["revision"],
        "error_code": row["error_code"],
    }


def _publish(db_path, handle: KernelHandle, request_id: str, record: dict, digest: str, finalize) -> dict:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        existing = connection.execute("SELECT * FROM operations WHERE id = ?", (request_id,)).fetchone()
        if existing is not None and existing["payload_hash"] != digest:
            raise ApiError(409, "CONFLICT", "this request id was already used with a different payload")
        if existing is not None and existing["status"] == "completed":
            return _completed(connection, handle, existing)
        if not handle.embeddings_loaded:
            _save_operation(
                connection, request_id, existing, digest, record, None, None, None, "failed", "MODEL_UNAVAILABLE"
            )
            raise ApiError(503, "MODEL_UNAVAILABLE", "local embedding model is not ready", retryable=True)
        memory_id, revision = _identity(connection, existing, record)
        session_id = handle.session_for(memory_id, revision)
        _save_operation(
            connection, request_id, existing, digest, record, memory_id, revision, None, "prepared", None
        )
        existing = connection.execute("SELECT * FROM operations WHERE id = ?", (request_id,)).fetchone()
        kernel_id, created = _kernel_id(handle, session_id, record["content"], record["valid_until"])
        stored = read_version(handle.db_path, session_id, kernel_id)
        if stored != record["content"]:
            raise ApiError(409, "CONFLICT", "the version session already holds different content")
        if not has_embedding(handle.db_path, kernel_id):
            if created:
                discard_version(handle.db_path, session_id, kernel_id)
            _save_operation(
                connection,
                request_id,
                existing,
                digest,
                record,
                memory_id,
                revision,
                None,
                "failed",
                "MODEL_UNAVAILABLE",
            )
            raise ApiError(503, "MODEL_UNAVAILABLE", "local embedding was not stored", retryable=True)
        _save_operation(
            connection, request_id, existing, digest, record, memory_id, revision, kernel_id, "prepared", None
        )
        _crash("kernel")
        _commit_ref(
            connection,
            request_id,
            memory_id,
            revision,
            kernel_id,
            session_id,
            record,
            finalize,
        )
        result = {
            "memory_id": memory_id,
            "revision": revision,
            "kernel_id": kernel_id,
            "operation_id": request_id,
            "kind": record["kind"],
            "category": record["category"],
            "scope": record["scope"],
            "valid_until": record["valid_until"],
            "share_enabled": record["share_enabled"],
            "status": "accepted",
        }
        _crash("commit")
        return result
    finally:
        connection.close()


def _identity(connection: sqlite3.Connection, existing, record: dict) -> tuple[str, int]:
    if record["target_id"]:
        memory_id = record["target_id"]
        revision = record["base_revision"] + 1
        if existing is not None and existing["target_id"] not in {None, memory_id}:
            raise ApiError(409, "CONFLICT", "this request id was already used with a different payload")
        written = existing is not None and existing["kernel_id"]
        if not written:
            active = _active(connection, memory_id)
            if active is None:
                raise ApiError(404, "NOT_FOUND", "memory not found")
            if active["revision"] != record["base_revision"]:
                raise ApiError(409, "CONFLICT", VERSION_CONFLICT)
        return memory_id, revision
    if existing is not None and existing["target_id"]:
        return existing["target_id"], int(existing["revision"] or 1)
    return str(uuid.uuid4()), 1


def _active(connection: sqlite3.Connection, memory_id: str):
    return connection.execute(
        """
        SELECT revision, kernel_id FROM memory_refs
        WHERE memory_id = ? AND lifecycle = 'active'
        """,
        (memory_id,),
    ).fetchone()


def _kernel_id(handle: KernelHandle, session_id: str, content: str, valid_until: str | None) -> tuple[str, bool]:
    rows = session_rows(handle.db_path, session_id)
    if len(rows) > 1:
        raise ApiError(503, "KERNEL_UNAVAILABLE", "the version session has more than one kernel row", retryable=False)
    if len(rows) == 1:
        if rows[0].content != content:
            raise ApiError(409, "CONFLICT", "the version session already holds different content")
        return rows[0].kernel_id, False
    try:
        return write_version(handle.db_path, session_id, content, valid_until), True
    except ApiError:
        raise
    except Exception as exc:
        rows = session_rows(handle.db_path, session_id)
        if len(rows) == 1 and rows[0].content == content:
            return rows[0].kernel_id, False
        raise ApiError(503, "KERNEL_UNAVAILABLE", "kernel write failed", retryable=True) from exc


def _commit_ref(
    connection: sqlite3.Connection,
    request_id: str,
    memory_id: str,
    revision: int,
    kernel_id: str,
    session_id: str,
    record: dict,
    finalize,
) -> None:
    now = _now()
    try:
        connection.execute("BEGIN IMMEDIATE")
        if record["target_id"]:
            updated = connection.execute(
                """
                UPDATE memory_refs
                SET lifecycle = 'superseded'
                WHERE memory_id = ? AND revision = ? AND lifecycle = 'active'
                """,
                (memory_id, record["base_revision"]),
            )
            if updated.rowcount != 1:
                connection.rollback()
                raise ApiError(409, "CONFLICT", VERSION_CONFLICT)
        else:
            active = connection.execute(
                """
                SELECT COUNT(*) FROM memory_refs
                WHERE memory_id = ? AND lifecycle = 'active'
                """,
                (memory_id,),
            ).fetchone()[0]
            if active:
                connection.rollback()
                raise ApiError(409, "CONFLICT", "this memory already has an active version")
        connection.execute(
            """
            INSERT INTO memory_refs (
                memory_id, revision, kernel_id, kernel_session, kind, category, scope,
                lifecycle, share_enabled, valid_until, source_refs, approved_evidence,
                operation_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)
            """,
            (
                memory_id,
                revision,
                kernel_id,
                session_id,
                record["kind"],
                record["category"],
                record["scope"],
                1 if record["share_enabled"] else 0,
                record["valid_until"],
                json.dumps(record["source_refs"], ensure_ascii=False),
                record["approved_evidence"],
                request_id,
                now,
            ),
        )
        connection.execute(
            """
            UPDATE operations
            SET status = 'completed', kernel_id = ?, error_code = NULL, updated_at = ?
            WHERE id = ?
            """,
            (kernel_id, now, request_id),
        )
        if finalize is not None:
            finalize(connection)
        connection.commit()
    except ApiError:
        if connection.in_transaction:
            connection.rollback()
        raise
    except sqlite3.IntegrityError as exc:
        if connection.in_transaction:
            connection.rollback()
        raise ApiError(409, "CONFLICT", "the control record rejected a second active version") from exc


def _completed(connection: sqlite3.Connection, handle: KernelHandle, operation) -> dict:
    refs = connection.execute(
        "SELECT * FROM memory_refs WHERE operation_id = ?",
        (operation["id"],),
    ).fetchall()
    if len(refs) != 1 or not refs[0]["kernel_id"]:
        raise ApiError(503, "KERNEL_UNAVAILABLE", "the completed operation has no active memory", retryable=True)
    ref = refs[0]
    stored = read_version(handle.db_path, ref["kernel_session"], ref["kernel_id"])
    if stored is None:
        raise ApiError(503, "KERNEL_UNAVAILABLE", "the published kernel row is missing", retryable=True)
    return {
        "memory_id": ref["memory_id"],
        "revision": ref["revision"],
        "kernel_id": ref["kernel_id"],
        "operation_id": operation["id"],
        "kind": ref["kind"],
        "category": ref["category"],
        "scope": ref["scope"],
        "valid_until": ref["valid_until"],
        "share_enabled": bool(ref["share_enabled"]),
        "status": "accepted",
    }


def _save_operation(
    connection: sqlite3.Connection,
    request_id: str,
    existing,
    digest: str,
    record: dict,
    memory_id: str | None,
    revision: int | None,
    kernel_id: str | None,
    status: str,
    error_code: str | None,
) -> None:
    now = _now()
    payload_json = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    action = "update" if record["target_id"] else "publish"
    if existing is None:
        connection.execute(
            """
            INSERT INTO operations (
                id, action, target_id, base_revision, payload_hash, payload_json, revision,
                kernel_id, status, error_code, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                action,
                memory_id,
                record["base_revision"],
                digest,
                payload_json,
                revision,
                kernel_id,
                status,
                error_code,
                now,
                now,
            ),
        )
    else:
        connection.execute(
            """
            UPDATE operations
            SET target_id = COALESCE(?, target_id),
                base_revision = COALESCE(?, base_revision),
                revision = COALESCE(?, revision),
                payload_json = COALESCE(?, payload_json),
                kernel_id = COALESCE(?, kernel_id),
                status = ?,
                error_code = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                memory_id,
                record["base_revision"],
                revision,
                payload_json,
                kernel_id,
                status,
                error_code,
                now,
                request_id,
            ),
        )
    connection.commit()


def _record(payload: dict) -> dict:
    content = _content(payload.get("content"))
    kind = payload.get("kind")
    category = payload.get("category")
    if kind not in _KINDS or category not in _CATEGORIES:
        raise ApiError(400, "VALIDATION_ERROR", "kind or category is not supported")
    scope = payload.get("scope")
    if scope is not None:
        if not isinstance(scope, str) or not scope.strip():
            raise ApiError(400, "VALIDATION_ERROR", "scope must be a non-empty string")
        scope = scope.strip()
    valid_until = payload.get("valid_until")
    if valid_until is not None and not isinstance(valid_until, str):
        raise ApiError(400, "VALIDATION_ERROR", "valid_until is invalid")
    share_enabled = payload.get("share_enabled", True)
    if not isinstance(share_enabled, bool):
        raise ApiError(400, "VALIDATION_ERROR", "share_enabled must be a boolean")
    source_refs = payload.get("source_refs") or []
    if not isinstance(source_refs, list) or any(not isinstance(item, str) for item in source_refs):
        raise ApiError(400, "VALIDATION_ERROR", "source_refs must be a list of strings")
    target_id = payload.get("target_id")
    base_revision = payload.get("base_revision")
    if target_id is not None:
        if not isinstance(target_id, str) or not target_id.strip():
            raise ApiError(400, "VALIDATION_ERROR", "target_id is invalid")
        target_id = target_id.strip()
        if not isinstance(base_revision, int) or isinstance(base_revision, bool) or base_revision < 1:
            raise ApiError(400, "VALIDATION_ERROR", "base_revision is required")
    elif base_revision is not None:
        raise ApiError(400, "VALIDATION_ERROR", "base_revision requires target_id")
    evidence = payload.get("approved_evidence")
    if evidence is not None and not isinstance(evidence, str):
        raise ApiError(400, "VALIDATION_ERROR", "approved_evidence is invalid")
    return {
        "approved_evidence": evidence,
        "base_revision": base_revision,
        "category": category,
        "content": content,
        "kind": kind,
        "scope": scope,
        "share_enabled": share_enabled,
        "source_refs": list(source_refs),
        "target_id": target_id,
        "valid_until": valid_until,
    }


def _content(value) -> str:
    if not isinstance(value, str):
        raise ApiError(400, "VALIDATION_ERROR", "content is required")
    text = value.strip()
    if not text:
        raise ApiError(400, "VALIDATION_ERROR", "content is required")
    if len(text) > _MAX_CONTENT:
        raise ApiError(400, "VALIDATION_ERROR", "a memory can contain at most 2000 characters")
    return text


def _digest(record: dict) -> str:
    raw = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def crash_injection_armed(point: str) -> bool:
    """Process-exit faults are only for an explicit test mode in a temp directory."""
    if os.environ.get("ZHIWO_CRASH_AFTER") != point:
        return False
    if os.environ.get("ZHIWO_TEST_MODE") != "1":
        return False
    raw = os.environ.get("ZHIWO_DATA_DIR", "").strip()
    if not raw:
        return False
    try:
        data_dir = Path(raw).expanduser().resolve()
        data_dir.relative_to(Path(tempfile.gettempdir()).resolve())
    except (OSError, ValueError):
        return False
    repo = Path(__file__).resolve().parents[3]
    try:
        data_dir.relative_to(repo.resolve())
    except ValueError:
        return True
    return False


def _crash(point: str) -> None:
    if crash_injection_armed(point):
        os._exit(86)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
