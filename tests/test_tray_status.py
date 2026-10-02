"""v2: tray status and per-memory read counts.

Temporary control database, synthetic rows, no Kernel. Checks that the
tray read leaks no memory text or query, that refused and unsent calls do
not count as reads, and that the Owner routes are wired.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from zhiwo.repositories.migrate import migrate  # noqa: E402
from zhiwo.services.access import attach_reads, memory_reads  # noqa: E402
from zhiwo.services.status import tray_status  # noqa: E402

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
SECRET = "合成正文：周报在周五下午写"
QUERY = "合成查询：周报习惯"


def _at(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).isoformat()


class TrayStatusTest(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory(prefix="zhiwo-status-")
        self.db = Path(self.folder.name) / "zhiwo.db"
        migrate(self.db)
        self.connection = sqlite3.connect(self.db)
        self._agent("agent-cc", "Claude Code")
        self._agent("agent-oc", "OpenCode")
        self._memory("mem-pref", "preference")
        self._memory("mem-proj", "project")

    def tearDown(self) -> None:
        self.connection.close()
        self.folder.cleanup()

    def _agent(self, agent_id: str, name: str) -> None:
        self.connection.execute(
            """
            INSERT INTO agents (id, name, credential_hash, enabled, policy_version, created_at, updated_at)
            VALUES (?, ?, ?, 1, 1, ?, ?)
            """,
            (agent_id, name, f"hash-{agent_id}", _at(500), _at(500)),
        )
        self.connection.commit()

    def _memory(self, memory_id: str, category: str) -> None:
        self.connection.execute(
            """
            INSERT INTO memory_refs (
                memory_id, revision, kernel_id, kernel_session, kind, category, lifecycle,
                share_enabled, created_at
            ) VALUES (?, 1, ?, ?, 'fact', ?, 'active', 1, ?)
            """,
            (memory_id, f"k-{memory_id}", f"s-{memory_id}", category, _at(500)),
        )
        self.connection.commit()

    def _event(self, agent_id, tool, hours_ago, ids=(), outcome="success", delivery="sent", query=QUERY) -> None:
        payload = {
            "request_id": str(uuid.uuid4()),
            "query": query,
            "items": [{"id": memory_id, "revision": 1, "content": SECRET, "category": "x"} for memory_id in ids],
        }
        self.connection.execute(
            """
            INSERT INTO access_events (
                id, request_id, agent_id, tool, outcome, policy_version,
                response_snapshot, created_at, delivery_state
            ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (str(uuid.uuid4()), payload["request_id"], agent_id, tool, outcome,
             json.dumps(payload, ensure_ascii=False), _at(hours_ago), delivery),
        )
        self.connection.commit()

    def _pending(self, created_hours_ago: float, status: str = "pending") -> None:
        self.connection.execute(
            """
            INSERT INTO sources (id, kind, name, content, content_hash, imported_at)
            VALUES (?, 'paste', NULL, '合成来源', 'h', ?)
            """,
            (source := str(uuid.uuid4()), _at(created_hours_ago)),
        )
        self.connection.execute(
            """
            INSERT INTO proposals (id, origin, change_type, payload_json, evidence_json, status,
                                   source_id, job_id, created_at)
            VALUES (?, 'import', 'add', '{}', '{}', ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), status, source, str(uuid.uuid4()), _at(created_hours_ago)),
        )
        self.connection.commit()

    def test_status_counts_without_text(self) -> None:
        self._event("agent-cc", "get_context", 1, ["mem-pref", "mem-proj"])
        self._event("agent-cc", "search_memory", 0.5, ["mem-pref"])
        self._event("agent-oc", "search_memory", 2, ["mem-proj"])
        self._event("agent-oc", "search_memory", 0.2, ["mem-pref"], outcome="rejected")
        self._event("agent-oc", "propose_memory", 0.1)
        self._event("agent-cc", "get_context", 30, ["mem-pref"])
        self._pending(3)
        self._pending(1)
        self._pending(0.5, status="rejected")

        view = tray_status(self.db, embeddings_loaded=True, utc_offset_minutes=0, now=NOW)

        self.assertEqual(view["pending"]["count"], 2)
        self.assertEqual(view["pending"]["latest_at"], _at(1))
        self.assertEqual(view["sharing"], {"paused": False, "paused_until": None})
        self.assertEqual(view["reads_today"]["total"], 3)
        self.assertEqual(
            view["reads_today"]["agents"],
            [{"id": "agent-cc", "name": "Claude Code", "count": 2}, {"id": "agent-oc", "name": "OpenCode", "count": 1}],
        )
        recent = view["recent_reads"]
        self.assertEqual(len(recent), 2)
        self.assertEqual(recent[0]["tool"], "search_memory")
        self.assertEqual(recent[0]["categories"], {"preference": 1})
        self.assertEqual(recent[1]["categories"], {"preference": 1, "project": 1})
        self.assertEqual(recent[1]["agent_name"], "Claude Code")
        encoded = json.dumps(view, ensure_ascii=False)
        self.assertNotIn(SECRET, encoded)
        self.assertNotIn(QUERY, encoded)

    def test_reads_7d_counts_only_sent_successful_reads(self) -> None:
        self._event("agent-cc", "get_context", 1, ["mem-pref", "mem-proj"])
        self._event("agent-cc", "search_memory", 24 * 6, ["mem-pref"])
        self._event("agent-cc", "search_memory", 24 * 8, ["mem-pref"])
        self._event("agent-oc", "search_memory", 2, ["mem-pref"], outcome="rejected")
        self._event("agent-oc", "search_memory", 2, ["mem-pref"], delivery="failed")
        self._event("agent-oc", "search_memory", 2, ["mem-pref"], delivery="prepared")
        self._event("agent-oc", "propose_memory", 2, ["mem-pref"])
        self.assertEqual(memory_reads(self.db, ["mem-pref", "mem-proj", "mem-none"], now=NOW),
                         {"mem-pref": 2, "mem-proj": 1, "mem-none": 0})
        items = [{"id": "mem-proj"}, {"id": "mem-pref"}]
        attach_reads(self.db, items, now=NOW)
        self.assertEqual([item["reads_7d"] for item in items], [1, 2])

    def test_owner_routes(self) -> None:
        from fastapi.testclient import TestClient

        from zhiwo.api.app import create_app
        from zhiwo.api.auth import require_owner

        app = create_app()
        app.router.lifespan_context = _no_lifespan
        app.dependency_overrides[require_owner] = lambda: None
        app.state.settings = SimpleNamespace(control_db=self.db)
        app.state.kernel = SimpleNamespace(embeddings_loaded=False)
        with TestClient(app) as client:
            status = client.get("/api/v1/status", params={"utc_offset_minutes": -480}).json()
            self.assertEqual(status["service"], {"ok": True, "embeddings_loaded": False})
            paused = client.post("/api/v1/sharing/pause").json()
            self.assertTrue(paused["paused"])
            self.assertEqual(client.get("/api/v1/status").json()["sharing"], paused)
            self.assertEqual(client.delete("/api/v1/sharing/pause").json(), {"paused": False, "paused_until": None})


from contextlib import asynccontextmanager  # noqa: E402


@asynccontextmanager
async def _no_lifespan(_app):
    yield


if __name__ == "__main__":
    unittest.main()
