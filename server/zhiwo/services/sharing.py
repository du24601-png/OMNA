"""Pause sharing for one hour.

The end time is one row in `settings`. It is checked whenever a tool runs,
so it survives a restart and needs no timer: once the time has passed the
pause is simply over. Pausing and resuming hold the commit lock, like a
revocation, so a tool cannot pass its last check and then send after the
owner paused.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from zhiwo.api.errors import ApiError
from zhiwo.services.commit_gate import commit_lock

PAUSE_KEY = "sharing_paused_until"
PAUSE_MINUTES = 60
PAUSED_MESSAGE = "用户暂停了共享，现在不能读取或提议记忆。请如实告诉用户。"


def paused_until(connection, now: datetime | None = None) -> str | None:
    """The end of an active pause, or None when sharing is on."""
    row = connection.execute("SELECT value FROM settings WHERE key = ?", (PAUSE_KEY,)).fetchone()
    if row is None:
        return None
    try:
        until = datetime.fromisoformat(str(row[0]))
    except ValueError:
        return None
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    return until.isoformat() if until > current else None


def paused_error() -> ApiError:
    return ApiError(403, "SHARING_PAUSED", PAUSED_MESSAGE)


def sharing_state(db_path, now: datetime | None = None) -> dict:
    connection = _connect(db_path)
    try:
        until = paused_until(connection, now)
    finally:
        connection.close()
    return {"paused": until is not None, "paused_until": until}


def pause_sharing(db_path, now: datetime | None = None) -> dict:
    current = now or datetime.now(timezone.utc)
    until = (current + timedelta(minutes=PAUSE_MINUTES)).isoformat()
    with commit_lock:
        connection = _connect(db_path)
        try:
            connection.execute(
                """
                INSERT INTO settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (PAUSE_KEY, until),
            )
            connection.commit()
        finally:
            connection.close()
    return {"paused": True, "paused_until": until}


def resume_sharing(db_path) -> dict:
    with commit_lock:
        connection = _connect(db_path)
        try:
            connection.execute("DELETE FROM settings WHERE key = ?", (PAUSE_KEY,))
            connection.commit()
        finally:
            connection.close()
    return {"paused": False, "paused_until": None}


def _connect(db_path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection
