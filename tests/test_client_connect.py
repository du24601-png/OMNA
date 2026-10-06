"""Merge one omna entry into the known client configs.

Uses a temporary home. Does not read or write the real user profile.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from zhiwo.api.auth import credential_digest
from zhiwo.api.errors import ApiError
from zhiwo.repositories.migrate import migrate, put_setting
from zhiwo.services.agents import create_agent, list_agents
from zhiwo.services.client_connect import connect_client, list_clients

ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "tests" / "results" / "client_connect_windows.json"


class ClientConnectTest(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self.directory = Path(tempfile.mkdtemp(prefix="zhiwo-clients-"))
        self.home = self.directory / "home"
        self.home.mkdir()
        self.db = self.directory / "zhiwo.db"
        migrate(self.db)
        self.lookup = lambda _name: None
        self._saved_env = {
            key: os.environ.pop(key, None)
            for key in ("ZHIWO_BRIDGE_PYTHON", "ZHIWO_BRIDGE_PYTHONPATH")
        }

    def tearDown(self) -> None:
        import shutil

        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        link = self.home / ".workbuddy"
        if link.is_junction():
            link.rmdir()
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_list_is_the_known_clients(self) -> None:
        listed = list_clients(self.home, self.lookup)
        self.assertEqual(
            [item["id"] for item in listed["clients"]],
            ["workbuddy", "zcode", "opencode", "codex", "claude", "claude-code"],
        )
        self.assertTrue(all(item["installed"] is False and item["configured"] is False for item in listed["clients"]))
        (self.home / ".codex").mkdir()
        again = list_clients(self.home, self.lookup)
        codex = next(item for item in again["clients"] if item["id"] == "codex")
        self.assertEqual(codex["name"], "ChatGPT")
        self.assertTrue(codex["installed"])
        self.assertFalse(next(item["installed"] for item in again["clients"] if item["id"] == "workbuddy"))
        code_only = list_clients(self.home, lambda name: "claude" if name == "claude" else None)
        by_id = {item["id"]: item for item in code_only["clients"]}
        self.assertTrue(by_id["claude-code"]["installed"])
        self.assertFalse(by_id["claude"]["installed"])
        (self.home / "AppData" / "Roaming" / "Claude").mkdir(parents=True)
        desktop_only = list_clients(self.home, self.lookup)
        by_id = {item["id"]: item for item in desktop_only["clients"]}
        self.assertTrue(by_id["claude"]["installed"])
        self.assertFalse(by_id["claude-code"]["installed"])

    def test_existing_codex_connection_is_named_chatgpt(self) -> None:
        linked = create_agent(self.db, str(uuid.uuid4()), "Codex")
        other = create_agent(self.db, str(uuid.uuid4()), "Codex 笔记")
        put_setting(self.db, "client_agent:codex", linked["id"])
        names = {item["id"]: item["name"] for item in list_agents(self.db)["agents"]}
        self.assertEqual(names[linked["id"]], "ChatGPT")
        self.assertEqual(names[other["id"]], "Codex 笔记")

    def test_list_links_each_client_to_its_connection(self) -> None:
        (self.home / ".config" / "opencode").mkdir(parents=True)
        before = {item["id"]: item for item in list_clients(self.home, self.lookup, self.db)["clients"]}
        self.assertIsNone(before["opencode"]["agent_id"])
        result = connect_client(self.db, self.home, "opencode", "read", str(uuid.uuid4()), port=8765)
        custom = create_agent(self.db, str(uuid.uuid4()), "OpenCode")
        after = {item["id"]: item for item in list_clients(self.home, self.lookup, self.db)["clients"]}
        self.assertEqual(after["opencode"]["agent_id"], result["agent_id"])
        self.assertNotEqual(after["opencode"]["agent_id"], custom["id"])
        self.assertTrue(all(after[key]["agent_id"] is None for key in after if key != "opencode"))
        views = {item["id"]: item for item in list_agents(self.db)["agents"]}
        self.assertIsNone(views[result["agent_id"]]["last_access_at"])
        self.assertNotIn("credential", json.dumps(after))

    def test_merge_hides_credential_and_keeps_siblings(self) -> None:
        self._seed()
        first_id = str(uuid.uuid4())
        result = connect_client(self.db, self.home, "opencode", "read", first_id, port=8765)
        secret = self._secret("opencode")
        self.assertNotIn("credential", result)
        self.assertNotIn(secret, json.dumps(result))
        self.assertNotIn(secret.encode(), self.db.read_bytes())
        opencode = json.loads((self.home / ".config" / "opencode" / "opencode.json").read_text(encoding="utf-8"))
        self.assertEqual(opencode["model"], "keep-me")
        self.assertEqual(opencode["mcp"]["other"]["command"], ["echo"])
        self.assertEqual(opencode["mcp"]["omna"]["type"], "local")
        self.assertEqual(opencode["mcp"]["omna"]["environment"]["ZHIWO_AGENT_CREDENTIAL"], secret)
        self.assertEqual(opencode["mcp"]["omna"]["environment"]["ZHIWO_API_ORIGIN"], "http://127.0.0.1:8765")
        self.assertEqual(opencode["mcp"]["omna"]["environment"]["PYTHONPATH"], str(ROOT / "server"))
        permissions = self._permissions(result["agent_id"])
        self.assertEqual(permissions["allowed_categories"], ["preference", "goal"])
        self.assertEqual(permissions["allowed_tools"], ["get_context", "search_memory"])

        same = (self.home / ".config" / "opencode" / "opencode.json").read_text(encoding="utf-8")
        replay = connect_client(self.db, self.home, "opencode", "read", first_id, port=8765)
        self.assertEqual(replay["agent_id"], result["agent_id"])
        self.assertEqual((self.home / ".config" / "opencode" / "opencode.json").read_text(encoding="utf-8"), same)
        with self.assertRaises(ApiError) as conflict:
            connect_client(self.db, self.home, "opencode", "propose", first_id, port=8765)
        self.assertEqual(conflict.exception.status, 409)

        second = connect_client(self.db, self.home, "opencode", "propose", str(uuid.uuid4()), port=8765)
        self.assertEqual(second["agent_id"], result["agent_id"])
        changed = json.loads((self.home / ".config" / "opencode" / "opencode.json").read_text(encoding="utf-8"))
        new_secret = changed["mcp"]["omna"]["environment"]["ZHIWO_AGENT_CREDENTIAL"]
        self.assertNotEqual(new_secret, secret)
        self.assertNotIn(secret, json.dumps(changed))
        self.assertEqual(self._permissions(second["agent_id"])["allowed_tools"], ["get_context", "search_memory", "propose_memory"])
        self.assertEqual(changed["mcp"]["other"]["command"], ["echo"])

        for client_id in ("workbuddy", "zcode", "codex", "claude", "claude-code"):
            written = connect_client(self.db, self.home, client_id, "read", str(uuid.uuid4()), port=9)
            self.assertTrue(written["configured"])
            self.assertNotIn(self._secret(client_id), json.dumps(written))

        workbuddy = json.loads((self.home / ".workbuddy" / "mcp.json").read_text(encoding="utf-8"))
        self.assertEqual(workbuddy["mcpServers"]["wecom"]["command"], "uvx")
        self.assertIn("omna", workbuddy["mcpServers"])
        zcode = json.loads((self.home / ".zcode" / "cli" / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(zcode["theme"], "quiet")
        self.assertEqual(zcode["mcp"]["servers"]["memory"]["command"], "npx")
        codex = (self.home / ".codex" / "config.toml").read_text(encoding="utf-8")
        self.assertIn('model = "keep-me"', codex)
        self.assertIn("[mcp_servers.other]", codex)
        self.assertEqual(codex.count("[mcp_servers.omna]"), 1)
        self.assertNotIn("old-secret", codex)
        listed = list_clients(self.home, self.lookup)
        claude = json.loads((self.home / "AppData" / "Roaming" / "Claude" / "claude_desktop_config.json").read_text(encoding="utf-8"))
        self.assertEqual(claude["mcpServers"]["other"]["command"], "keep")
        self.assertEqual(claude["mcpServers"]["omna"]["command"], sys.executable)
        self.assertNotIn("type", claude["mcpServers"]["omna"])
        code = json.loads((self.home / ".claude.json").read_text(encoding="utf-8"))
        self.assertEqual(code["theme"], "keep-me")
        self.assertEqual(code["mcpServers"]["other"]["command"], "echo")
        self.assertEqual(code["mcpServers"]["omna"]["type"], "stdio")
        self.assertEqual(sum(1 for item in listed["clients"] if item["configured"]), 6)

    def test_invalid_json_is_left_unchanged(self) -> None:
        path = self.home / ".workbuddy" / "mcp.json"
        path.parent.mkdir(parents=True)
        path.write_text("{", encoding="utf-8")
        with self.assertRaises(ApiError) as error:
            connect_client(self.db, self.home, "workbuddy", "read", str(uuid.uuid4()), port=1)
        self.assertEqual(error.exception.code, "VALIDATION_ERROR")
        self.assertEqual(path.read_text(encoding="utf-8"), "{")
        self.assertFalse(path.with_name("mcp.json.tmp").exists())
        self.assertEqual(self._agent_count(), 0)

    def test_junction_outside_home_is_refused(self) -> None:
        import subprocess

        outside = self.directory / "outside"
        outside.mkdir()
        target = outside / "mcp.json"
        target.write_text("{}", encoding="utf-8")
        link = self.home / ".workbuddy"
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
            capture_output=True,
            text=True,
        )
        if created.returncode != 0:
            self.skipTest("junction not permitted")
        with self.assertRaises(ApiError) as error:
            connect_client(self.db, self.home, "workbuddy", "read", str(uuid.uuid4()), port=1)
        self.assertEqual(error.exception.code, "VALIDATION_ERROR")
        self.assertEqual(target.read_text(encoding="utf-8"), "{}")
        self.assertEqual(self._agent_count(), 0)

    def test_install_files_under_the_user_profile_count(self) -> None:
        npm = self.home / "AppData" / "Roaming" / "npm" / "opencode.cmd"
        npm.parent.mkdir(parents=True)
        npm.write_text("@echo off\n", encoding="utf-8")
        desktop = self.home / "AppData" / "Local" / "AnthropicClaude" / "app-1.0.0" / "claude.exe"
        desktop.parent.mkdir(parents=True)
        desktop.write_bytes(b"")
        listed = {item["id"]: item for item in list_clients(self.home, self.lookup)["clients"]}
        self.assertTrue(listed["opencode"]["installed"])
        self.assertTrue(listed["claude"]["installed"])
        self.assertFalse(listed["claude-code"]["installed"])
        self.assertFalse(listed["zcode"]["installed"])

    def test_command_on_the_user_path_counts(self) -> None:
        listed = {
            item["id"]: item
            for item in list_clients(
                self.home,
                self.lookup,
                path_lookup=lambda name: r"C:\Tools\claude.cmd" if name == "claude" else None,
            )["clients"]
        }
        self.assertTrue(listed["claude-code"]["installed"])
        self.assertFalse(listed["claude"]["installed"])

    def test_registered_claude_package_counts_as_desktop(self) -> None:
        listed = {
            item["id"]: item
            for item in list_clients(self.home, self.lookup, packages=frozenset({"claude"}))["clients"]
        }
        self.assertTrue(listed["claude"]["installed"])
        self.assertFalse(listed["claude-code"]["installed"])

    def test_packaged_runtime_is_what_gets_written(self) -> None:
        python = self.directory / "python.exe"
        python.write_bytes(b"")
        bundled = self.directory / "bundled"
        bundled.mkdir()
        os.environ["ZHIWO_BRIDGE_PYTHON"] = str(python)
        os.environ["ZHIWO_BRIDGE_PYTHONPATH"] = str(bundled)
        connect_client(self.db, self.home, "workbuddy", "read", str(uuid.uuid4()), port=8765)
        written = json.loads((self.home / ".workbuddy" / "mcp.json").read_text(encoding="utf-8"))
        entry = written["mcpServers"]["omna"]
        self.assertEqual(entry["command"], str(python.resolve()))
        self.assertEqual(entry["args"], ["-m", "zhiwo.gateway.stdio_bridge"])
        self.assertEqual(entry["env"]["PYTHONPATH"], str(bundled.resolve()))
        self.assertEqual(entry["env"]["ZHIWO_API_ORIGIN"], "http://127.0.0.1:8765")
        self.assertNotIn(entry["env"]["ZHIWO_AGENT_CREDENTIAL"], json.dumps({key: value for key, value in entry["env"].items() if key != "ZHIWO_AGENT_CREDENTIAL"}))

    def test_installed_runtime_runs_isolated(self) -> None:
        python = self.directory / "python.exe"
        python.write_bytes(b"")
        os.environ["ZHIWO_BRIDGE_PYTHON"] = str(python)
        connect_client(self.db, self.home, "workbuddy", "read", str(uuid.uuid4()), port=8765)
        entry = json.loads((self.home / ".workbuddy" / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]["omna"]
        self.assertEqual(entry["command"], str(python.resolve()))
        self.assertEqual(entry["args"], ["-I", "-X", "utf8", "-m", "zhiwo.gateway.stdio_bridge"])
        self.assertNotIn("PYTHONPATH", entry["env"])

    def test_missing_bundled_python_writes_nothing(self) -> None:
        os.environ["ZHIWO_BRIDGE_PYTHON"] = str(self.directory / "missing.exe")
        with self.assertRaises(ApiError) as error:
            connect_client(self.db, self.home, "workbuddy", "read", str(uuid.uuid4()), port=8765)
        self.assertEqual(error.exception.code, "UNAVAILABLE")
        self.assertFalse((self.home / ".workbuddy" / "mcp.json").exists())
        self.assertEqual(self._agent_count(), 0)

    def test_failed_rewrite_keeps_the_old_credential(self) -> None:
        first = connect_client(self.db, self.home, "workbuddy", "read", str(uuid.uuid4()), port=8765)
        path = self.home / ".workbuddy" / "mcp.json"
        before = path.read_text(encoding="utf-8")
        secret = json.loads(before)["mcpServers"]["omna"]["env"]["ZHIWO_AGENT_CREDENTIAL"]
        digest = self._hash(first["agent_id"])
        self.assertEqual(digest, credential_digest(secret))
        path.with_name("mcp.json.tmp").mkdir()
        with self.assertRaises(ApiError) as error:
            connect_client(
                self.db,
                self.home,
                "workbuddy",
                "propose",
                str(uuid.uuid4()),
                port=8765,
                allowed_tools=["get_context", "search_memory", "propose_memory", "explain_memory"],
                allowed_categories=["preference", "goal", "project"],
            )
        self.assertEqual(error.exception.code, "UNAVAILABLE")
        self.assertEqual(path.read_text(encoding="utf-8"), before)
        self.assertEqual(self._hash(first["agent_id"]), digest)
        self.assertEqual(self._permissions(first["agent_id"])["allowed_tools"], ["get_context", "search_memory"])

    def test_rewrite_can_keep_tools_outside_the_preset(self) -> None:
        first = connect_client(self.db, self.home, "zcode", "read", str(uuid.uuid4()), port=8765)
        tools = ["get_context", "search_memory", "propose_memory", "explain_memory"]
        categories = ["preference", "goal", "project"]
        again = connect_client(
            self.db,
            self.home,
            "zcode",
            "propose",
            str(uuid.uuid4()),
            port=8765,
            allowed_tools=tools,
            allowed_categories=categories,
        )
        self.assertEqual(again["agent_id"], first["agent_id"])
        self.assertEqual(self._permissions(first["agent_id"])["allowed_tools"], tools)
        self.assertEqual(self._permissions(first["agent_id"])["allowed_categories"], categories)
        with self.assertRaises(ApiError) as error:
            connect_client(
                self.db,
                self.home,
                "zcode",
                "read",
                str(uuid.uuid4()),
                port=8765,
                allowed_tools=["get_context"],
            )
        self.assertEqual(error.exception.code, "VALIDATION_ERROR")
        self.assertEqual(self._permissions(first["agent_id"])["allowed_tools"], tools)

    def _seed(self) -> None:
        opencode = self.home / ".config" / "opencode" / "opencode.json"
        opencode.parent.mkdir(parents=True)
        opencode.write_text(
            json.dumps({"model": "keep-me", "mcp": {"other": {"command": ["echo"]}}}),
            encoding="utf-8",
        )
        workbuddy = self.home / ".workbuddy" / "mcp.json"
        workbuddy.parent.mkdir(parents=True)
        workbuddy.write_text(json.dumps({"mcpServers": {"wecom": {"command": "uvx"}}}), encoding="utf-8")
        zcode = self.home / ".zcode" / "cli" / "config.json"
        zcode.parent.mkdir(parents=True)
        zcode.write_text(
            json.dumps({"theme": "quiet", "mcp": {"servers": {"memory": {"command": "npx"}}}}),
            encoding="utf-8",
        )
        codex = self.home / ".codex" / "config.toml"
        codex.parent.mkdir(parents=True)
        codex.write_text(
            'model = "keep-me"\n\n[mcp_servers.other]\ncommand = "echo"\n\n'
            '[mcp_servers.zhiwo]\ncommand = "old"\n\n[mcp_servers.zhiwo.env]\n'
            'ZHIWO_AGENT_CREDENTIAL = "old-secret"\n',
            encoding="utf-8",
        )
        desktop = self.home / "AppData" / "Roaming" / "Claude" / "claude_desktop_config.json"
        desktop.parent.mkdir(parents=True)
        desktop.write_text(json.dumps({"mcpServers": {"other": {"command": "keep"}}}), encoding="utf-8")
        (self.home / ".claude.json").write_text(
            json.dumps({"theme": "keep-me", "mcpServers": {"other": {"command": "echo"}}}),
            encoding="utf-8",
        )

    def _secret(self, client_id: str) -> str:
        if client_id == "opencode":
            data = json.loads((self.home / ".config" / "opencode" / "opencode.json").read_text(encoding="utf-8"))
            return data["mcp"]["omna"]["environment"]["ZHIWO_AGENT_CREDENTIAL"]
        if client_id == "workbuddy":
            data = json.loads((self.home / ".workbuddy" / "mcp.json").read_text(encoding="utf-8"))
            return data["mcpServers"]["omna"]["env"]["ZHIWO_AGENT_CREDENTIAL"]
        if client_id == "zcode":
            data = json.loads((self.home / ".zcode" / "cli" / "config.json").read_text(encoding="utf-8"))
            return data["mcp"]["servers"]["omna"]["env"]["ZHIWO_AGENT_CREDENTIAL"]
        if client_id == "claude":
            data = json.loads((self.home / "AppData" / "Roaming" / "Claude" / "claude_desktop_config.json").read_text(encoding="utf-8"))
            return data["mcpServers"]["omna"]["env"]["ZHIWO_AGENT_CREDENTIAL"]
        if client_id == "claude-code":
            data = json.loads((self.home / ".claude.json").read_text(encoding="utf-8"))
            return data["mcpServers"]["omna"]["env"]["ZHIWO_AGENT_CREDENTIAL"]
        text = (self.home / ".codex" / "config.toml").read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.startswith("ZHIWO_AGENT_CREDENTIAL"):
                return line.split("=", 1)[1].strip().strip('"')
        raise AssertionError("missing codex credential")

    def _hash(self, agent_id: str) -> str:
        connection = sqlite3.connect(self.db)
        try:
            return connection.execute("SELECT credential_hash FROM agents WHERE id = ?", (agent_id,)).fetchone()[0]
        finally:
            connection.close()

    def _agent_count(self) -> int:
        connection = sqlite3.connect(self.db)
        try:
            return connection.execute("SELECT COUNT(*) FROM agents").fetchone()[0]
        finally:
            connection.close()

    def _permissions(self, agent_id: str) -> dict:
        connection = sqlite3.connect(self.db)
        try:
            row = connection.execute(
                "SELECT allowed_tools, allowed_categories FROM agent_permissions WHERE agent_id = ?",
                (agent_id,),
            ).fetchone()
        finally:
            connection.close()
        return {"allowed_tools": json.loads(row[0]), "allowed_categories": json.loads(row[1])}


def main() -> None:
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ClientConnectTest)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    payload = {
        "pass": result.wasSuccessful(),
        "tests": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "clients": ["workbuddy", "zcode", "opencode", "codex", "claude", "claude-code"],
    }
    RESULT.parent.mkdir(parents=True, exist_ok=True)
    RESULT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
