"""Filter the owner memory list by the same origin the list displays.

Uses a temporary database. Does not read the real library.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from zhiwo.api.errors import ApiError
from zhiwo.repositories.migrate import migrate, put_setting
from zhiwo.services.agents import create_agent
from zhiwo.services.memories import list_memories, parse_origin

ROOT = Path(__file__).resolve().parents[1]


class OriginFilterTest(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self.directory = Path(tempfile.mkdtemp(prefix="zhiwo-origin-"))
        self.db = self.directory / "zhiwo.db"
        migrate(self.db)
        self.handle = type("Handle", (), {"db_path": self.db})()
        self._read = __import__("zhiwo.services.memories", fromlist=["read_version"])
        self.original = self._read.read_version
        self._read.read_version = lambda *_args, **_kwargs: "一条记忆"

    def tearDown(self) -> None:
        import shutil

        self._read.read_version = self.original
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_origin_keeps_one_client_and_lists_the_rest(self) -> None:
        self._memory("own", None, "2026-09-26T00:00:00+00:00")
        agent = create_agent(self.db, str(uuid.uuid4()), "OpenCode")
        put_setting(self.db, "client_agent:opencode", agent["id"])
        source = self._source("agent_claim")
        self._proposal(source, agent["id"])
        self._memory("from-agent", source, "2026-09-26T01:00:00+00:00")
        loose = self._source("agent_claim")
        self._memory("unlinked", loose, "2026-09-26T02:00:00+00:00")

        own = list_memories(self.db, self.handle, origin="omna", state="all", limit=50)
        self.assertEqual([item["id"] for item in own["items"]], ["own"])
        agent_rows = list_memories(self.db, self.handle, origin="opencode", state="all", limit=50)
        self.assertEqual([item["id"] for item in agent_rows["items"]], ["from-agent"])
        unnamed = list_memories(self.db, self.handle, origin="agent:Agent 提案", state="all", limit=50)
        self.assertEqual([item["id"] for item in unnamed["items"]], ["unlinked"])
        self.assertEqual(
            [item["id"] for item in own["origins"]],
            ["omna", "opencode", "agent:Agent 提案"],
        )
        with self.assertRaises(ApiError):
            parse_origin("../codex")

    def _memory(self, memory_id: str, source_id: str | None, created_at: str) -> None:
        connection = sqlite3.connect(self.db)
        try:
            connection.execute(
                """
                INSERT INTO memory_refs (
                    memory_id, revision, kernel_id, kernel_session, kind, category,
                    lifecycle, share_enabled, source_refs, created_at
                ) VALUES (?, 1, ?, ?, 'fact', 'preference', 'active', 0, ?, ?)
                """,
                (
                    memory_id,
                    f"kernel-{memory_id}",
                    f"session-{memory_id}",
                    json.dumps([source_id] if source_id else []),
                    created_at,
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def _source(self, kind: str) -> str:
        source_id = str(uuid.uuid4())
        connection = sqlite3.connect(self.db)
        try:
            connection.execute(
                """
                INSERT INTO sources (id, kind, name, content, content_hash, imported_at)
                VALUES (?, ?, '', 'evidence', 'hash', '2026-09-26T00:00:00+00:00')
                """,
                (source_id, kind),
            )
            connection.commit()
        finally:
            connection.close()
        return source_id

    def _proposal(self, source_id: str, agent_id: str) -> None:
        connection = sqlite3.connect(self.db)
        try:
            connection.execute(
                """
                INSERT INTO proposals (
                    id, origin, change_type, payload_json, evidence_json, status,
                    source_id, job_id, agent_id, created_at
                ) VALUES (?, 'agent', 'add', '{}', '{}', 'accepted', ?, ?, ?, '2026-09-26T00:00:00+00:00')
                """,
                (str(uuid.uuid4()), source_id, str(uuid.uuid4()), agent_id),
            )
            connection.commit()
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
