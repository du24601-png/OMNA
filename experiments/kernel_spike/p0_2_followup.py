"""P0.2 follow-up: delete residue, capacity eviction, and operation identity.

Does not repeat the version, path, or TTL experiments.
Does not modify Kernel tables and does not add a second memory engine.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from p0_2_boundaries import (
    SAMPLES,
    SESSION_ID,
    assert_native_windows,
    assert_python_pin,
    isolated_env,
    remember,
)

SPIKE_ROOT = Path(__file__).resolve().parent
RUNS = SPIKE_ROOT / "runs" / "p0_2_followup"
RESULT_PATH = SPIKE_ROOT / "results" / "p0_2_followup.json"
OPERATION_ID = "11111111-1111-4111-8111-111111111111"
PRODUCT_ID = "22222222-2222-4222-8222-222222222222"


def connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def open_memory(data_dir: Path):
    os.environ["MNEMOSYNE_DATA_DIR"] = str(data_dir)
    os.environ["MNEMOSYNE_PERSONA_FILE"] = str(data_dir / "persona.md")
    from mnemosyne import Mnemosyne
    from mnemosyne.core import beam

    db_path = data_dir / "mnemosyne.db"
    mem = Mnemosyne(session_id=SESSION_ID, db_path=db_path)
    return mem, db_path, beam


def residue_rows(db_path: Path, memory_id: str, marker: str) -> list[dict]:
    found = []
    with connect(db_path) as connection:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        ]
        for table in tables:
            columns = [
                row[1]
                for row in connection.execute(f"PRAGMA table_info({table})")
            ]
            for record in connection.execute(f"SELECT * FROM {table}"):
                values = {column: record[column] for column in columns}
                blob = " ".join("" if value is None else str(value) for value in values.values())
                if memory_id in blob or marker in blob:
                    found.append({"table": table, "row": values})
    return found


def run_delete(data_dir: Path) -> dict:
    data_dir.mkdir(parents=True, exist_ok=True)
    mem, db_path, _beam = open_memory(data_dir)
    memory_id = remember(mem, SAMPLES["delete_me"])
    forgotten = mem.forget(memory_id)
    marker = "ZW-P02-DELETE"
    rows = residue_rows(db_path, memory_id, marker)
    import mnemosyne

    names = sorted(
        name
        for name in set(dir(mnemosyne)) | set(dir(mnemosyne.Mnemosyne))
        if any(part in name.lower() for part in ("forget", "purge", "memoria", "delete"))
    )
    recall = mem.recall("ZW-P02-DELETE", top_k=5)
    return {
        "forgotten": bool(forgotten),
        "get_after_forget": mem.get(memory_id),
        "kernel_id": memory_id,
        "residue": rows,
        "recall_after_forget": recall,
        "public_names": names,
        "extract_flag_disables_memoria": False,
    }


def run_cap(data_dir: Path, guarded: bool) -> dict:
    data_dir.mkdir(parents=True, exist_ok=True)
    mem, db_path, beam = open_memory(data_dir)
    limit = beam.WORKING_MEMORY_MAX_ITEMS
    texts = [
        "容量样本一。标记 ZW-P02-CAP-1。",
        "容量样本二。标记 ZW-P02-CAP-2。",
        "容量样本三。标记 ZW-P02-CAP-3。",
    ]
    ids = [remember(mem, texts[0]), remember(mem, texts[1])]
    refused = False
    if guarded:
        with connect(db_path) as connection:
            count = connection.execute(
                """
                SELECT COUNT(*) FROM working_memory
                WHERE session_id = ? AND consolidated_at IS NULL
                """,
                (SESSION_ID,),
            ).fetchone()[0]
        if count >= limit:
            refused = True
        else:
            ids.append(remember(mem, texts[2]))
    else:
        ids.append(remember(mem, texts[2]))
    surviving = []
    for text in texts:
        with connect(db_path) as connection:
            row = connection.execute(
                "SELECT id, content FROM working_memory WHERE content = ?",
                (text,),
            ).fetchone()
        surviving.append({"content": text, "present": row is not None, "id": None if row is None else row["id"]})
    return {
        "max_items_constant": limit,
        "env_at_process_start": os.environ.get("MNEMOSYNE_WM_MAX_ITEMS"),
        "guarded": guarded,
        "refused_third": refused,
        "surviving": surviving,
        "ids": ids,
    }


def run_cap_late(data_dir: Path) -> dict:
    data_dir.mkdir(parents=True, exist_ok=True)
    _mem, _db_path, beam = open_memory(data_dir)
    before = beam.WORKING_MEMORY_MAX_ITEMS
    os.environ["MNEMOSYNE_WM_MAX_ITEMS"] = "2"
    return {
        "constant_after_import": before,
        "env_mutated_after_import": os.environ["MNEMOSYNE_WM_MAX_ITEMS"],
        "constant_unchanged": beam.WORKING_MEMORY_MAX_ITEMS == before,
    }


def run_cap_read(data_dir: Path) -> dict:
    mem, db_path, _beam = open_memory(data_dir)
    with connect(db_path) as connection:
        before = connection.execute("SELECT COUNT(*) FROM working_memory").fetchone()[0]
    rows = []
    with connect(db_path) as connection:
        stored = list(connection.execute("SELECT id, content FROM working_memory"))
    for record in stored:
        rows.append(mem.get(record["id"]))
        mem.recall(record["content"][-12:], top_k=3)
    with connect(db_path) as connection:
        after = connection.execute("SELECT COUNT(*) FROM working_memory").fetchone()[0]
    return {"count_before_read": before, "count_after_read": after, "got": len(rows)}


def init_operations(path: Path) -> None:
    with connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS operations (
                operation_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                status TEXT NOT NULL,
                kernel_id TEXT,
                product_memory_id TEXT NOT NULL
            )
            """
        )


def run_idem_write(data_dir: Path) -> dict:
    data_dir.mkdir(parents=True, exist_ok=True)
    control = data_dir / "operations.db"
    init_operations(control)
    payload = SAMPLES["idempotent"]
    with connect(control) as connection:
        connection.execute(
            "INSERT INTO operations(operation_id, payload, status, product_memory_id) VALUES (?, ?, 'prepared', ?)",
            (OPERATION_ID, payload, PRODUCT_ID),
        )
    mem, _db_path, _beam = open_memory(data_dir)
    remember(mem, payload)
    # Kernel write has committed. Exit before the control row is confirmed.
    sys.stdout.flush()
    os._exit(86)


def run_idem_mismatch(data_dir: Path) -> dict:
    control = data_dir / "operations.db"
    with connect(control) as connection:
        operation = connection.execute(
            "SELECT payload, status, kernel_id FROM operations WHERE operation_id = ?",
            (OPERATION_ID,),
        ).fetchone()
    attempted = SAMPLES["idempotent_other"]
    if operation["payload"] != attempted:
        with connect(data_dir / "mnemosyne.db") as connection:
            others = connection.execute(
                "SELECT COUNT(*) FROM working_memory WHERE content = ?",
                (attempted,),
            ).fetchone()[0]
            originals = connection.execute(
                "SELECT COUNT(*) FROM working_memory WHERE content = ?",
                (operation["payload"],),
            ).fetchone()[0]
        return {
            "conflict": True,
            "remember_called": False,
            "status": operation["status"],
            "kernel_id_still_unconfirmed": operation["kernel_id"] is None,
            "original_content_rows": originals,
            "other_content_rows": others,
        }
    raise SystemExit("mismatch fixture unexpectedly matched the stored payload")


def run_idem_retry(data_dir: Path) -> dict:
    control = data_dir / "operations.db"
    with connect(control) as connection:
        operation = connection.execute(
            "SELECT payload, status FROM operations WHERE operation_id = ?",
            (OPERATION_ID,),
        ).fetchone()
    if operation["status"] == "published":
        raise SystemExit("retry saw an already published operation")
    mem, db_path, _beam = open_memory(data_dir)
    kernel_id = remember(mem, operation["payload"])
    with connect(control) as connection:
        connection.execute(
            "UPDATE operations SET status = 'published', kernel_id = ? WHERE operation_id = ? AND payload = ?",
            (kernel_id, OPERATION_ID, operation["payload"]),
        )
    with connect(db_path) as connection:
        copies = connection.execute(
            "SELECT COUNT(*) FROM working_memory WHERE content = ?",
            (operation["payload"],),
        ).fetchone()[0]
        published = connect(control).execute(
            "SELECT kernel_id, status, payload FROM operations WHERE operation_id = ?",
            (OPERATION_ID,),
        ).fetchone()
    return {
        "kernel_id": kernel_id,
        "copies": copies,
        "published_status": published["status"],
        "published_kernel_id": published["kernel_id"],
        "payload_unchanged": published["payload"] == SAMPLES["idempotent"],
    }


def run_identity(data_dir: Path) -> dict:
    data_dir.mkdir(parents=True, exist_ok=True)
    mem, db_path, _beam = open_memory(data_dir)
    content = SAMPLES["same_body"]
    daily_id = remember(mem, content, valid_until="2099-01-01T00:00:00")
    with connect(db_path) as connection:
        after_daily = dict(
            connection.execute(
                "SELECT id, valid_until, superseded_by FROM working_memory WHERE id = ?",
                (daily_id,),
            ).fetchone()
        )
    formal_id = remember(mem, content, valid_until="2000-01-01T00:00:00")
    with connect(db_path) as connection:
        after_formal = dict(
            connection.execute(
                "SELECT id, valid_until, superseded_by FROM working_memory WHERE id = ?",
                (daily_id,),
            ).fetchone()
        )
        copies = connection.execute(
            "SELECT COUNT(*) FROM working_memory WHERE content = ?",
            (content,),
        ).fetchone()[0]
    invalidated = mem.invalidate(daily_id)
    with connect(db_path) as connection:
        after_invalidate = dict(
            connection.execute(
                "SELECT id, valid_until, superseded_by FROM working_memory WHERE id = ?",
                (daily_id,),
            ).fetchone()
        )
    forgotten = mem.forget(daily_id)
    return {
        "daily_kernel_id": daily_id,
        "formal_kernel_id": formal_id,
        "shared_kernel_id": daily_id == formal_id,
        "copies": copies,
        "after_daily": after_daily,
        "after_second_remember": after_formal,
        "invalidate_returned": bool(invalidated),
        "after_invalidate": after_invalidate,
        "forget_returned": bool(forgotten),
        "get_after_forget": mem.get(daily_id),
        "scenarios_in_kernel": False,
    }


def spawn(mode: str, data_dir: Path, extra: dict[str, str] | None = None) -> dict:
    completed = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), mode, str(data_dir)],
        cwd=str(SPIKE_ROOT),
        env=isolated_env(
            data_dir,
            {
                "MNEMOSYNE_WM_TTL_HOURS": "1000000",
                "MNEMOSYNE_WM_MAX_ITEMS": "1000000",
                **(extra or {}),
            },
        ),
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    payload: dict
    if completed.stdout.strip():
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError:
            payload = {"raw": completed.stdout[-2000:]}
    else:
        payload = {}
    payload["exit_code"] = completed.returncode
    if completed.returncode not in (0, 86):
        payload["stderr"] = completed.stderr[-3000:]
    return payload


def main() -> int:
    if len(sys.argv) == 3:
        assert_native_windows()
        mode = sys.argv[1]
        data_dir = Path(sys.argv[2])
        runners = {
            "delete": run_delete,
            "cap": lambda path: run_cap(path, guarded=False),
            "cap-guard": lambda path: run_cap(path, guarded=True),
            "cap-late": run_cap_late,
            "cap-read": run_cap_read,
            "idem-write": run_idem_write,
            "idem-mismatch": run_idem_mismatch,
            "idem-retry": run_idem_retry,
            "identity": run_identity,
        }
        emit(runners[mode](data_dir))
        return 0

    assert_native_windows()
    assert_python_pin()
    if RUNS.exists():
        import shutil

        shutil.rmtree(RUNS)
    hold = RUNS / "hold"
    idem = RUNS / "idem"
    hold_write = spawn("cap", hold, {"MNEMOSYNE_WM_MAX_ITEMS": "1000000"})
    result = {
        "task": "P0.2-followup",
        "p0_2_status": "PARTIAL",
        "delete": spawn("delete", RUNS / "delete"),
        "capacity": {
            "silent_trim_at_2": spawn("cap", RUNS / "cap-silent", {"MNEMOSYNE_WM_MAX_ITEMS": "2"}),
            "guard_refuses_third": spawn("cap-guard", RUNS / "cap-guard", {"MNEMOSYNE_WM_MAX_ITEMS": "2"}),
            "env_after_import_ignored": spawn(
                "cap-late", RUNS / "cap-late", {"MNEMOSYNE_WM_MAX_ITEMS": "1000000"}
            ),
            "read_path_keeps_rows": {
                "write": hold_write,
                "read": spawn("cap-read", hold),
            },
        },
        "idempotency": {},
        "identity": spawn("identity", RUNS / "identity"),
    }
    crashed = spawn("idem-write", idem)
    mismatch = spawn("idem-mismatch", idem)
    retry = spawn("idem-retry", idem)
    result["idempotency"] = {
        "crashed_after_kernel_write": crashed,
        "different_payload": mismatch,
        "retry_original_payload": retry,
    }
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "PARTIAL", "result": str(RESULT_PATH)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
