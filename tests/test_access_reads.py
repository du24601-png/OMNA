"""Homepage read chart counts real calls and returns no memory text."""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from zhiwo.api.errors import ApiError
from zhiwo.repositories.migrate import migrate
from zhiwo.services.access import read_counts

NOW = datetime(2026, 9, 26, 15, 0, tzinfo=timezone.utc)
OFFSET = 240
SECRET = "机密正文不该出现在趋势里"


def _agent(db_path: Path, agent_id: str, name: str) -> None:
    now = "2026-09-01T00:00:00+00:00"
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """
            INSERT INTO agents (
                id, name, credential_hash, enabled, policy_version, created_at, updated_at
            ) VALUES (?, ?, ?, 1, 1, ?, ?)
            """,
            (agent_id, name, f"hash-{agent_id}", now, now),
        )
        connection.commit()
    finally:
        connection.close()


def _event(db_path: Path, agent_id: str, tool: str, created_at: str, outcome: str = "success") -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """
            INSERT INTO access_events (
                id, request_id, agent_id, tool, outcome, policy_version,
                response_snapshot, created_at, delivery_state
            ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, 'sent')
            """,
            (
                f"{agent_id}-{tool}-{created_at}",
                f"req-{agent_id}-{created_at}",
                agent_id,
                tool,
                outcome,
                json.dumps({"items": [{"content": SECRET}]}, ensure_ascii=False),
                created_at,
            ),
        )
        connection.commit()
    finally:
        connection.close()


class ReadCountsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.db = Path(self.folder.name) / "zhiwo.db"
        migrate(self.db)
        _agent(self.db, "agent-opencode", "OpenCode")
        _agent(self.db, "agent-claude", "Claude")
        _agent(self.db, "agent-propose", "只提议")
        _event(self.db, "agent-opencode", "search_memory", "2026-09-26T15:00:00+00:00")
        _event(self.db, "agent-opencode", "get_context", "2026-09-26T03:00:00+00:00")
        _event(self.db, "agent-claude", "explain_memory", "2026-09-20T16:00:00+00:00", "rejected")
        _event(self.db, "agent-claude", "search_memory", "2026-09-13T16:00:00+00:00")
        _event(self.db, "agent-propose", "propose_memory", "2026-09-26T15:00:00+00:00")
        _event(self.db, "gone", "search_memory", "2026-09-26T15:00:00+00:00")

    def tearDown(self) -> None:
        self.folder.cleanup()

    def test_local_days_skip_proposals_and_omit_text(self) -> None:
        view = read_counts(self.db, 7, OFFSET, now=NOW)
        self.assertEqual(
            view["days"],
            ["2026-09-20", "2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25", "2026-09-26"],
        )
        by_id = {item["id"]: item for item in view["series"]}
        self.assertEqual(by_id["agent-opencode"]["name"], "OpenCode")
        self.assertEqual(by_id["agent-opencode"]["counts"], [0, 0, 0, 0, 0, 1, 1])
        self.assertEqual(by_id["agent-claude"]["counts"], [1, 0, 0, 0, 0, 0, 0])
        self.assertEqual(by_id["gone"]["name"], "已移除的连接")
        self.assertNotIn("agent-propose", by_id)
        self.assertEqual(view["series"][0]["id"], "agent-opencode")
        self.assertNotIn(SECRET, json.dumps(view, ensure_ascii=False))
        self.assertEqual(set(view), {"days", "series", "last_24h"})
        self.assertEqual(len(view["last_24h"]["hours"]), 24)

    def test_fourteen_days_include_the_older_read(self) -> None:
        view = read_counts(self.db, 14, OFFSET, now=NOW)
        self.assertEqual(len(view["days"]), 14)
        self.assertEqual(view["days"][0], "2026-09-13")
        claude = next(item for item in view["series"] if item["id"] == "agent-claude")
        self.assertEqual(claude["counts"][0], 1)
        self.assertEqual(sum(claude["counts"]), 2)

    def test_empty_library_has_days_and_no_series(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "zhiwo.db"
            migrate(db)
            view = read_counts(db, 7, 0, now=NOW)
        self.assertEqual(len(view["days"]), 7)
        self.assertEqual(view["series"], [])
        self.assertEqual(len(view["last_24h"]["hours"]), 24)
        self.assertEqual(view["last_24h"]["series"], [])

    def test_last_24_hours_bucket_by_local_hour(self) -> None:
        _agent(self.db, "agent-edge", "边界")
        _event(self.db, "agent-edge", "search_memory", "2026-09-25T15:00:00+00:00")
        _event(self.db, "agent-edge", "get_context", "2026-09-25T16:00:00+00:00")
        view = read_counts(self.db, 7, OFFSET, now=NOW)
        hours = view["last_24h"]
        self.assertEqual(hours["hours"][0], "2026-09-25T12:00")
        self.assertEqual(hours["hours"][-1], "2026-09-26T11:00")
        by_id = {item["id"]: item for item in hours["series"]}
        self.assertEqual(by_id["agent-opencode"]["counts"][11], 1)
        self.assertEqual(by_id["agent-opencode"]["counts"][23], 1)
        self.assertEqual(sum(by_id["agent-opencode"]["counts"]), 2)
        self.assertEqual(by_id["gone"]["counts"][23], 1)
        self.assertEqual(by_id["agent-edge"]["counts"][0], 1)
        self.assertEqual(sum(by_id["agent-edge"]["counts"]), 1)
        self.assertNotIn("agent-claude", by_id)
        self.assertNotIn("agent-propose", by_id)
        self.assertNotIn(SECRET, json.dumps(hours, ensure_ascii=False))

    def test_rejects_unknown_window_and_offset(self) -> None:
        with self.assertRaises(ApiError) as days_error:
            read_counts(self.db, 5, 0, now=NOW)
        self.assertEqual(days_error.exception.code, "VALIDATION_ERROR")
        with self.assertRaises(ApiError) as offset_error:
            read_counts(self.db, 7, 24 * 60, now=NOW)
        self.assertEqual(offset_error.exception.code, "VALIDATION_ERROR")


if __name__ == "__main__":
    unittest.main()
