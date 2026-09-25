"""Qualified-row counting for search_published. No Kernel and no credentials."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1].parent / "server"))

from zhiwo.adapters.memory_search import search_published


def _record(memory_id: str, content: str) -> dict:
    return {
        "memory_id": memory_id,
        "revision": 1,
        "content": content,
        "scenario": "运动",
        "valid_until": "2099-01-01T00:00:00",
    }


class SearchFilterTests(unittest.TestCase):
    def test_unregistered_first_row_does_not_consume_limit(self) -> None:
        catalog = {"keep": _record("m-keep", "合格记忆")}
        rows = [
            {"id": "missing", "content": "未登记的候选"},
            {"id": "keep", "content": "合格记忆"},
        ]
        result = search_published(rows, catalog, "查询", limit=1)
        self.assertEqual([item["id"] for item in result["items"]], ["m-keep"])
        self.assertFalse(result["truncated"])

    def test_limit_counts_qualified_rows_and_marks_truncated(self) -> None:
        catalog = {
            "first": _record("m-first", "第一条合格"),
            "second": _record("m-second", "第二条合格"),
        }
        rows = [
            {"id": "first", "content": "第一条合格"},
            {"id": "second", "content": "第二条合格"},
        ]
        result = search_published(rows, catalog, "查询", limit=1)
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["id"], "m-first")
        self.assertTrue(result["truncated"])


if __name__ == "__main__":
    unittest.main()
