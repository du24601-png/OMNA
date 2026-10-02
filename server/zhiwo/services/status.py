"""One small read for the tray and the flyout.

The desktop shell polls this every few seconds. It returns counts, times,
names, and categories only: no memory text, query, or snapshot.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from zhiwo.services.access import read_counts, recent_reads
from zhiwo.services.sharing import sharing_state


def tray_status(db_path, *, embeddings_loaded: bool, utc_offset_minutes: int, now: datetime | None = None) -> dict:
    connection = sqlite3.connect(db_path)
    try:
        count, latest = connection.execute(
            "SELECT COUNT(*), MAX(created_at) FROM proposals WHERE status = 'pending'"
        ).fetchone()
    finally:
        connection.close()
    counts = read_counts(db_path, 7, utc_offset_minutes, now=now, include_rejected=False)
    agents = [
        {"id": series["id"], "name": series["name"], "count": series["counts"][-1]}
        for series in counts["series"]
        if series["counts"][-1] > 0
    ]
    agents.sort(key=lambda item: (-item["count"], item["name"]))
    return {
        "service": {"ok": True, "embeddings_loaded": bool(embeddings_loaded)},
        "pending": {"count": int(count), "latest_at": latest},
        "sharing": sharing_state(db_path, now),
        "reads_today": {"total": sum(item["count"] for item in agents), "agents": agents},
        "recent_reads": recent_reads(db_path, 2),
    }
