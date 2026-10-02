"""Which agent is organizing its instruction file into memories right now.

The owner opens a session from the agent page or onboarding; for one hour
that agent's new additions are tagged with the session's batch id, so they
can be accepted or undone together. Kept tiny so the agent tools can read it
without importing the review and delete services.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

SESSION_PREFIX = "organize_batch:"


def open_session(connection, agent_id: str | None, now: datetime | None = None) -> str | None:
    """The batch id of this agent's organize session, if one is still open."""
    if not agent_id:
        return None
    row = connection.execute("SELECT value FROM settings WHERE key = ?", (SESSION_PREFIX + agent_id,)).fetchone()
    if row is None:
        return None
    try:
        session = json.loads(row[0])
        expires = datetime.fromisoformat(session["expires_at"])
    except (ValueError, KeyError, TypeError):
        return None
    if expires <= (now or datetime.now(timezone.utc)):
        return None
    return session.get("batch_id")
