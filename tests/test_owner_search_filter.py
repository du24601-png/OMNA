"""Current-search qualification counts a hit only after category and expiry.

Owner search still returns unshared memories. This does not start the service.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from zhiwo.services.search import qualify_hits

NOW = datetime(2026, 9, 25, tzinfo=timezone.utc)


def _ref(kernel_id: str, category: str, *, valid_until: str | None = None, share_enabled: int = 1) -> dict:
    return {
        "memory_id": f"mem-{kernel_id}",
        "revision": 1,
        "kernel_id": kernel_id,
        "kind": "fact",
        "category": category,
        "scope": None,
        "valid_until": valid_until,
        "share_enabled": share_enabled,
    }


class QualifyHitsTest(unittest.TestCase):
    def test_category_and_expiry_do_not_consume_the_limit(self) -> None:
        published = {
            "expired": _ref("expired", "preference", valid_until="2020-01-01T00:00:00+00:00"),
            "other": _ref("other", "identity"),
            "first": _ref("first", "preference"),
            "second": _ref("second", "preference"),
            "private": _ref("private", "preference", share_enabled=0),
        }
        recall = [
            {"id": "missing", "content": "未发布的候选不应出现"},
            {"id": "expired", "content": "已经过期的偏好"},
            {"id": "other", "content": "身份不是这次的主题"},
            {"id": "first", "content": "当前偏好一"},
            {"id": "second", "content": "当前偏好二"},
            {"id": "private", "content": "仅自己可见的偏好"},
        ]
        found = qualify_hits(recall, published, category="preference", now=NOW, limit=1)
        self.assertEqual([item["id"] for item in found["items"]], ["mem-first"])
        self.assertTrue(found["truncated"])
        self.assertEqual(found["items"][0]["content"], "当前偏好一")

    def test_later_category_survives_a_full_window_of_other_categories(self) -> None:
        published = {f"id-{index}": _ref(f"id-{index}", "identity") for index in range(20)}
        published["goal"] = _ref("goal", "goal")
        recall = [{"id": f"id-{index}", "content": f"身份 {index}"} for index in range(20)]
        recall.append({"id": "goal", "content": "转去做产品"})
        found = qualify_hits(recall, published, category="goal", now=NOW, limit=20)
        self.assertEqual([item["content"] for item in found["items"]], ["转去做产品"])
        self.assertFalse(found["truncated"])

    def test_owner_search_keeps_unshared_memories(self) -> None:
        published = {"private": _ref("private", "preference", share_enabled=0)}
        found = qualify_hits(
            [{"id": "private", "content": "仅自己可见"}],
            published,
            category=None,
            now=NOW,
            limit=10,
        )
        self.assertEqual(found["items"][0]["content"], "仅自己可见")
        self.assertFalse(found["items"][0]["share_enabled"])
        self.assertFalse(found["truncated"])


if __name__ == "__main__":
    unittest.main()
