"""The four agent tools. They share one visibility rule.

Recall runs before the commit lock. Inside the lock the tool re-checks
the connection and the current rows, writes one access snapshot of the
payload it will return, commits that snapshot, and only then returns the
same object. Nothing here publishes a memory.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone

from zhiwo.adapters.kernel_client import recall_rows
from zhiwo.api.errors import ApiError
from zhiwo.contracts.memory import CATEGORIES, TOOLS
from zhiwo.services.access import bind_delivery, record_prepared
from zhiwo.services.agents import fetch_principal
from zhiwo.services.commit_gate import commit_lock
from zhiwo.services.organize import open_session
from zhiwo.services.policy import RECALL_WINDOW, memory_visible, select_visible
from zhiwo.services.sharing import paused_error, paused_until

_UNVERIFIED = "Agent 提供，未核实"


def get_context(
    db_path,
    kernel,
    credential: str,
    task: str,
    *,
    max_items: int = 5,
    request_id: str | None = None,
    before_commit=None,
    recall=None,
    record=None,
) -> dict:
    text = _text(task, "task")
    limit = _bound(max_items, 1, 10, "max_items")
    return _read(
        db_path,
        kernel,
        credential,
        "get_context",
        text,
        limit=limit,
        request_id=_optional_request_id(request_id),
        before_commit=before_commit,
        recall=recall,
        record=record,
    )


def search_memory(
    db_path,
    kernel,
    credential: str,
    query: str,
    *,
    categories: list[str] | None = None,
    limit: int = 10,
    request_id: str | None = None,
    before_commit=None,
    recall=None,
    record=None,
) -> dict:
    text = _text(query, "query")
    bounded = _bound(limit, 1, 20, "limit")
    requested = _category_filter(categories)
    return _read(
        db_path,
        kernel,
        credential,
        "search_memory",
        text,
        limit=bounded,
        categories=requested,
        request_id=_optional_request_id(request_id),
        before_commit=before_commit,
        recall=recall,
        record=record,
    )


def propose_memory(
    db_path,
    _kernel,
    credential: str,
    request_id: str | None,
    change: dict,
    evidence: dict,
    *,
    before_commit=None,
    record=None,
) -> dict:
    """Store one pending proposal. The target of an update must be visible.

    The category must be one the connection may propose (propose_categories),
    which is separate from what it may read. Without a request id the key is
    derived from the proposal itself, so a retried or repeated suggestion
    returns the first proposal instead of adding another one.
    """
    del _kernel
    proposal = _change(change)
    fragment, source_ref = _evidence_input(evidence)
    request_key = _derived_request_id(proposal, fragment, source_ref) if request_id is None else _required_request_id(request_id)
    turn = _authorize(db_path, credential, "propose_memory", request_key, record)
    if proposal["category"] not in turn.propose_categories:
        _audit_and_raise(
            db_path,
            turn,
            "propose_memory",
            request_key,
            ApiError(403, "FORBIDDEN", _category_denied(turn.propose_categories)),
            record,
        )
    if before_commit is not None:
        before_commit()
    with commit_lock:
        connection = _connect(db_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            principal = _recheck(connection, credential, "propose_memory", turn.policy_version)
            if proposal["category"] not in principal.propose_categories:
                _deny(
                    connection,
                    principal,
                    "propose_memory",
                    request_key,
                    ApiError(403, "FORBIDDEN", _category_denied(principal.propose_categories)),
                    record,
                )
            existing = connection.execute(
                """
                SELECT * FROM proposals
                WHERE agent_id = ? AND client_request_id = ?
                """,
                (principal.agent_id, request_key),
            ).fetchone()
            if existing is not None:
                if not _same_proposal(existing, proposal, fragment, source_ref):
                    _deny(
                        connection,
                        principal,
                        "propose_memory",
                        request_key,
                        ApiError(409, "CONFLICT", "this request id was already used with a different payload"),
                        record,
                    )
                payload = {
                    "request_id": request_key,
                    "proposal_id": existing["id"],
                    "status": existing["status"],
                }
                return _finish(connection, principal, "propose_memory", request_key, payload, "success", record)
            now = _now()
            if proposal["change_type"] == "update":
                _require_visible_target(
                    connection,
                    principal,
                    proposal["target_id"],
                    proposal["base_revision"],
                    now,
                )
            verified = _source_matches(connection, principal, fragment, source_ref, now)
            source_id, job_id = _store_claim(connection, fragment, now)
            stored_payload = dict(proposal["payload"])
            batch_id = open_session(connection, principal.agent_id) if proposal["change_type"] == "add" else None
            if batch_id:
                stored_payload["batch_id"] = batch_id
            proposal_id = str(uuid.uuid4())
            connection.execute(
                """
                INSERT INTO proposals (
                    id, origin, change_type, target_id, base_revision, payload_json,
                    evidence_json, status, source_id, job_id, created_at, agent_id, client_request_id
                ) VALUES (?, 'agent', ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)
                """,
                (
                    proposal_id,
                    proposal["change_type"],
                    proposal["target_id"],
                    proposal["base_revision"],
                    _dump(stored_payload),
                    _dump(_evidence_record(fragment, source_ref, verified)),
                    source_id,
                    job_id,
                    now,
                    principal.agent_id,
                    request_key,
                ),
            )
            payload = {"request_id": request_key, "proposal_id": proposal_id, "status": "pending"}
            return _finish(connection, principal, "propose_memory", request_key, payload, "success", record)
        except ApiError as exc:
            _fail(connection, "propose_memory", request_key, exc, record, turn)
        finally:
            connection.close()


def explain_memory(
    db_path,
    _kernel,
    credential: str,
    memory_id: str,
    *,
    request_id: str | None = None,
    before_commit=None,
    record=None,
) -> dict:
    """Return the approved fragment of the current visible version.

    A hidden memory and a missing id produce the same not-found response.
    """
    del _kernel
    if not isinstance(memory_id, str) or not memory_id.strip():
        raise ApiError(400, "VALIDATION_ERROR", "id is required")
    memory_key = memory_id.strip()
    request_key = _optional_request_id(request_id)
    turn = _authorize(db_path, credential, "explain_memory", request_key, record)
    if before_commit is not None:
        before_commit()
    with commit_lock:
        connection = _connect(db_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            principal = _recheck(connection, credential, "explain_memory", turn.policy_version)
            row = connection.execute(
                """
                SELECT memory_id, revision, category, share_enabled, valid_until, lifecycle,
                       source_refs, approved_evidence, created_at
                FROM memory_refs
                WHERE memory_id = ? AND lifecycle = 'active'
                """,
                (memory_key,),
            ).fetchone()
            if row is None or not memory_visible(principal, row, _now_dt()):
                _deny(
                    connection,
                    principal,
                    "explain_memory",
                    request_key,
                    ApiError(404, "NOT_FOUND", "memory not found"),
                    record,
                )
            result = {
                "id": row["memory_id"],
                "revision": int(row["revision"]),
                "source_kind": _source_kind(connection, row["source_refs"]),
                "confirmed_at": row["created_at"],
                "evidence": _fragment(row["approved_evidence"]),
            }
            payload = {"request_id": request_key, "result": result}
            return _finish(connection, principal, "explain_memory", request_key, payload, "success", record)
        except ApiError as exc:
            _fail(connection, "explain_memory", request_key, exc, record, turn)
        finally:
            connection.close()


def _read(
    db_path,
    kernel,
    credential: str,
    tool: str,
    text: str,
    *,
    limit: int,
    request_id: str,
    before_commit,
    recall,
    record,
    categories: set[str] | None = None,
) -> dict:
    turn = _authorize(db_path, credential, tool, request_id, record)
    allowed = set(turn.allowed_categories)
    if categories is not None:
        unknown = categories - set(CATEGORIES)
        if unknown:
            raise ApiError(400, "VALIDATION_ERROR", "category is not supported")
        allowed &= categories
    recalled = []
    if allowed:
        recall_fn = recall or (lambda query, top_k: recall_rows(kernel, query, top_k))
        recalled = list(recall_fn(text, RECALL_WINDOW) or [])
    if before_commit is not None:
        before_commit()
    with commit_lock:
        connection = _connect(db_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            principal = _recheck(connection, credential, tool, turn.policy_version)
            if categories is not None:
                principal = _narrow(principal, categories)
            if not principal.allowed_categories:
                selected = {"items": [], "truncated": False}
            else:
                published = _active_map(connection)
                selected = select_visible(recalled, published, principal, limit=limit, now=_now_dt())
            payload = {"request_id": request_id, "items": selected["items"], "truncated": selected["truncated"]}
            outcome = "success" if selected["items"] else "empty"
            return _finish(connection, principal, tool, request_id, payload, outcome, record)
        except ApiError as exc:
            _fail(connection, tool, request_id, exc, record, turn)
        finally:
            connection.close()


def _authorize(db_path, credential: str, tool: str, request_id: str, record):
    try:
        return _begin(db_path, credential, tool)
    except ApiError as exc:
        _audit_and_raise(db_path, getattr(exc, "principal", None), tool, request_id, exc, record)


def _begin(db_path, credential: str, tool: str):
    if tool not in TOOLS:
        raise ApiError(400, "VALIDATION_ERROR", "tool is not available")
    if not isinstance(credential, str) or not credential:
        raise ApiError(401, "UNAUTHENTICATED", "agent credential rejected")
    connection = _connect(db_path)
    try:
        principal = fetch_principal(connection, credential)
        paused = principal is not None and paused_until(connection) is not None
    finally:
        connection.close()
    if principal is None:
        raise _mark(ApiError(401, "UNAUTHENTICATED", "agent credential rejected"), None)
    if not principal.enabled:
        raise _mark(ApiError(403, "FORBIDDEN", "agent is disabled"), principal)
    if paused:
        raise _mark(paused_error(), principal)
    if tool not in principal.allowed_tools:
        raise _mark(ApiError(403, "FORBIDDEN", "tool is not allowed"), principal)
    return principal


def _recheck(connection, credential: str, tool: str, policy_version: int):
    principal = fetch_principal(connection, credential)
    if principal is None:
        raise _mark(ApiError(401, "UNAUTHENTICATED", "agent credential rejected"), None)
    if not principal.enabled:
        raise _mark(ApiError(403, "FORBIDDEN", "agent is disabled"), principal)
    if paused_until(connection) is not None:
        raise _mark(paused_error(), principal)
    if principal.policy_version != policy_version:
        raise _mark(ApiError(403, "FORBIDDEN", "connection permissions changed"), principal)
    if tool not in principal.allowed_tools:
        raise _mark(ApiError(403, "FORBIDDEN", "tool is not allowed"), principal)
    return principal


def _mark(exc: ApiError, principal) -> ApiError:
    exc.principal = principal
    return exc


def _error_payload(exc: ApiError) -> dict:
    return {"error": {"code": exc.code, "message": exc.message, "retryable": exc.retryable}}


def _finish(connection, principal, tool: str, request_id: str, payload: dict, outcome: str, record) -> dict:
    """Write the snapshot, commit it, and return that same payload.

    A failed insert or a failed commit is AUDIT_UNAVAILABLE. The payload is
    not returned in that case, and the event id is not published.
    """
    spec = {
        "request_id": request_id,
        "agent_id": None if principal is None else principal.agent_id,
        "tool": tool,
        "outcome": outcome,
        "policy_version": None if principal is None else principal.policy_version,
        "payload": payload,
    }
    try:
        event_id = record_prepared(connection, spec) if record is None else record(connection, spec)
        if not isinstance(event_id, str) or not event_id:
            raise ApiError(503, "AUDIT_UNAVAILABLE", "the access record could not be saved", retryable=True)
        connection.commit()
    except ApiError:
        _abort(connection)
        raise
    except sqlite3.Error as exc:
        _abort(connection)
        raise ApiError(503, "AUDIT_UNAVAILABLE", "the access record could not be saved", retryable=True) from exc
    except Exception as exc:
        _abort(connection)
        raise ApiError(503, "AUDIT_UNAVAILABLE", "the access record could not be saved", retryable=True) from exc
    bind_delivery(event_id)
    return payload


def _abort(connection) -> None:
    try:
        if connection.in_transaction:
            connection.rollback()
    except sqlite3.Error:
        return


def _deny(connection, principal, tool: str, request_id: str, exc: ApiError, record) -> None:
    exc.principal = principal
    _finish(connection, principal, tool, request_id, _error_payload(exc), "rejected", record)
    exc.request_id = request_id
    raise exc


def _fail(connection, tool: str, request_id: str, exc: ApiError, record, fallback) -> None:
    if connection.in_transaction and exc.code != "AUDIT_UNAVAILABLE":
        principal = getattr(exc, "principal", None)
        if principal is None:
            principal = fallback
        try:
            _finish(connection, principal, tool, request_id, _error_payload(exc), "rejected", record)
        except ApiError:
            if connection.in_transaction:
                connection.rollback()
            raise
        exc.request_id = request_id
    elif connection.in_transaction:
        connection.rollback()
    raise exc


def _audit_and_raise(db_path, principal, tool: str, request_id: str, exc: ApiError, record) -> None:
    with commit_lock:
        connection = _connect(db_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            _finish(connection, principal, tool, request_id, _error_payload(exc), "rejected", record)
        except ApiError:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()
    exc.request_id = request_id
    raise exc


def _narrow(principal, categories: set[str]):
    allowed = tuple(category for category in principal.allowed_categories if category in categories)
    return principal.__class__(
        agent_id=principal.agent_id,
        name=principal.name,
        enabled=principal.enabled,
        policy_version=principal.policy_version,
        allowed_tools=principal.allowed_tools,
        allowed_categories=allowed,
        propose_categories=principal.propose_categories,
    )


def _active_map(connection) -> dict:
    rows = connection.execute(
        """
        SELECT memory_id, revision, kernel_id, kind, category, scope, lifecycle,
               share_enabled, valid_until
        FROM memory_refs
        WHERE lifecycle = 'active' AND kernel_id IS NOT NULL AND kernel_id != ''
        """
    ).fetchall()
    return {row["kernel_id"]: row for row in rows}


def _require_visible_target(connection, principal, memory_id: str, base_revision: int, now: datetime) -> None:
    row = connection.execute(
        """
        SELECT revision, category, share_enabled, valid_until, lifecycle
        FROM memory_refs
        WHERE memory_id = ? AND lifecycle = 'active'
        """,
        (memory_id,),
    ).fetchone()
    if row is None or not memory_visible(principal, row, now):
        raise ApiError(404, "NOT_FOUND", "memory not found")
    if int(row["revision"]) != base_revision:
        raise ApiError(409, "CONFLICT", "the memory changed since this proposal was prepared")


def _source_matches(connection, principal, fragment: str, source_ref: str | None, now: datetime) -> bool:
    if not source_ref:
        return False
    source = connection.execute("SELECT content FROM sources WHERE id = ?", (source_ref,)).fetchone()
    if source is None or fragment not in (source["content"] or ""):
        return False
    rows = connection.execute(
        """
        SELECT category, share_enabled, valid_until, lifecycle, source_refs
        FROM memory_refs
        WHERE lifecycle = 'active'
        """
    ).fetchall()
    for row in rows:
        if source_ref not in _source_ids(row["source_refs"]):
            continue
        if memory_visible(principal, row, now):
            return True
    return False


def _store_claim(connection, fragment: str, now: str) -> tuple[str, str]:
    source_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    digest = hashlib.sha256(fragment.encode("utf-8")).hexdigest()
    connection.execute(
        """
        INSERT INTO sources (id, kind, name, content, content_hash, imported_at)
        VALUES (?, 'agent_claim', NULL, ?, ?, ?)
        """,
        (source_id, fragment, digest, now),
    )
    connection.execute(
        """
        INSERT INTO import_jobs (id, source_id, status, extractor_config, error_code, created_at)
        VALUES (?, ?, 'extracted', ?, NULL, ?)
        """,
        (job_id, source_id, _dump({"origin": "agent"}), now),
    )
    return source_id, job_id


_PROPOSAL_KEYS = uuid.UUID("6f1d6f0e-2b8a-4a57-9a0e-6d3c1b7e5a10")


def _derived_request_id(proposal: dict, fragment: str, source_ref: str | None) -> str:
    """Stable key for one suggestion. Same change and evidence, same key."""
    key = _dump(
        {
            "change_type": proposal["change_type"],
            "target_id": proposal["target_id"],
            "base_revision": proposal["base_revision"],
            "payload": proposal["payload"],
            "evidence": fragment,
            "source_ref": source_ref,
        }
    )
    return str(uuid.uuid5(_PROPOSAL_KEYS, key))


def _category_denied(allowed) -> str:
    if not allowed:
        return "this connection may not propose memories in any category"
    return "category is not allowed; this connection may propose: " + ", ".join(allowed)


def _same_proposal(existing, proposal: dict, fragment: str, source_ref: str | None) -> bool:
    if existing["origin"] != "agent" or existing["change_type"] != proposal["change_type"]:
        return False
    if (existing["target_id"] or None) != proposal["target_id"]:
        return False
    if existing["base_revision"] != proposal["base_revision"]:
        return False
    try:
        stored = json.loads(existing["payload_json"])
        evidence = json.loads(existing["evidence_json"])
    except json.JSONDecodeError:
        return False
    stored.pop("batch_id", None)
    return stored == proposal["payload"] and evidence.get("text") == fragment and evidence.get("source_ref") == source_ref


def _change(change: dict) -> dict:
    if not isinstance(change, dict):
        raise ApiError(400, "VALIDATION_ERROR", "change is invalid")
    allowed = {"type", "content", "kind", "category", "scope", "target_id", "base_revision"}
    if set(change) - allowed:
        raise ApiError(400, "VALIDATION_ERROR", "change contains an unsupported field")
    change_type = change.get("type")
    if change_type not in {"add", "update"}:
        raise ApiError(400, "VALIDATION_ERROR", "change.type must be add or update")
    content = _text(change.get("content"), "content")
    kind = change.get("kind")
    category = change.get("category")
    if kind not in {"fact", "event"}:
        raise ApiError(400, "VALIDATION_ERROR", "kind must be fact or event")
    if category not in CATEGORIES:
        raise ApiError(400, "VALIDATION_ERROR", "category must be one of: " + ", ".join(CATEGORIES))
    scope = change.get("scope")
    if scope is not None:
        scope = _text(scope, "scope")
    target_id = change.get("target_id")
    base_revision = change.get("base_revision")
    if change_type == "update":
        if not isinstance(target_id, str) or not target_id.strip():
            raise ApiError(400, "VALIDATION_ERROR", "target_id is required")
        if isinstance(base_revision, bool) or not isinstance(base_revision, int) or base_revision < 1:
            raise ApiError(400, "VALIDATION_ERROR", "base_revision is required")
        target_id = target_id.strip()
    elif target_id is not None or base_revision is not None:
        raise ApiError(400, "VALIDATION_ERROR", "an added memory does not take a target")
    else:
        target_id = None
        base_revision = None
    return {
        "change_type": change_type,
        "target_id": target_id,
        "base_revision": base_revision,
        "category": category,
        "payload": {
            "category": category,
            "content": content,
            "kind": kind,
            "scope": scope,
        },
    }


def _evidence_input(evidence: dict) -> tuple[str, str | None]:
    if not isinstance(evidence, dict):
        raise ApiError(400, "VALIDATION_ERROR", "evidence is invalid")
    if set(evidence) - {"text", "source_ref"}:
        raise ApiError(400, "VALIDATION_ERROR", "evidence contains an unsupported field")
    fragment = _text(evidence.get("text"), "evidence")
    source_ref = evidence.get("source_ref")
    if source_ref is not None:
        if not isinstance(source_ref, str) or not source_ref.strip():
            raise ApiError(400, "VALIDATION_ERROR", "source_ref is invalid")
        source_ref = source_ref.strip()
    return fragment, source_ref


def _evidence_record(fragment: str, source_ref: str | None, verified: bool) -> dict:
    record = {"text": fragment, "source_ref": source_ref, "verification": "matched" if verified else "unverified"}
    if not verified:
        record["note"] = _UNVERIFIED
    return record


def _fragment(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(loaded, dict):
        return None
    text = loaded.get("text")
    if not isinstance(text, str):
        return None
    fragment = text.strip()
    if not fragment or len(fragment) > 2000:
        return None
    return fragment


def _source_kind(connection, raw) -> str | None:
    ids = _source_ids(raw)
    if not ids:
        return None
    row = connection.execute("SELECT kind FROM sources WHERE id = ?", (ids[0],)).fetchone()
    if row is None:
        return None
    return row["kind"]


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


def _category_filter(categories) -> set[str] | None:
    if categories is None:
        return None
    if not isinstance(categories, list) or any(not isinstance(item, str) for item in categories):
        raise ApiError(400, "VALIDATION_ERROR", "categories are invalid")
    if len(categories) != len(set(categories)):
        raise ApiError(400, "VALIDATION_ERROR", "duplicate category")
    return set(categories)


def _text(value, label: str) -> str:
    if not isinstance(value, str):
        raise ApiError(400, "VALIDATION_ERROR", f"{label} is required")
    text = value.strip()
    if not text or len(text) > 2000:
        raise ApiError(400, "VALIDATION_ERROR", f"{label} must contain 1 to 2000 characters")
    return text


def _bound(value, low: int, high: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < low or value > high:
        raise ApiError(400, "VALIDATION_ERROR", f"{label} must be an integer from {low} to {high}")
    return value


def _optional_request_id(value: str | None) -> str:
    if value is None:
        return str(uuid.uuid4())
    return _required_request_id(value)


def _required_request_id(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ApiError(400, "VALIDATION_ERROR", "request id must be a UUID") from exc


def _dump(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _connect(db_path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _now() -> str:
    return _now_dt().isoformat()


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)
