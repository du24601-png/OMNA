"""v2: import from agent instruction files, split by structure, dedupe.

Temporary home and control database, synthetic fixtures from
fixtures/agent_files, no Kernel. Existing memories are passed in as plain
rows, so near-duplicate marking is checked without an embedding model.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures" / "agent_files"
sys.path.insert(0, str(ROOT / "server"))

from zhiwo.api.errors import ApiError  # noqa: E402
from zhiwo.config import Settings  # noqa: E402
from zhiwo.repositories.migrate import migrate  # noqa: E402
from zhiwo.services.agent_files import list_agent_files  # noqa: E402
from zhiwo.services.imports import import_source, retry_import  # noqa: E402
from zhiwo.services.split import dedupe, normalize, split_markdown  # noqa: E402


class AgentFilesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="zhiwo-agent-files-"))
        self.home = self.folder / "home"
        self.home.mkdir()
        self.db = self.folder / "zhiwo.db"
        migrate(self.db)
        self.settings = Settings(
            data_dir=self.folder,
            owner_credential="test",
            connect_only=True,
            fastembed_cache=self.folder / "cache",
            extractor_base_url="",
            extractor_model="",
            extractor_api_key="",
            test_mode=True,
        )
        self.env = {key: os.environ.get(key) for key in ("ZHIWO_TEST_MODE", "ZHIWO_CLIENT_HOME")}
        os.environ["ZHIWO_TEST_MODE"] = "1"
        os.environ["ZHIWO_CLIENT_HOME"] = str(self.home)

    def tearDown(self) -> None:
        for key, value in self.env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.folder, ignore_errors=True)

    def _place(self, fixture: str, *parts: str) -> Path:
        target = self.home.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FIXTURES / fixture, target)
        return target

    def _import(self, file_id: str, existing=None) -> dict:
        return import_source(self.db, self.settings, str(uuid.uuid4()), {"kind": "agent_file", "file_id": file_id}, existing=existing)

    def test_listing_is_whitelist_only_and_reads_nothing(self) -> None:
        self._place("claude_cn_structured.md", ".claude", "CLAUDE.md")
        self._place("zcode_rules.md", ".zcode", "AGENTS.md")
        self._place("codex_mixed.md", ".claude", "notes.md")
        self._place("codex_mixed.md", "Documents", "AGENTS.md")
        listed = list_agent_files(self.db)["files"]
        self.assertEqual([item["id"] for item in listed], ["claude-code", "zcode"])
        self.assertEqual(listed[0]["path"], "~/.claude/CLAUDE.md")
        self.assertEqual([client["id"] for client in listed[0]["clients"]], ["claude-code", "opencode"])
        self.assertIsNone(listed[0]["imported_at"])
        encoded = json.dumps(listed, ensure_ascii=False)
        self.assertNotIn("独立开发者", encoded)

    def test_thirty_line_file_splits_one_candidate_per_item(self) -> None:
        path = self._place("claude_cn_structured.md", ".claude", "CLAUDE.md")
        before = path.stat()
        original = path.read_bytes()
        job = self._import("claude-code")
        after = path.stat()
        self.assertEqual(len(original.decode("utf-8").splitlines()), 30)
        self.assertEqual(job["status"], "extracted")
        self.assertEqual(job["method"], "split")
        self.assertEqual(job["name"], "~/.claude/CLAUDE.md")
        list_items = [line for line in original.decode("utf-8").splitlines() if line.startswith("- ")]
        self.assertEqual(len(job["proposals"]), len(list_items))
        text = original.decode("utf-8")
        for proposal in job["proposals"]:
            self.assertIn(proposal["evidence"]["text"], text)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(after.st_mtime_ns, before.st_mtime_ns)
        self.assertIsNotNone(list_agent_files(self.db)["files"][0]["imported_at"])

    def test_identical_lines_merge(self) -> None:
        self._place("dup_heavy.md", ".config", "opencode", "AGENTS.md")
        job = self._import("opencode")
        contents = [proposal["payload"]["content"] for proposal in job["proposals"]]
        self.assertEqual(len(contents), 4)
        self.assertEqual(job["merged_duplicates"], 4)
        self.assertEqual(len({normalize(content) for content in contents}), 4)

    def test_existing_memory_drops_exact_and_marks_near(self) -> None:
        self._place("opencode_minimal.md", ".zcode", "AGENTS.md")
        existing = [
            {"id": "m-1", "content": "文档用飞书写，不用 Notion。"},
            {"id": "m-2", "content": "正在负责会员体系的改版，计划 10 月底上线"},
        ]
        job = self._import("zcode", existing=lambda: existing)
        contents = [proposal["payload"]["content"] for proposal in job["proposals"]]
        self.assertNotIn("文档用飞书写，不用 Notion", contents)
        self.assertEqual(job["merged_duplicates"], 1)
        near = [proposal for proposal in job["proposals"] if proposal["payload"].get("similar_to")]
        self.assertEqual([item["payload"]["similar_to"]["memory_id"] for item in near], ["m-2"])

    def test_refuses_unknown_ids_and_mixed_fields(self) -> None:
        self._place("claude_cn_structured.md", ".claude", "CLAUDE.md")
        for bad in ("../.claude/CLAUDE.md", "C:/Windows/win.ini", "", None, "workbuddy"):
            with self.subTest(file_id=bad):
                with self.assertRaises(ApiError) as caught:
                    self._import(bad)
                self.assertEqual(caught.exception.code, "VALIDATION_ERROR")
        with self.assertRaises(ApiError):
            import_source(self.db, self.settings, str(uuid.uuid4()), {"kind": "agent_file", "file_id": "claude-code", "text": "x"})
        with self.assertRaises(ApiError):
            import_source(self.db, self.settings, str(uuid.uuid4()), {"kind": "paste", "text": "- 一条", "file_id": "claude-code"})
        with self.assertRaises(ApiError) as caught:
            self._import("zcode")
        self.assertEqual(caught.exception.code, "NOT_FOUND")

    def test_link_out_of_home_is_ignored(self) -> None:
        outside = self.folder / "outside.md"
        shutil.copyfile(FIXTURES / "zcode_rules.md", outside)
        link = self.home / ".zcode" / "AGENTS.md"
        link.parent.mkdir(parents=True)
        try:
            link.symlink_to(outside)
        except OSError:
            link.parent.rmdir()
            target = self.folder / "outside-dir"
            target.mkdir()
            shutil.copyfile(outside, target / "AGENTS.md")
            made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link.parent), str(target)], capture_output=True)
            if made.returncode != 0:
                self.skipTest("this account cannot create links")
        self.assertEqual(list_agent_files(self.db)["files"], [])
        with self.assertRaises(ApiError) as caught:
            self._import("zcode")
        self.assertEqual(caught.exception.code, "NOT_FOUND")

    def test_plain_paste_without_model_keeps_v1_behaviour(self) -> None:
        job = import_source(self.db, self.settings, str(uuid.uuid4()), {"kind": "paste", "text": "用户：我最近在学吉他。\n助手：不错。"})
        self.assertEqual(job["status"], "extractor_unavailable")
        self.assertEqual(job["proposals"], [])
        listed = import_source(self.db, self.settings, str(uuid.uuid4()), {"kind": "paste", "text": "我的偏好：\n- 回答简洁\n- 用中文"})
        self.assertEqual(listed["status"], "extracted")
        self.assertEqual([item["payload"]["content"] for item in listed["proposals"]], ["回答简洁", "用中文"])

    def test_retry_of_an_old_unstructured_job_stays_unavailable(self) -> None:
        job = import_source(self.db, self.settings, str(uuid.uuid4()), {"kind": "paste", "text": "只有一句话"})
        again = retry_import(self.db, self.settings, job["job_id"])
        self.assertEqual(again["status"], "extractor_unavailable")

    def test_nested_labels_and_code_blocks(self) -> None:
        nested = split_markdown((FIXTURES / "agents_nested.md").read_text(encoding="utf-8"))
        contents = [item["content"] for item in nested]
        self.assertIn("设计工具：主力用 Figma", contents)
        self.assertNotIn("设计工具：", contents)
        mixed = split_markdown((FIXTURES / "codex_mixed.md").read_text(encoding="utf-8"))
        self.assertFalse(any("pytest" in item["content"] for item in mixed))
        kept, merged = dedupe(mixed, [])
        self.assertEqual(merged, 1)


if __name__ == "__main__":
    unittest.main()
