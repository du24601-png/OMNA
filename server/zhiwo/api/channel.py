"""Hand a prepared tool response to the HTTP send channel.

The order under the commit lock is: re-check the rows that the snapshot
names, keep the snapshot equal to the bytes about to be written, write
those bytes, then record delivery. A revocation uses the same lock, so it
either finishes before this section or waits until the write returns.
"""

from __future__ import annotations

import json
import sqlite3
import anyio
from functools import partial
from datetime import datetime, timezone

from zhiwo.api.errors import ApiError
from zhiwo.services.access import dump_payload, note_delivery
from zhiwo.services.agents import fetch_principal_by_id, note_client_observed
from zhiwo.services.commit_gate import commit_lock
from zhiwo.services.policy import memory_visible

_INTERRUPTED = (ConnectionError, TimeoutError, BrokenPipeError)
_STATUS = {
    "UNAUTHENTICATED": 401,
    "FORBIDDEN": 403,
    "NOT_FOUND": 404,
    "CONFLICT": 409,
    "VALIDATION_ERROR": 400,
    "AUDIT_UNAVAILABLE": 503,
    "MODEL_UNAVAILABLE": 503,
    "KERNEL_UNAVAILABLE": 503,
}
_AUDIT = {
    "error": {
        "code": "AUDIT_UNAVAILABLE",
        "message": "the access record could not be saved",
        "retryable": True,
    }
}


async def handoff(db_path, event_id: str, send, *, before=None, transport: str = "") -> None:
    """Re-check, write the HTTP response, then note delivery for this event."""
    # RLock is thread-owned, not coroutine-owned. The event-loop thread must
    # never hold it across await: a second task on that thread can re-enter it.
    # One worker owns the complete critical section; only ASGI writes return
    # to the event loop. Cancellation is shielded until the worker releases it.
    await anyio.to_thread.run_sync(partial(_handoff_sync, db_path, event_id, send, before=before, transport=transport))


def _handoff_sync(db_path, event_id: str, send, *, before=None, transport: str = "") -> None:
    if before is not None:
        before()
    with commit_lock:
        status, payload, outcome, agent_id, stored = _align(db_path, event_id)
        try:
            anyio.from_thread.run(write_http, send, status, payload)
        except _INTERRUPTED:
            if stored:
                _safe_note(db_path, event_id, "failed")
            raise
        except BaseException:
            if stored:
                _safe_note(db_path, event_id, "unknown")
            raise
        else:
            if not stored:
                return
            try:
                note_delivery(db_path, event_id, "sent")
            except Exception:
                _safe_note(db_path, event_id, "unknown")
                return
            if transport == "stdio" and outcome in {"success", "empty"} and agent_id:
                try:
                    note_client_observed(db_path, agent_id)
                except Exception:
                    return


async def write_http(send, status: int, payload: dict) -> None:
    """Write one JSON response. This is the HTTP send channel."""
    body = dump_payload(payload).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _align(db_path, event_id: str) -> tuple[int, dict, str, str | None, bool]:
    connection = _connect(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM access_events WHERE id = ?", (event_id,)).fetchone()
        if row is None:
            connection.rollback()
            return 503, dict(_AUDIT), "rejected", None, False
        payload = _load(row["response_snapshot"])
        try:
            status, revised, outcome = _revise(connection, row, payload)
        except ApiError as exc:
            status, revised, outcome = exc.status, _error_body(payload, exc.code, exc.message), "rejected"
        encoded = dump_payload(revised)
        if encoded != row["response_snapshot"] or outcome != row["outcome"]:
            connection.execute(
                "UPDATE access_events SET response_snapshot = ?, outcome = ? WHERE id = ?",
                (encoded, outcome, event_id),
            )
        connection.commit()
        return status, revised, outcome, row["agent_id"], True
    finally:
        connection.close()


def _revise(connection, row, payload: dict) -> tuple[int, dict, str]:
    if row["outcome"] == "rejected":
        code = _error_code(payload)
        return _STATUS.get(code, 400), payload, "rejected"
    principal = None
    if row["agent_id"]:
        principal = fetch_principal_by_id(connection, row["agent_id"])
    if principal is None:
        return 401, _error_body(payload, "UNAUTHENTICATED", "agent credential rejected"), "rejected"
    if not principal.enabled:
        return 403, _error_body(payload, "FORBIDDEN", "agent is disabled"), "rejected"
    if row["policy_version"] is not None and principal.policy_version != int(row["policy_version"]):
        return 403, _error_body(payload, "FORBIDDEN", "connection permissions changed"), "rejected"
    if row["tool"] not in principal.allowed_tools:
        return 403, _error_body(payload, "FORBIDDEN", "tool is not allowed"), "rejected"
    now = datetime.now(timezone.utc)
    if isinstance(payload.get("items"), list):
        kept = [item for item in payload["items"] if _item_visible(connection, principal, item, now)]
        revised = dict(payload)
        revised["items"] = kept
        outcome = "success" if kept else "empty"
        return 200, revised, outcome
    result = payload.get("result")
    if isinstance(result, dict) and not _item_visible(connection, principal, result, now):
        return 404, _error_body(payload, "NOT_FOUND", "memory not found"), "rejected"
    return 200, payload, row["outcome"] if row["outcome"] in {"success", "empty"} else "success"


def _item_visible(connection, principal, item, now: datetime) -> bool:
    if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not isinstance(item.get("revision"), int):
        return False
    row = connection.execute(
        """
        SELECT memory_id, revision, category, scope, lifecycle, share_enabled, valid_until
        FROM memory_refs
        WHERE memory_id = ? AND lifecycle = 'active'
        """,
        (item["id"],),
    ).fetchone()
    if row is None or int(row["revision"]) != item["revision"]:
        return False
    return memory_visible(principal, row, now)


def _error_body(payload: dict, code: str, message: str) -> dict:
    body = {"error": {"code": code, "message": message, "retryable": code == "AUDIT_UNAVAILABLE"}}
    request_id = payload.get("request_id") if isinstance(payload, dict) else None
    if isinstance(request_id, str):
        return {"request_id": request_id, **body}
    return body


def _error_code(payload: dict) -> str:
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict) and isinstance(error.get("code"), str):
        return error["code"]
    return "VALIDATION_ERROR"


def _load(raw: str) -> dict:
    loaded = json.loads(raw)
    if not isinstance(loaded, dict):
        raise ApiError(503, "AUDIT_UNAVAILABLE", "the access record could not be saved", retryable=True)
    return loaded


def _connect(db_path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _safe_note(db_path, event_id: str, state: str) -> None:
    try:
        note_delivery(db_path, event_id, state)
    except Exception:
        return


class ChannelApp:
    """ASGI wrapper. Tool responses go through `handoff` instead of the raw return."""

    def __init__(self, app) -> None:
        self.app = app

    def __getattr__(self, name):
        return getattr(self.app, name)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        from zhiwo.services.access import close_delivery, open_delivery

        box, token = open_delivery()
        captured: list[dict] = []

        async def capture(message):
            captured.append(message)

        try:
            await self.app(scope, receive, capture)
            event_id = box.event_id
            if not isinstance(event_id, str) or not event_id:
                for message in captured:
                    await send(message)
                return
            settings = getattr(self.app.state, "settings", None)
            if settings is None:
                for message in captured:
                    await send(message)
                return
            before = getattr(self.app.state, "before_channel", None)
            await handoff(
                settings.control_db,
                event_id,
                send,
                before=before,
                transport=_transport(scope),
            )
        finally:
            close_delivery(token)


def _transport(scope) -> str:
    for key, value in scope.get("headers") or []:
        if key.lower() == b"x-zhiwo-transport":
            try:
                return value.decode("ascii")
            except UnicodeDecodeError:
                return ""
    return ""
