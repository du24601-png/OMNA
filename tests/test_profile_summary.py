from __future__ import annotations

import sqlite3
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from zhiwo.api.errors import ApiError
from zhiwo.repositories.migrate import migrate
from zhiwo.services.profile_summary import (
    clear_profile_summary,
    generate_profile_summary,
    get_profile_summary,
)


def test_profile_summary_cache_is_a_single_derived_record() -> None:
    db_path = Path(__file__).parent / ".profile-summary-test.db"
    db_path.unlink(missing_ok=True)
    try:
        assert migrate(db_path) == 7

        connection = sqlite3.connect(db_path)
        try:
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(profile_summary)")
            }
            assert columns == {
                "id",
                "text",
                "input_hash",
                "generated_at",
                "model",
                "memory_count",
            }
        finally:
            connection.close()
    finally:
        db_path.unlink(missing_ok=True)


def test_summary_uses_current_memories_and_never_overwrites_after_a_change() -> None:
    db_path = Path(__file__).parent / ".profile-summary-service-test.db"
    db_path.unlink(missing_ok=True)
    try:
        migrate(db_path)
        connection = sqlite3.connect(db_path)
        try:
            rows = [
                ("mem-1", 1, "kernel-1", "session-1", "preference", "正式报告", "active", None),
                ("mem-2", 1, "kernel-2", "session-2", "goal", None, "active", "2020-01-01T00:00:00+00:00"),
                ("mem-3", 1, "kernel-3", "session-3", "project", None, "superseded", None),
            ]
            for memory_id, revision, kernel_id, session, category, scope, lifecycle, valid_until in rows:
                connection.execute(
                    """
                    INSERT INTO memory_refs (
                        memory_id, revision, kernel_id, kernel_session, kind, category,
                        scope, lifecycle, share_enabled, valid_until, created_at
                    ) VALUES (?, ?, ?, ?, 'fact', ?, ?, ?, 1, ?, '2026-09-27T00:00:00+00:00')
                    """,
                    (memory_id, revision, kernel_id, session, category, scope, lifecycle, valid_until),
                )
            connection.commit()
        finally:
            connection.close()

        handle = SimpleNamespace(db_path=Path("ignored-mnemosyne.db"))
        settings = SimpleNamespace(
            extractor_configured=True,
            extractor_base_url="https://example.invalid/v1",
            extractor_model="summary-model",
            extractor_api_key="secret",
        )
        bodies = {"kernel-1": "正式报告需要详细论据", "kernel-2": "已经过期", "kernel-3": "旧项目"}
        captured: list[dict] = []

        def fake_model(_settings, _system_prompt: str, user_payload: str) -> str:
            captured.append(json.loads(user_payload))
            return "用户在正式报告中偏好详细论据。"

        with patch("zhiwo.services.profile_summary.read_version", side_effect=lambda _db, _session, kid: bodies[kid]):
            empty = get_profile_summary(db_path, handle, extractor_configured=True)
            assert empty["status"] == "empty"
            assert empty["current_memory_count"] == 1

            generated = generate_profile_summary(db_path, handle, settings, call_model=fake_model)
            assert generated["status"] == "current"
            assert generated["memory_count"] == 1
            assert captured[0]["previous_summary"] == ""
            assert [item["id"] for item in captured[0]["current_memories"]] == ["mem-1"]

            connection = sqlite3.connect(db_path)
            try:
                connection.execute("UPDATE memory_refs SET scope = '研究报告' WHERE memory_id = 'mem-1'")
                connection.commit()
            finally:
                connection.close()

            stale = get_profile_summary(db_path, handle, extractor_configured=True)
            assert stale["status"] == "stale"
            assert stale["text"] == "用户在正式报告中偏好详细论据。"

            def changing_model(_settings, _system_prompt: str, user_payload: str) -> str:
                captured.append(json.loads(user_payload))
                connection = sqlite3.connect(db_path)
                try:
                    connection.execute("UPDATE memory_refs SET scope = '董事会报告' WHERE memory_id = 'mem-1'")
                    connection.commit()
                finally:
                    connection.close()
                return "这份结果已经过时。"

            try:
                generate_profile_summary(db_path, handle, settings, call_model=changing_model)
            except ApiError as exc:
                assert exc.status == 409
                assert exc.code == "CONFLICT"
                assert exc.message == "生成期间记忆有变化，原摘要没有被覆盖。"
            else:
                raise AssertionError("a summary generated from stale input was saved")

            assert captured[1]["previous_summary"] == "用户在正式报告中偏好详细论据。"
            after_conflict = get_profile_summary(db_path, handle, extractor_configured=True)
            assert after_conflict["text"] == "用户在正式报告中偏好详细论据。"
            assert after_conflict["status"] == "stale"

            clear_profile_summary(db_path)
            assert get_profile_summary(db_path, handle, extractor_configured=True)["status"] == "empty"
    finally:
        db_path.unlink(missing_ok=True)
