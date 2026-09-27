"""Access snapshots for tool responses.

The snapshot is the business payload that was just checked. Each call gets
its own event id. Delivery states say whether that payload was prepared,
handed to the send channel, failed to send, or could not be classified.
None of them means a model read or used it.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextvars import ContextVar
from datetime import datetime, time, timedelta, timezone

from zhiwo.api.errors import ApiError
from zhiwo.services.commit_gate import commit_lock

_OUTCOMES = {"success", "empty", "rejected"}
_CHANNEL = {"sent", "failed", "unknown"}
_TRANSITIONS = {"prepared": frozenset({"sent", "failed", "unknown"})}


class DeliveryBox:
    """Mutable holder shared with the request that will hand off the response.

    A copied context still points at this object, so a tool running on a
    worker thread can publish its event id to the sender.
    """

    def __init__(self) -> None:
        self.event_id: str | None = None


_box: ContextVar[DeliveryBox | None] = ContextVar("zhiwo_delivery_box", default=None)


def open_delivery() -> tuple[DeliveryBox, object]:
    box = DeliveryBox()
    return box, _box.set(box)


def close_delivery(token) -> None:
    _box.reset(token)


def bind_delivery(event_id: str) -> None:
    box = _box.get()
    if box is not None and isinstance(event_id, str) and event_id:
        box.event_id = event_id


def dump_payload(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def record_prepared(connection, spec: dict) -> str:
    """Insert the snapshot inside the caller's open transaction.

    Returns the new event id. The caller commits after this returns. A write
    failure becomes AUDIT_UNAVAILABLE and the payload is not returned.
    """
    outcome = spec.get("outcome")
    payload = spec.get("payload")
    if outcome not in _OUTCOMES or not isinstance(payload, dict):
        raise ApiError(503, "AUDIT_UNAVAILABLE", "the access record could not be saved", retryable=True)
    event_id = str(uuid.uuid4())
    try:
        connection.execute(
            """
            INSERT INTO access_events (
                id, request_id, agent_id, tool, outcome, policy_version,
                response_snapshot, created_at, delivery_state
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'prepared')
            """,
            (
                event_id,
                spec.get("request_id"),
                spec.get("agent_id"),
                spec.get("tool"),
                outcome,
                spec.get("policy_version"),
                dump_payload(payload),
                _now(),
            ),
        )
    except sqlite3.Error as exc:
        raise ApiError(503, "AUDIT_UNAVAILABLE", "the access record could not be saved", retryable=True) from exc
    return event_id


def note_delivery(db_path, event_id: str, state: str) -> None:
    """Move one prepared snapshot to sent, failed, or unknown.

    The id is the event from `record_prepared`, not the caller's request id.
    Only `prepared` may change, and only to one of those three states.
    `sent` means the payload was handed to the send channel. It does not
    mean the other side received it or that a model used it.
    """
    if state not in _CHANNEL:
        raise ApiError(400, "VALIDATION_ERROR", "delivery state is not supported")
    if not isinstance(event_id, str) or not event_id:
        raise ApiError(404, "NOT_FOUND", "access event not found")
    with commit_lock:
        connection = _connect(db_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT delivery_state FROM access_events WHERE id = ?",
                (event_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise ApiError(404, "NOT_FOUND", "access event not found")
            current = row["delivery_state"]
            if state not in _TRANSITIONS.get(current, frozenset()):
                connection.rollback()
                raise ApiError(409, "CONFLICT", "delivery state cannot change")
            updated = connection.execute(
                """
                UPDATE access_events
                SET delivery_state = ?
                WHERE id = ? AND delivery_state = ?
                """,
                (state, event_id, current),
            )
            if updated.rowcount != 1:
                connection.rollback()
                raise ApiError(409, "CONFLICT", "delivery state cannot change")
            connection.commit()
        finally:
            connection.close()


def list_access_events(db_path, agent_id: str | None = None) -> dict:
    connection = _connect(db_path)
    try:
        if agent_id:
            rows = connection.execute(
                """
                SELECT * FROM access_events
                WHERE agent_id = ?
                ORDER BY created_at DESC, id DESC
                """,
                (agent_id,),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT * FROM access_events ORDER BY created_at DESC, id DESC"
            ).fetchall()
    finally:
        connection.close()
    return {"events": [_summary(row) for row in rows]}


_READ_TOOLS = ("get_context", "search_memory", "explain_memory")


def _count_series(buckets: dict[str, dict[str, int]], names: dict[str, str], keys: list[str]) -> list[dict]:
    bases = {agent_id: names.get(agent_id) or "已移除的连接" for agent_id in buckets}
    repeated = {name for name in bases.values() if list(bases.values()).count(name) > 1}
    series = []
    for agent_id, counts in buckets.items():
        name = bases[agent_id]
        if name in repeated:
            name = f"{name} · {agent_id[:4]}"
        series.append(
            {
                "id": agent_id or "removed",
                "name": name,
                "counts": [counts.get(key, 0) for key in keys],
            }
        )
    series.sort(key=lambda item: (-sum(item["counts"]), item["name"], item["id"]))
    return series


def read_counts(db_path, days: int, utc_offset_minutes: int, *, now: datetime | None = None) -> dict:
    """Daily read counts for the owner chart, plus the last 24 local hours.

    A read is get_context, search_memory, or explain_memory. Proposals are
    not reads. The result is dates, hours, and counts only: no snapshot,
    sentence, or query. `utc_offset_minutes` matches `Date.getTimezoneOffset()`.
    `last_24h` is 24 clock hours ending at the current local hour.
    """
    if type(days) is not int or days not in (7, 14):
        raise ApiError(400, "VALIDATION_ERROR", "days must be 7 or 14")
    if type(utc_offset_minutes) is not int or not -14 * 60 <= utc_offset_minutes <= 14 * 60:
        raise ApiError(400, "VALIDATION_ERROR", "utc offset is not supported")
    now_utc = now or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    local_now = now_utc - timedelta(minutes=utc_offset_minutes)
    today = local_now.date()
    start = today - timedelta(days=days - 1)
    day_list = [(start + timedelta(days=index)).isoformat() for index in range(days)]
    day_keys = set(day_list)
    local_hour = local_now.replace(minute=0, second=0, microsecond=0, tzinfo=None)
    hour_start = local_hour - timedelta(hours=23)
    hour_list = [(hour_start + timedelta(hours=index)).strftime("%Y-%m-%dT%H:00") for index in range(24)]
    hour_keys = set(hour_list)
    start_utc = datetime.combine(start, time.min, tzinfo=timezone.utc) + timedelta(minutes=utc_offset_minutes)
    connection = _connect(db_path)
    try:
        rows = connection.execute(
            """
            SELECT agent_id, tool, created_at
            FROM access_events
            WHERE tool IN ('get_context', 'search_memory', 'explain_memory')
              AND created_at >= ?
            """,
            (start_utc.isoformat(),),
        ).fetchall()
        names = {
            row["id"]: row["name"]
            for row in connection.execute("SELECT id, name FROM agents")
        }
    finally:
        connection.close()
    day_buckets: dict[str, dict[str, int]] = {}
    hour_buckets: dict[str, dict[str, int]] = {}
    for row in rows:
        if row["tool"] not in _READ_TOOLS:
            continue
        try:
            created = datetime.fromisoformat(row["created_at"])
        except ValueError:
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        shifted = created - timedelta(minutes=utc_offset_minutes)
        agent_id = row["agent_id"] if isinstance(row["agent_id"], str) and row["agent_id"] else ""
        day = shifted.date().isoformat()
        if day in day_keys:
            day_buckets.setdefault(agent_id, {})[day] = day_buckets.get(agent_id, {}).get(day, 0) + 1
        hour_key = shifted.strftime("%Y-%m-%dT%H:00")
        if hour_key in hour_keys and created <= now_utc:
            hour_buckets.setdefault(agent_id, {})[hour_key] = hour_buckets.get(agent_id, {}).get(hour_key, 0) + 1
    return {
        "days": day_list,
        "series": _count_series(day_buckets, names, day_list),
        "last_24h": {"hours": hour_list, "series": _count_series(hour_buckets, names, hour_list)},
    }


def get_access_event(db_path, event_id: str) -> dict:
    connection = _connect(db_path)
    try:
        row = connection.execute("SELECT * FROM access_events WHERE id = ?", (event_id,)).fetchone()
    finally:
        connection.close()
    if row is None:
        raise ApiError(404, "NOT_FOUND", "access event not found")
    view = _summary(row)
    view["response"] = json.loads(row["response_snapshot"])
    return view


def _summary(row) -> dict:
    payload = json.loads(row["response_snapshot"])
    return {
        "id": row["id"],
        "request_id": row["request_id"],
        "agent_id": row["agent_id"],
        "tool": row["tool"],
        "outcome": row["outcome"],
        "policy_version": row["policy_version"],
        "created_at": row["created_at"],
        "delivery_state": row["delivery_state"],
        "versions": _versions(payload),
        "returned": _returned(payload),
    }


_RETURNED_LIMIT = 8
_RETURNED_CHARS = 160


def _returned(payload: dict) -> list[str]:
    """Sentences the caller actually received, clipped for the owner list.

    Memory text and an explain fragment count. An error message does not,
    so a rejection cannot look like a memory that was handed over.
    """
    lines: list[str] = []
    items = payload.get("items")
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                _push_line(lines, item.get("content"))
    result = payload.get("result")
    if isinstance(result, dict):
        _push_line(lines, result.get("evidence"))
    return lines[:_RETURNED_LIMIT]


def _push_line(lines: list[str], value: object) -> None:
    if not isinstance(value, str):
        return
    text = " ".join(value.split())
    if not text:
        return
    if len(text) > _RETURNED_CHARS:
        text = text[:_RETURNED_CHARS] + "…"
    lines.append(text)


def _versions(payload: dict) -> list[dict]:
    found = []
    items = payload.get("items")
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("id"), str) and isinstance(item.get("revision"), int):
                found.append({"id": item["id"], "revision": item["revision"]})
    result = payload.get("result")
    if isinstance(result, dict) and isinstance(result.get("id"), str) and isinstance(result.get("revision"), int):
        found.append({"id": result["id"], "revision": result["revision"]})
    return found


def _connect(db_path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
