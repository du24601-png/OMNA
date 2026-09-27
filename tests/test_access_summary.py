"""Owner access list carries a clipped sentence, not the whole snapshot."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from zhiwo.services.access import _summary


def _row(payload: dict) -> dict:
    return {
        "id": "event",
        "request_id": "request",
        "agent_id": "agent",
        "tool": "search_memory",
        "outcome": "success",
        "policy_version": 1,
        "created_at": "2026-09-27T00:00:00+00:00",
        "delivery_state": "sent",
        "response_snapshot": json.dumps(payload, ensure_ascii=False),
    }


class AccessSummaryTest(unittest.TestCase):
    def test_returned_text_is_clipped_and_errors_are_not_sentences(self) -> None:
        view = _summary(
            _row(
                {
                    "items": [
                        {"id": "m", "revision": 1, "content": "  日常沟通时，\n我更喜欢简洁。  "},
                        {"id": "n", "revision": 2, "content": "甲" * 180},
                    ],
                    "error": {"message": "不应出现在句子里"},
                }
            )
        )
        self.assertEqual(view["returned"][0], "日常沟通时， 我更喜欢简洁。")
        self.assertEqual(view["returned"][1], "甲" * 160 + "…")
        self.assertNotIn("不应出现在句子里", "".join(view["returned"]))
        self.assertEqual(view["versions"], [{"id": "m", "revision": 1}, {"id": "n", "revision": 2}])

    def test_explain_fragment_is_a_returned_line(self) -> None:
        view = _summary(_row({"result": {"id": "m", "revision": 3, "evidence": "已确认的片段"}}))
        self.assertEqual(view["returned"], ["已确认的片段"])

    def test_proposal_without_text_returns_nothing(self) -> None:
        view = _summary(_row({"proposal_id": "p", "status": "pending"}))
        self.assertEqual(view["returned"], [])
