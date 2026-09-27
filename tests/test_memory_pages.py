"""The owner memory list continues by creation time instead of stopping at 50.

Uses a temporary database. Does not read the real library.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from zhiwo.api.errors import ApiError
from zhiwo.repositories.migrate import migrate
from zhiwo.services import memories as memories_module
from zhiwo.services.memories import list_memories


class MemoryPageTest(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self.directory = Path(tempfile.mkdtemp(prefix="zhiwo-pages-"))
        self.db = self.directory / "zhiwo.db"
        migrate(self.db)
        self.handle = type("Handle", (), {"db_path": self.db})()
        self.reads: list[str] = []
        self.original = memories_module.read_version
        memories_module.read_version = self._read

    def tearDown(self) -> None:
        import shutil

        memories_module.read_version = self.original
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_pages_follow_creation_time_and_read_only_the_page(self) -> None:
        self._memory("early", "2026-09-25T00:00:00+00:00")
        self._memory("middle", "2026-09-26T00:00:00+00:00")
        self._memory("late", "2026-09-27T00:00:00+00:00")
        self.reads.clear()

        first = list_memories(self.db, self.handle, limit=2)
        self.assertEqual([item["id"] for item in first["items"]], ["late", "middle"])
        self.assertEqual(first["total"], 3)
        self.assertTrue(first["next_cursor"])
        self.assertEqual(self.reads, ["kernel-late-1", "kernel-middle-1"])

        self.reads.clear()
        second = list_memories(self.db, self.handle, limit=2, cursor=first["next_cursor"])
        self.assertEqual([item["id"] for item in second["items"]], ["early"])
        self.assertEqual(second["total"], 3)
        self.assertIsNone(second["next_cursor"])
        self.assertEqual(self.reads, ["kernel-early-1"])

        oldest = list_memories(self.db, self.handle, limit=2, sort="oldest")
        self.assertEqual([item["id"] for item in oldest["items"]], ["early", "middle"])
        newer = list_memories(self.db, self.handle, limit=2, sort="oldest", cursor=oldest["next_cursor"])
        self.assertEqual([item["id"] for item in newer["items"]], ["late"])

    def test_equal_times_stay_in_id_order_across_pages(self) -> None:
        stamp = "2026-09-26T00:00:00+00:00"
        self._memory("b", stamp)
        self._memory("a", stamp)
        self._memory("c", stamp)
        first = list_memories(self.db, self.handle, limit=2, sort="newest")
        self.assertEqual([item["id"] for item in first["items"]], ["a", "b"])
        second = list_memories(self.db, self.handle, limit=2, cursor=first["next_cursor"])
        self.assertEqual([item["id"] for item in second["items"]], ["c"])

    def test_category_filter_does_not_read_other_rows(self) -> None:
        self._memory("pref", "2026-09-26T03:00:00+00:00", category="preference")
        self._memory("goal", "2026-09-26T02:00:00+00:00", category="goal")
        self.reads.clear()
        listed = list_memories(self.db, self.handle, category="preference", limit=50)
        self.assertEqual([item["id"] for item in listed["items"]], ["pref"])
        self.assertEqual(listed["total"], 1)
        self.assertIsNone(listed["next_cursor"])
        self.assertEqual(self.reads, ["kernel-pref-1"])

    def test_history_revisions_page_separately(self) -> None:
        self._memory("same", "2026-09-26T00:00:00+00:00", lifecycle="superseded", revision=1)
        self._memory("same", "2026-09-27T00:00:00+00:00", lifecycle="superseded", revision=2)
        first = list_memories(self.db, self.handle, state="history", limit=1)
        self.assertEqual([(item["id"], item["revision"]) for item in first["items"]], [("same", 2)])
        second = list_memories(self.db, self.handle, state="history", limit=1, cursor=first["next_cursor"])
        self.assertEqual([(item["id"], item["revision"]) for item in second["items"]], [("same", 1)])

    def test_invalid_cursor_and_sort_are_rejected(self) -> None:
        with self.assertRaises(ApiError) as cursor:
            list_memories(self.db, self.handle, cursor="not-a-cursor")
        self.assertEqual(cursor.exception.code, "VALIDATION_ERROR")
        with self.assertRaises(ApiError) as sort:
            list_memories(self.db, self.handle, sort="alpha")
        self.assertEqual(sort.exception.code, "VALIDATION_ERROR")

    def _read(self, _db_path, _session, kernel_id):
        self.reads.append(kernel_id)
        return "一条记忆"

    def _memory(self, memory_id: str, created_at: str, *, category: str = "preference", lifecycle: str = "active", revision: int = 1) -> None:
        connection = sqlite3.connect(self.db)
        try:
            connection.execute(
                """
                INSERT INTO memory_refs (
                    memory_id, revision, kernel_id, kernel_session, kind, category,
                    lifecycle, share_enabled, source_refs, created_at
                ) VALUES (?, ?, ?, ?, 'fact', ?, ?, 0, ?, ?)
                """,
                (
                    memory_id,
                    revision,
                    f"kernel-{memory_id}-{revision}",
                    f"session-{memory_id}-{revision}",
                    category,
                    lifecycle,
                    json.dumps([]),
                    created_at,
                ),
            )
            connection.commit()
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
