"""Refuse a Kernel write before it can evict an existing row.

The count matches BeamMemory._trim_working_memory: unconsolidated rows in
the same session_id. History and unpublished rows in that session count.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

_write_lock = threading.Lock()


class CapacityExceeded(RuntimeError):
    """The session is already at the configured working-memory cap."""


def evictable_count(db_path: Path, session_id: str) -> int:
    connection = sqlite3.connect(db_path)
    try:
        return connection.execute(
            """
            SELECT COUNT(*) FROM working_memory
            WHERE session_id = ? AND consolidated_at IS NULL
            """,
            (session_id,),
        ).fetchone()[0]
    finally:
        connection.close()


def remember_within_cap(mem, db_path: Path, session_id: str, content: str, limit: int, **kwargs):
    """Count and remember under one lock. Does not call remember when full."""
    if limit < 1:
        raise CapacityExceeded("working-memory cap must be positive")
    with _write_lock:
        count = evictable_count(db_path, session_id)
        if count >= limit:
            raise CapacityExceeded(f"{session_id} already holds {count} rows; cap is {limit}")
        return mem.remember(
            content,
            source="zhiwo",
            importance=0.9,
            scope="global",
            extract=False,
            extract_entities=False,
            **kwargs,
        )
