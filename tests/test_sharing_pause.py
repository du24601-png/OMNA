"""v2: pause sharing for one hour.

Runs in-process against a temporary control database with synthetic data.
Recall is stubbed to return nothing, so no Kernel or embedding model is
needed; the point is the gate, not retrieval.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from zhiwo.api.channel import handoff  # noqa: E402
from zhiwo.api.errors import ApiError  # noqa: E402
from zhiwo.contracts.memory import CATEGORIES, TOOLS  # noqa: E402
from zhiwo.repositories.migrate import migrate, put_setting  # noqa: E402
from zhiwo.services.agent_tools import explain_memory, get_context, propose_memory, search_memory  # noqa: E402
from zhiwo.services.agents import create_agent, update_agent  # noqa: E402
from zhiwo.services.sharing import PAUSE_KEY, pause_sharing, resume_sharing, sharing_state  # noqa: E402


def _nothing(_query, _top_k):
    return []


class SharingPauseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory(prefix="zhiwo-pause-")
        self.db = Path(self.folder.name) / "zhiwo.db"
        migrate(self.db)
        created = create_agent(self.db, str(uuid.uuid4()), "合成连接")
        update_agent(
            self.db,
            created["id"],
            str(uuid.uuid4()),
            {"allowed_tools": list(TOOLS), "allowed_categories": list(CATEGORIES)},
        )
        self.agent_id = created["id"]
        self.secret = created["credential"]

    def tearDown(self) -> None:
        self.folder.cleanup()

    def _calls(self):
        return {
            "get_context": lambda: get_context(self.db, None, self.secret, "写周报", recall=_nothing),
            "search_memory": lambda: search_memory(self.db, None, self.secret, "周报", recall=_nothing),
            "explain_memory": lambda: explain_memory(self.db, None, self.secret, "no-such-memory"),
            "propose_memory": lambda: propose_memory(
                self.db,
                None,
                self.secret,
                None,
                {"type": "add", "content": "周报在周五下午写", "kind": "fact", "category": "preference"},
                {"text": "周报在周五下午写"},
            ),
        }

    def _events(self) -> list[sqlite3.Row]:
        connection = sqlite3.connect(self.db)
        connection.row_factory = sqlite3.Row
        try:
            return connection.execute("SELECT * FROM access_events ORDER BY created_at, id").fetchall()
        finally:
            connection.close()

    def test_all_four_tools_refuse_and_are_recorded(self) -> None:
        state = pause_sharing(self.db)
        self.assertTrue(state["paused"])
        for tool, call in self._calls().items():
            with self.subTest(tool=tool):
                with self.assertRaises(ApiError) as caught:
                    call()
                self.assertEqual(caught.exception.code, "SHARING_PAUSED")
                self.assertIn("用户暂停了共享", caught.exception.message)
        events = self._events()
        self.assertEqual(sorted(row["tool"] for row in events), sorted(TOOLS))
        for row in events:
            self.assertEqual(row["outcome"], "rejected")
            self.assertEqual(row["agent_id"], self.agent_id)
            self.assertEqual(json.loads(row["response_snapshot"])["error"]["code"], "SHARING_PAUSED")

    def test_resume_and_expiry_restore_sharing(self) -> None:
        pause_sharing(self.db)
        resume_sharing(self.db)
        self.assertEqual(sharing_state(self.db), {"paused": False, "paused_until": None})
        self.assertEqual(get_context(self.db, None, self.secret, "写周报", recall=_nothing)["items"], [])

        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        put_setting(self.db, PAUSE_KEY, past)
        self.assertFalse(sharing_state(self.db)["paused"])
        for tool, call in self._calls().items():
            if tool == "explain_memory":
                continue
            with self.subTest(tool=tool):
                call()

    def test_pause_survives_restart(self) -> None:
        until = pause_sharing(self.db)["paused_until"]
        migrate(self.db)
        self.assertEqual(sharing_state(self.db), {"paused": True, "paused_until": until})

    def test_pause_after_authorization_still_refuses(self) -> None:
        with self.assertRaises(ApiError) as caught:
            get_context(
                self.db,
                None,
                self.secret,
                "写周报",
                recall=_nothing,
                before_commit=lambda: pause_sharing(self.db),
            )
        self.assertEqual(caught.exception.code, "SHARING_PAUSED")
        self.assertEqual(self._events()[-1]["outcome"], "rejected")

    def test_pause_before_send_withholds_prepared_payload(self) -> None:
        get_context(self.db, None, self.secret, "写周报", recall=_nothing)
        event_id = self._events()[-1]["id"]
        pause_sharing(self.db)
        sent: list[dict] = []

        async def collect(message):
            sent.append(message)

        asyncio.run(handoff(self.db, event_id, collect))
        self.assertEqual(sent[0]["status"], 403)
        body = json.loads(sent[-1]["body"])
        self.assertEqual(body["error"]["code"], "SHARING_PAUSED")
        row = self._events()[-1]
        self.assertEqual(row["outcome"], "rejected")
        self.assertEqual(row["delivery_state"], "sent")


if __name__ == "__main__":
    unittest.main()
