"""MCP branding and legacy migration, using only temporary client homes."""

from __future__ import annotations

import json
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from zhiwo.services.client_connect import PROFILES, _config_path, _write_profile, list_clients


class MCPNameTest(unittest.TestCase):
    def test_legacy_json_entry_is_replaced_and_other_servers_are_preserved(self):
        for profile in PROFILES:
            if profile.id == "codex":
                continue
            with self.subTest(client=profile.id), tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                path = _config_path(home, profile.id)
                path.parent.mkdir(parents=True, exist_ok=True)
                entries = {"zhiwo": {"command": "legacy"}, "other": {"command": "keep"}}
                if profile.id == "opencode":
                    original = {"mcp": entries}
                elif profile.id == "zcode":
                    original = {"mcp": {"servers": entries}}
                else:
                    original = {"mcpServers": entries}
                path.write_text(json.dumps(original), encoding="utf-8")
                self.assertTrue(self._configured(home, profile.id))
                launch = {"command": "python", "args": ["bridge"], "env": {}}
                _write_profile(home, path, profile.id, launch, "synthetic-credential")
                written = json.loads(path.read_text(encoding="utf-8"))
                servers = written.get("mcpServers", written.get("mcp"))
                if profile.id == "zcode":
                    servers = servers["servers"]
                self.assertEqual(set(servers), {"omna", "other"})
                self.assertEqual(servers["other"], {"command": "keep"})
                self.assertTrue(self._configured(home, profile.id))

    def test_codex_rewrite_handles_both_names_and_quoted_tables(self):
        for quote in ('', '"', "'"):
            with self.subTest(quote=quote), tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                path = _config_path(home, "codex")
                path.parent.mkdir(parents=True, exist_ok=True)
                legacy = f"{quote}zhiwo{quote}"
                current = f"{quote}omna{quote}"
                path.write_text(
                    'model = "keep"\n'
                    f'[mcp_servers.{legacy}]\ncommand = "legacy"\n'
                    f'[mcp_servers.{legacy}.env]\nOLD = "legacy-value"\n'
                    f'[mcp_servers.{current}]\ncommand = "outdated"\n'
                    f'[mcp_servers.{current}.env]\nOLD = "outdated-value"\n'
                    '[mcp_servers."omna.other"]\ncommand = "keep-other"\n'
                    '[mcp_servers.other]\ncommand = "keep"\n',
                    encoding="utf-8",
                )
                self.assertTrue(self._configured(home, "codex"))
                launch = {"command": "python", "args": ["bridge"], "env": {}}
                _write_profile(home, path, "codex", launch, "synthetic-credential")
                once = path.read_text(encoding="utf-8")
                servers = tomllib.loads(once)["mcp_servers"]
                self.assertEqual(set(servers), {"omna", "other", "omna.other"})
                self.assertEqual(servers["other"]["command"], "keep")
                self.assertEqual(servers["omna.other"]["command"], "keep-other")
                self.assertNotIn("OLD", servers["omna"]["env"])
                self.assertTrue(self._configured(home, "codex"))
                _write_profile(home, path, "codex", launch, "synthetic-credential")
                self.assertEqual(path.read_text(encoding="utf-8"), once)

    @staticmethod
    def _configured(home, client_id):
        return next(
            row["configured"]
            for row in list_clients(home, lookup=lambda _: None, packages=frozenset())["clients"]
            if row["id"] == client_id
        )


class MCPHandshakeNameTest(unittest.IsolatedAsyncioTestCase):
    async def test_real_stdio_handshake_reports_omna_and_four_tools(self):
        import anyio
        from mcp.client.session import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "zhiwo.gateway.stdio_bridge"],
            env={
                "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "server"),
                "ZHIWO_API_ORIGIN": "http://127.0.0.1:1",
                "ZHIWO_AGENT_CREDENTIAL": "synthetic-credential",
            },
        )
        with anyio.fail_after(20):
            async with stdio_client(params) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    initialized = await session.initialize()
                    self.assertEqual(initialized.server_info.name, "omna")
                    tools = await session.list_tools()
                    self.assertEqual(
                        {tool.name for tool in tools.tools},
                        {"get_context", "search_memory", "propose_memory", "explain_memory"},
                    )
