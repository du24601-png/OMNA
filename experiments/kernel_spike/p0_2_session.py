"""P0.2: version sessions, derived-table cleanup, and the write cap.

Stops the session route when ids collide or one recall cannot see them.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

SPIKE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = SPIKE_ROOT.parents[1]
sys.path.insert(0, str(REPO_ROOT / "server"))

from p0_2_boundaries import SAMPLES, assert_native_windows, assert_python_pin, isolated_env
from zhiwo.adapters.derived_cleanup import DerivedCleanupError, cleanup_derived_rows
from zhiwo.adapters.kernel_session import kernel_session
from zhiwo.adapters.retention import CapacityExceeded, evictable_count, remember_within_cap

RUNS = SPIKE_ROOT / "runs" / "p0_2_session"
RESULT_PATH = SPIKE_ROOT / "results" / "p0_2_session.json"
SAME = SAMPLES["same_body"]
VERSION_A = "偏好版本甲。标记 ZW-P02-VER-A。"
VERSION_B = "偏好版本乙。标记 ZW-P02-VER-B。"
PAIR = "同正文删除对照，含 250ms。标记 ZW-P02-DEL-PAIR。"
LIMIT = 1_000_000


def connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def open_memory(data_dir: Path, session_id: str):
    os.environ["MNEMOSYNE_DATA_DIR"] = str(data_dir)
    os.environ["MNEMOSYNE_PERSONA_FILE"] = str(data_dir / "persona.md")
    from mnemosyne import Mnemosyne

    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "mnemosyne.db"
    return Mnemosyne(session_id=session_id, db_path=db_path), db_path


def column(db_path: Path, memory_id: str, name: str):
    with connect(db_path) as connection:
        row = connection.execute(
            f"SELECT {name} FROM working_memory WHERE id = ?",
            (memory_id,),
        ).fetchone()
    return None if row is None else row[name]


def owned_rows(db_path: Path, kernel_id: str) -> dict[str, list[dict]]:
    found: dict[str, list[dict]] = {"gists": [], "memoria_facts": []}
    with connect(db_path) as connection:
        for table, column_name in (("gists", "memory_id"), ("memoria_facts", "source_memory_id")):
            present = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
                (table,),
            ).fetchone()
            if present is None:
                continue
            rows = connection.execute(
                f"SELECT * FROM {table} WHERE {column_name} = ?",
                (kernel_id,),
            ).fetchall()
            found[table] = [dict(row) for row in rows]
    return found


def other_residue(db_path: Path, kernel_id: str, marker: str) -> list[dict]:
    found = []
    approved = {"gists", "memoria_facts"}
    with connect(db_path) as connection:
        tables = [
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        ]
        for table in tables:
            if table in approved:
                continue
            columns = [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
            for record in connection.execute(f"SELECT * FROM {table}"):
                values = {name: record[name] for name in columns}
                blob = " ".join("" if value is None else str(value) for value in values.values())
                if kernel_id in blob or marker in blob:
                    found.append({"table": table, "row": values})
    return found


def run_identity(data_dir: Path) -> dict:
    daily_session = kernel_session("mem-daily", 1)
    formal_session = kernel_session("mem-formal", 1)
    daily_mem, db_path = open_memory(data_dir, daily_session)
    daily_id = remember_within_cap(
        daily_mem, db_path, daily_session, SAME, LIMIT, valid_until="2099-01-01T00:00:00"
    )
    formal_mem, _db_path = open_memory(data_dir, formal_session)
    formal_id = remember_within_cap(
        formal_mem, db_path, formal_session, SAME, LIMIT, valid_until="2098-06-01T00:00:00"
    )
    product = "mem-pref"
    version_ids = {}
    for revision, content in ((1, VERSION_A), (2, VERSION_B), (3, VERSION_A)):
        session_id = kernel_session(product, revision)
        mem, _db_path = open_memory(data_dir, session_id)
        version_ids[revision] = remember_within_cap(mem, db_path, session_id, content, LIMIT)
    reader, _db_path = open_memory(data_dir, "zhiwo:reader")
    recalled = [row.get("id") for row in reader.recall("ZW-P02-SAME-BODY", top_k=10)]
    version_reads = {
        str(revision): reader.get(memory_id).get("content") if reader.get(memory_id) else None
        for revision, memory_id in version_ids.items()
    }
    distinct_versions = len(set(version_ids.values())) == 3
    distinct_scenarios = daily_id != formal_id
    no_overwrite = (
        column(db_path, daily_id, "valid_until") == "2099-01-01T00:00:00"
        and column(db_path, formal_id, "valid_until") == "2098-06-01T00:00:00"
    )
    unified = daily_id in recalled and formal_id in recalled
    third_not_reused = version_ids[3] != version_ids[1]
    route_ok = distinct_versions and distinct_scenarios and no_overwrite and unified and third_not_reused
    return {
        "route_ok": route_ok,
        "daily_id": daily_id,
        "formal_id": formal_id,
        "daily_session": daily_session,
        "formal_session": formal_session,
        "valid_until_after_second_write": {
            "daily": column(db_path, daily_id, "valid_until"),
            "formal": column(db_path, formal_id, "valid_until"),
        },
        "version_ids": {str(revision): memory_id for revision, memory_id in version_ids.items()},
        "version_reads_from_one_reader": version_reads,
        "recall_ids_from_reader_session": recalled,
        "db_path": str(db_path),
    }


def payload(content: str, scenario: str, valid_until: str, memory_id: str, revision: int) -> dict:
    return {
        "content": content,
        "scenario": scenario,
        "valid_until": valid_until,
        "memory_id": memory_id,
        "revision": revision,
    }


def run_retry_write(data_dir: Path) -> dict:
    body = payload(SAMPLES["idempotent"], "日常回复", None, "mem-retry", 1)
    session_id = kernel_session(body["memory_id"], body["revision"])
    state = data_dir / "operation.json"
    data_dir.mkdir(parents=True, exist_ok=True)
    state.write_text(
        json.dumps({"operation_id": "op-retry", "payload": body, "status": "prepared"}, ensure_ascii=False),
        encoding="utf-8",
    )
    mem, db_path = open_memory(data_dir, session_id)
    remember_within_cap(mem, db_path, session_id, body["content"], LIMIT, valid_until=body["valid_until"])
    sys.stdout.flush()
    os._exit(86)


def run_retry_conflict(data_dir: Path) -> dict:
    state = json.loads((data_dir / "operation.json").read_text(encoding="utf-8"))
    stored = state["payload"]
    session_id = kernel_session(stored["memory_id"], stored["revision"])
    _mem, db_path = open_memory(data_dir, session_id)
    attempts = {
        "content": payload("另一句。标记 ZW-P02-CONFLICT。", stored["scenario"], stored["valid_until"], stored["memory_id"], stored["revision"]),
        "scenario": payload(stored["content"], "正式报告", stored["valid_until"], stored["memory_id"], stored["revision"]),
        "valid_until": payload(stored["content"], stored["scenario"], "2099-01-01T00:00:00", stored["memory_id"], stored["revision"]),
        "revision": payload(stored["content"], stored["scenario"], stored["valid_until"], stored["memory_id"], 2),
    }
    conflicts = {name: attempt != stored for name, attempt in attempts.items()}
    with connect(db_path) as connection:
        rows = connection.execute(
            "SELECT COUNT(*) FROM working_memory WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0]
    return {
        "status_before_retry": state["status"],
        "conflicts": conflicts,
        "remember_called": False,
        "rows_in_original_session": rows,
    }


def run_retry_resume(data_dir: Path) -> dict:
    state_path = data_dir / "operation.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    stored = state["payload"]
    session_id = kernel_session(stored["memory_id"], stored["revision"])
    mem, db_path = open_memory(data_dir, session_id)
    kernel_id = remember_within_cap(
        mem, db_path, session_id, stored["content"], LIMIT, valid_until=stored["valid_until"]
    )
    state["status"] = "published"
    state["kernel_id"] = kernel_id
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    with connect(db_path) as connection:
        rows = connection.execute(
            "SELECT COUNT(*) FROM working_memory WHERE session_id = ? AND content = ?",
            (session_id, stored["content"]),
        ).fetchone()[0]
    return {"kernel_id": kernel_id, "rows": rows, "status": state["status"]}


def run_delete_setup(data_dir: Path) -> dict:
    left_session = kernel_session("mem-keep", 1)
    right_session = kernel_session("mem-drop", 1)
    left_mem, db_path = open_memory(data_dir, left_session)
    left_id = remember_within_cap(left_mem, db_path, left_session, PAIR, LIMIT, valid_until="2099-01-01T00:00:00")
    right_mem, _db_path = open_memory(data_dir, right_session)
    right_id = remember_within_cap(right_mem, db_path, right_session, PAIR, LIMIT, valid_until="2097-01-01T00:00:00")
    state = {
        "drop_id": right_id,
        "keep_id": left_id,
        "drop_session": right_session,
        "keep_session": left_session,
        "status": "written",
    }
    (data_dir / "delete.json").write_text(json.dumps(state), encoding="utf-8")
    return {
        "drop_id": right_id,
        "keep_id": left_id,
        "drop_owned_before": owned_rows(db_path, right_id),
        "keep_owned_before": owned_rows(db_path, left_id),
    }


def run_delete_forget(data_dir: Path) -> dict:
    state_path = data_dir / "delete.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    mem, db_path = open_memory(data_dir, state["drop_session"])
    forgotten = mem.forget(state["drop_id"])
    state["status"] = "cleanup_pending"
    state["forgotten"] = bool(forgotten)
    state_path.write_text(json.dumps(state), encoding="utf-8")
    sys.stdout.flush()
    os._exit(86)


def run_delete_resume(data_dir: Path) -> dict:
    state_path = data_dir / "delete.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    _mem, db_path = open_memory(data_dir, state["drop_session"])
    body_gone = column(db_path, state["drop_id"], "content") is None
    cleaned = cleanup_derived_rows(db_path, state["drop_id"])
    again = cleanup_derived_rows(db_path, state["drop_id"])
    keep_mem, _db_path = open_memory(data_dir, "zhiwo:reader")
    kept = keep_mem.get(state["keep_id"])
    state["status"] = "deleted"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    return {
        "body_already_gone": body_gone,
        "cleaned": cleaned,
        "second_cleanup": again,
        "drop_owned_after": owned_rows(db_path, state["drop_id"]),
        "keep_content": None if kept is None else kept.get("content"),
        "keep_valid_until": column(db_path, state["keep_id"], "valid_until"),
        "keep_owned_after": owned_rows(db_path, state["keep_id"]),
        "other_residue_for_dropped_id": other_residue(db_path, state["drop_id"], "ZW-P02-DEL-PAIR"),
    }


def run_capacity(data_dir: Path) -> dict:
    session_id = kernel_session("mem-cap", 1)
    mem, db_path = open_memory(data_dir, session_id)
    from mnemosyne.core.beam import WORKING_MEMORY_MAX_ITEMS

    first = remember_within_cap(mem, db_path, session_id, "容量一。标记 ZW-P02-CAP-A。", 2)
    second = remember_within_cap(mem, db_path, session_id, "容量二。标记 ZW-P02-CAP-B。", 2)
    refused = False
    try:
        remember_within_cap(mem, db_path, session_id, "容量三。标记 ZW-P02-CAP-C。", 2)
    except CapacityExceeded:
        refused = True
    present = {
        "first": mem.get(first) is not None and mem.get(first).get("content", "").startswith("容量一"),
        "second": mem.get(second) is not None and mem.get(second).get("content", "").startswith("容量二"),
    }
    separate = []
    for revision in (1, 2, 3):
        other = kernel_session("mem-separate", revision)
        other_mem, _db_path = open_memory(data_dir, other)
        separate.append(
            remember_within_cap(other_mem, db_path, other, f"分会话 {revision}。标记 ZW-P02-SEP-{revision}。", 2)
        )
    return {
        "constant": WORKING_MEMORY_MAX_ITEMS,
        "count_at_refusal": evictable_count(db_path, session_id),
        "refused_third": refused,
        "first_two_intact": present,
        "separate_sessions_kept": len(separate) == 3,
    }


def run_schema_guard(data_dir: Path) -> dict:
    data_dir.mkdir(parents=True, exist_ok=True)
    empty = data_dir / "empty.db"
    sqlite3.connect(empty).close()
    try:
        cleanup_derived_rows(empty, "missing-id")
    except DerivedCleanupError as exc:
        return {"rejected": True, "error": str(exc)}
    return {"rejected": False}


def spawn(mode: str, data_dir: Path, extra: dict[str, str] | None = None) -> dict:
    completed = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), mode, str(data_dir)],
        cwd=str(SPIKE_ROOT),
        env=isolated_env(
            data_dir,
            {
                "MNEMOSYNE_WM_TTL_HOURS": "1000000",
                "MNEMOSYNE_WM_MAX_ITEMS": "1000000",
                "MNEMOSYNE_PROACTIVE_LINKING": "0",
                **(extra or {}),
            },
        ),
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    if completed.stdout.strip():
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError:
            payload = {"raw": completed.stdout[-2000:]}
    else:
        payload = {}
    payload["exit_code"] = completed.returncode
    if completed.returncode not in (0, 86):
        payload["stderr"] = completed.stderr[-4000:]
    return payload


def main() -> int:
    if len(sys.argv) == 3:
        assert_native_windows()
        mode = sys.argv[1]
        data_dir = Path(sys.argv[2])
        runners = {
            "identity": run_identity,
            "retry-write": run_retry_write,
            "retry-conflict": run_retry_conflict,
            "retry-resume": run_retry_resume,
            "delete-setup": run_delete_setup,
            "delete-forget": run_delete_forget,
            "delete-resume": run_delete_resume,
            "capacity": run_capacity,
            "schema-guard": run_schema_guard,
        }
        emit(runners[mode](data_dir))
        return 0

    assert_native_windows()
    assert_python_pin()
    if RUNS.exists():
        import shutil

        shutil.rmtree(RUNS)
    identity = spawn("identity", RUNS / "identity")
    retry_dir = RUNS / "retry"
    delete_dir = RUNS / "delete"
    result = {
        "task": "P0.2-session",
        "identity": identity,
        "retry": {
            "crashed": spawn("retry-write", retry_dir),
            "conflict": spawn("retry-conflict", retry_dir),
            "resumed": spawn("retry-resume", retry_dir),
        },
        "delete": {
            "setup": spawn("delete-setup", delete_dir),
            "interrupted": spawn("delete-forget", delete_dir),
            "resumed": spawn("delete-resume", delete_dir),
        },
        "capacity": spawn("capacity", RUNS / "capacity", {"MNEMOSYNE_WM_MAX_ITEMS": "2"}),
        "schema_guard": spawn("schema-guard", RUNS / "schema"),
    }
    route_ok = bool(identity.get("route_ok"))
    result["session_route"] = "PASS" if route_ok else "STOP"
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"session_route": result["session_route"], "result": str(RESULT_PATH)}, ensure_ascii=False))
    return 0 if route_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
