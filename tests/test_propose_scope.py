"""P0 of the agent-as-extractor change: separate proposal scope and service request ids.

Runs in-process against a temporary control database. propose_memory does
not touch the Kernel, so no embedding model is needed.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from zhiwo.api.errors import ApiError  # noqa: E402
from zhiwo.contracts.memory import CATEGORIES  # noqa: E402
from zhiwo.repositories.migrate import migrate  # noqa: E402

migrations = sys.modules["zhiwo.repositories.migrate"]
from zhiwo.services.agent_tools import propose_memory  # noqa: E402
from zhiwo.services.agents import create_agent, list_agents, update_agent  # noqa: E402
from zhiwo.services.client_connect import connect_client  # noqa: E402

ALL = list(CATEGORIES)
PROPOSE_TOOLS = ["get_context", "search_memory", "propose_memory"]


def _change(content: str, category: str = "identity") -> dict:
    return {"type": "add", "content": content, "kind": "fact", "category": category}


class ProposeScopeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp(prefix="zhiwo-propose-scope-"))
        self.db = self.directory / "zhiwo.db"
        self.home = self.directory / "home"
        self.home.mkdir()

    def _agent(self, tools: list[str], categories: list[str], propose: list[str] | None = None) -> dict:
        created = create_agent(self.db, str(uuid.uuid4()), "合成连接")
        patch = {"allowed_tools": tools, "allowed_categories": categories}
        if propose is not None:
            patch["propose_categories"] = propose
        view = update_agent(self.db, created["id"], str(uuid.uuid4()), patch)
        view["credential"] = created["credential"]
        return view

    def _propose(self, secret: str, change: dict, evidence: str = "我叫林舟", request_id: str | None = None) -> dict:
        return propose_memory(self.db, None, secret, request_id, change, {"text": evidence})

    def test_migration_keeps_existing_behaviour(self) -> None:
        connection = sqlite3.connect(self.db)
        try:
            connection.execute(
                "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
            )
            for version, name, script in migrations.MIGRATIONS[:7]:
                script(connection) if callable(script) else connection.executescript(script)
                connection.execute("INSERT INTO schema_migrations VALUES (?, ?, 'x')", (version, name))
            connection.execute(
                "INSERT INTO agents (id, name, credential_hash, enabled, policy_version, created_at, updated_at)"
                " VALUES ('a1', '旧连接', 'h', 1, 3, 'x', 'x')"
            )
            connection.execute(
                "INSERT INTO agent_permissions (agent_id, allowed_tools, allowed_categories)"
                " VALUES ('a1', '[\"propose_memory\"]', '[\"preference\",\"goal\"]')"
            )
            connection.commit()
        finally:
            connection.close()
        self.assertEqual(migrate(self.db), 8)
        self.assertEqual(migrate(self.db), 8)
        agent = list_agents(self.db)["agents"][0]
        self.assertEqual(agent["propose_categories"], ["preference", "goal"])
        self.assertEqual(agent["policy_version"], 3)

    def test_presets(self) -> None:
        migrate(self.db)
        proposing = connect_client(self.db, self.home, "opencode", "propose", str(uuid.uuid4()), port=8765)
        reading = connect_client(self.db, self.home, "claude-code", "read", str(uuid.uuid4()), port=8765)
        views = {agent["id"]: agent for agent in list_agents(self.db)["agents"]}
        self.assertEqual(views[proposing["agent_id"]]["allowed_categories"], ["preference", "goal"])
        self.assertEqual(views[proposing["agent_id"]]["propose_categories"], ALL)
        self.assertEqual(views[reading["agent_id"]]["propose_categories"], [])
        custom = connect_client(
            self.db, self.home, "zcode", "propose", str(uuid.uuid4()), port=8765,
            allowed_tools=PROPOSE_TOOLS, allowed_categories=["preference"], propose_categories=["project"],
        )
        view = next(agent for agent in list_agents(self.db)["agents"] if agent["id"] == custom["agent_id"])
        self.assertEqual(view["propose_categories"], ["project"])

    def test_update_agent_scope_and_policy_version(self) -> None:
        migrate(self.db)
        agent = self._agent(PROPOSE_TOOLS, ["preference"])
        self.assertEqual(agent["propose_categories"], ALL, "turning on proposals without categories means all")
        before = agent["policy_version"]
        narrowed = update_agent(self.db, agent["id"], str(uuid.uuid4()), {"propose_categories": ["goal"]})
        self.assertEqual(narrowed["propose_categories"], ["goal"])
        self.assertEqual(narrowed["allowed_categories"], ["preference"])
        self.assertEqual(narrowed["policy_version"], before + 1)
        renamed = update_agent(self.db, agent["id"], str(uuid.uuid4()), {"name": "新名字"})
        self.assertEqual(renamed["policy_version"], before + 1)
        self.assertEqual(renamed["propose_categories"], ["goal"])
        with self.assertRaises(ApiError):
            update_agent(self.db, agent["id"], str(uuid.uuid4()), {"propose_categories": ["身份"]})

    def test_propose_uses_propose_scope_not_read_scope(self) -> None:
        migrate(self.db)
        agent = self._agent(PROPOSE_TOOLS, ["preference"], propose=["identity"])
        stored = self._propose(agent["credential"], _change("用户叫林舟", "identity"))
        self.assertEqual(stored["status"], "pending")
        with self.assertRaises(ApiError) as denied:
            self._propose(agent["credential"], _change("偏好先给结论", "preference"), "先给结论")
        self.assertEqual(denied.exception.status, 403)
        self.assertIn("identity", denied.exception.message)
        none = self._agent(["search_memory", "propose_memory"], ["preference"], propose=[])
        with self.assertRaises(ApiError) as nothing:
            self._propose(none["credential"], _change("用户叫林舟"))
        self.assertEqual(nothing.exception.status, 403)

    def test_service_request_ids(self) -> None:
        migrate(self.db)
        agent = self._agent(PROPOSE_TOOLS, ["preference"])
        first = self._propose(agent["credential"], _change("用户叫林舟"))
        again = self._propose(agent["credential"], _change("用户叫林舟"))
        self.assertEqual(first["proposal_id"], again["proposal_id"], "the same suggestion is stored once")
        self.assertEqual(str(uuid.UUID(first["request_id"])), first["request_id"])
        other_evidence = self._propose(agent["credential"], _change("用户叫林舟"), "我的名字是林舟")
        self.assertNotEqual(first["proposal_id"], other_evidence["proposal_id"])
        explicit = str(uuid.uuid4())
        mine = self._propose(agent["credential"], _change("用户在杭州"), "在杭州", request_id=explicit)
        self.assertEqual(mine["request_id"], explicit)
        with self.assertRaises(ApiError) as conflict:
            self._propose(agent["credential"], _change("用户在上海"), "在上海", request_id=explicit)
        self.assertEqual(conflict.exception.status, 409)
        other = self._agent(PROPOSE_TOOLS, ["preference"])
        theirs = self._propose(other["credential"], _change("用户叫林舟"))
        self.assertNotEqual(theirs["proposal_id"], first["proposal_id"], "derived keys are per connection")
        connection = sqlite3.connect(self.db)
        try:
            count = connection.execute("SELECT COUNT(*) FROM proposals").fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(count, 4)


if __name__ == "__main__":
    unittest.main()
