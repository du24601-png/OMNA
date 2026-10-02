"""v2: remember a batch's additions at once, undo a whole batch.

Real Mnemosyne kernel with the local embedding cache, a temporary data
directory, synthetic fixtures only. Writes tests/results/batches_windows.json.

Covers the acceptance lines: after "remember N" the library has exactly N
more memories; after "undo this import" it is back to before and no tool can
read those memories; an agent's organize session groups its additions; a
batch whose memories were changed afterwards is not undone.
"""

from __future__ import annotations

import json
import os
import platform
import sqlite3
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "server"))

from zhiwo.adapters.kernel_client import connect  # noqa: E402
from zhiwo.api.errors import ApiError  # noqa: E402
from zhiwo.config import load_settings  # noqa: E402
from zhiwo.contracts.memory import CATEGORIES, TOOLS  # noqa: E402
from zhiwo.repositories.migrate import migrate, put_setting  # noqa: E402
from zhiwo.services.agent_tools import get_context, propose_memory, search_memory  # noqa: E402
from zhiwo.services.agents import create_agent, update_agent  # noqa: E402
from zhiwo.services.batches import accept_additions, batch_summary, start_organize, undo_batch  # noqa: E402
from zhiwo.services.imports import import_source  # noqa: E402
from zhiwo.services.memories import current_texts, update_memory  # noqa: E402
from zhiwo.services.organize import SESSION_PREFIX  # noqa: E402
from zhiwo.services.publish import publish_memory  # noqa: E402
from zhiwo.services.review import list_proposals  # noqa: E402

RESULT_PATH = REPO / "tests" / "results" / "batches_windows.json"
CACHE = REPO / "experiments" / "kernel_spike" / "runs" / "p0_3" / "fastembed-cache"
FIXTURE = REPO / "fixtures" / "agent_files" / "claude_cn_structured.md"


def _active_count(db) -> int:
    connection = sqlite3.connect(db)
    try:
        return connection.execute("SELECT COUNT(*) FROM memory_refs WHERE lifecycle = 'active'").fetchone()[0]
    finally:
        connection.close()


def _expect(code: str, call) -> None:
    try:
        call()
    except ApiError as exc:
        assert exc.code == code, f"expected {code}, got {exc.code}: {exc.message}"
        return
    raise AssertionError(f"expected {code}")


def _prepare_env(raw: str) -> None:
    os.environ["ZHIWO_DATA_DIR"] = raw
    os.environ["ZHIWO_OWNER_CREDENTIAL"] = "batches-owner"
    os.environ["ZHIWO_FASTEMBED_CACHE_DIR"] = str(CACHE)
    os.environ["HF_HUB_OFFLINE"] = "1"
    for key in ("ZHIWO_KERNEL_CONNECT_ONLY", "ZHIWO_EXTRACTOR_API_KEY", "ZHIWO_EXTRACTOR_BASE_URL", "ZHIWO_EXTRACTOR_MODEL"):
        os.environ.pop(key, None)


def run() -> dict:
    result: dict = {"platform": platform.platform(), "python": platform.python_version(), "pass": False}
    started = time.time()
    with tempfile.TemporaryDirectory(prefix="zhiwo-batches-", ignore_cleanup_errors=True) as raw:
        _prepare_env(raw)
        settings = load_settings()
        result["schema_version"] = migrate(settings.control_db)
        kernel = connect(settings.kernel_dir, cache_dir=settings.fastembed_cache, connect_only=False)
        db = settings.control_db
        existing = lambda: current_texts(db, kernel)  # noqa: E731

        publish_memory(db, kernel, str(uuid.uuid4()), {"content": "测试框架用 Vitest，不用 Jest", "kind": "fact", "category": "preference", "share_enabled": True, "source_refs": []})
        before = _active_count(db)

        job = import_source(db, settings, str(uuid.uuid4()), {"kind": "file", "name": "CLAUDE.md", "text": FIXTURE.read_text(encoding="utf-8")}, existing=existing)
        batch = job["job_id"]
        summary = batch_summary(db, batch)
        assert summary["kind"] == "import" and summary["pending_additions"] == 14, summary
        assert summary["plain_additions"] == 13, summary
        excluded = next(item["id"] for item in job["proposals"] if "不确定" in item["payload"]["content"])
        accepted = accept_additions(db, kernel, batch, [excluded], existing)
        reasons = sorted(item["reason"] for item in accepted["results"] if item["status"] == "skipped")
        assert accepted["accepted"] == 12 and reasons == ["excluded", "similar"], accepted
        assert _active_count(db) == before + 12
        again = accept_additions(db, kernel, batch, [excluded], existing)
        assert again["accepted"] == 0 and _active_count(db) == before + 12, again
        result["import_batch"] = {"candidates": len(job["proposals"]), "accepted": 12, "skipped": reasons, "repeat_accepted": again["accepted"]}

        agent = create_agent(db, str(uuid.uuid4()), "合成连接")
        update_agent(db, agent["id"], str(uuid.uuid4()), {"allowed_tools": list(TOOLS), "allowed_categories": list(CATEGORIES)})
        secret = agent["credential"]
        found = search_memory(db, kernel, secret, "包管理用 pnpm")
        assert any("pnpm" in (item.get("content") or "") for item in found["items"]), found

        _expect("VALIDATION_ERROR", lambda: undo_batch(db, kernel, batch, None))
        import zhiwo.services.batches as batches_module

        real_delete = batches_module.delete_memory
        calls = {"n": 0}

        def flaky(*args):
            calls["n"] += 1
            if calls["n"] == 4:
                raise ApiError(503, "KERNEL_UNAVAILABLE", "synthetic failure", retryable=True)
            return real_delete(*args)

        batches_module.delete_memory = flaky
        _expect("KERNEL_UNAVAILABLE", lambda: undo_batch(db, kernel, batch, True))
        batches_module.delete_memory = real_delete
        assert _active_count(db) == before + 9, _active_count(db)
        undone = undo_batch(db, kernel, batch, True)
        assert undone["deleted"] == 9 and _active_count(db) == before, undone
        texts = [item["content"] for item in current_texts(db, kernel)]
        assert texts == ["测试框架用 Vitest，不用 Jest"], texts
        for query in ("包管理用 pnpm", "纸鸢", "独立开发者"):
            for call in (search_memory, get_context):
                items = call(db, kernel, secret, query)["items"]
                assert all("Vitest" in (item.get("content") or "") for item in items), (query, items)
        _expect("NOT_FOUND", lambda: batch_summary(db, batch))
        assert not [item for item in list_proposals(db)["proposals"] if item["batch_id"] == batch]
        result["import_undo"] = {"interrupted_after_deleting": 3, "resumed_and_deleted": undone["deleted"], "active_after": _active_count(db), "readable_after": 0, "source_and_leftover_candidates_removed": True}

        reader = create_agent(db, str(uuid.uuid4()), "只读连接")
        update_agent(db, reader["id"], str(uuid.uuid4()), {"allowed_tools": ["get_context", "search_memory"], "allowed_categories": ["preference"]})
        _expect("CONFLICT", lambda: start_organize(db, reader["id"]))
        session = start_organize(db, agent["id"])
        assert "propose_memory" in session["prompt"]
        target = publish_memory(db, kernel, str(uuid.uuid4()), {"content": "在杭州", "kind": "fact", "category": "identity", "share_enabled": True, "source_refs": []})
        for content in ("周报在周五下午写", "开会前先发议程"):
            propose_memory(db, kernel, secret, None, {"type": "add", "content": content, "kind": "fact", "category": "preference"}, {"text": content})
        propose_memory(db, kernel, secret, None, {"type": "update", "content": "在杭州，时区 UTC+8", "kind": "fact", "category": "identity", "target_id": target["memory_id"], "base_revision": 1}, {"text": "在杭州，时区 UTC+8"})
        organized = batch_summary(db, session["batch_id"])
        assert organized["kind"] == "organize" and organized["pending_additions"] == 2 and organized["pending"] == 2, organized
        listed = list_proposals(db, status="pending", since=session["started_at"])["proposals"]
        assert len(listed) == 3 and sum(1 for item in listed if item["batch_id"] == session["batch_id"]) == 2, listed
        count = _active_count(db)
        took = accept_additions(db, kernel, session["batch_id"], [], existing)
        assert took["accepted"] == 2 and _active_count(db) == count + 2, took
        changed = next(item for item in took["results"] if item["status"] == "accepted")
        update_memory(db, kernel, changed["memory_id"], str(uuid.uuid4()), {"content": "周报在周五下午四点前写完", "kind": "fact", "category": "preference", "share_enabled": True, "source_refs": [], "base_revision": 1})
        _expect("CONFLICT", lambda: undo_batch(db, kernel, session["batch_id"], True))
        assert _active_count(db) == count + 2
        result["organize_batch"] = {"tagged_additions": 2, "update_left_out": True, "accepted": 2, "undo_after_edit": "CONFLICT"}

        put_setting(db, SESSION_PREFIX + agent["id"], json.dumps({"batch_id": str(uuid.uuid4()), "expires_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()}))
        late = propose_memory(db, kernel, secret, None, {"type": "add", "content": "下午不安排会议", "kind": "fact", "category": "preference"}, {"text": "下午不安排会议"})
        late_row = next(item for item in list_proposals(db)["proposals"] if item["id"] == late["proposal_id"])
        assert "batch_id" not in late_row["payload"], late_row
        result["expired_session_untagged"] = True
        _expect("VALIDATION_ERROR", lambda: list_proposals(db, since="not a time"))
    result["seconds"] = round(time.time() - started, 1)
    result["pass"] = True
    return result


if __name__ == "__main__":
    outcome: dict
    try:
        outcome = run()
    except Exception as exc:  # report the failure, then fail the process
        outcome = {"pass": False, "error": f"{type(exc).__name__}: {exc}"}
        RESULT_PATH.write_text(json.dumps(outcome, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        raise
    RESULT_PATH.write_text(json.dumps(outcome, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("OK", json.dumps(outcome, ensure_ascii=False))
