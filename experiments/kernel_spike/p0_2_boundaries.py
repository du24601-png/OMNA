"""P0.2: review, version, expiry, delete, and retry boundaries.

Official text stays in Mnemosyne. This script keeps only ids, revisions,
and publish state in a spike control database. It does not build zhiwo.db.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

SPIKE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = SPIKE_ROOT.parents[1]
SAMPLES = json.loads((SPIKE_ROOT / "fixtures" / "p0_2_samples.json").read_text(encoding="utf-8"))
RUNS = SPIKE_ROOT / "runs" / "p0_2"
RESULT_PATH = SPIKE_ROOT / "results" / "p0_2_windows.json"
SESSION_ID = "zhiwo-p0-2"
REQUIRED_PYTHON = "3.12.13"

DROPPED_ENV = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "OPENROUTER_BASE_URL",
    "HERMES_HOME",
    "MNEMOSYNE_HOME",
    "MNEMOSYNE_DATA_DIR",
    "MNEMOSYNE_DB_PATH",
    "MNEMOSYNE_EMBEDDING_API_KEY",
    "MNEMOSYNE_EMBEDDING_API_URL",
    "MNEMOSYNE_LLM_API_KEY",
    "MNEMOSYNE_LLM_BASE_URL",
    "MNEMOSYNE_SYNC_REMOTE",
    "MNEMOSYNE_SYNC_KEY",
)


def assert_native_windows() -> None:
    if sys.platform != "win32" or platform.system() != "Windows":
        raise SystemExit("BLOCKED: P0.2 requires native Windows")
    if os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"):
        raise SystemExit("BLOCKED: WSL is not accepted")


def assert_python_pin() -> None:
    pinned = (SPIKE_ROOT / ".python-version").read_text(encoding="utf-8").strip()
    running = sys.version.split()[0]
    if pinned != REQUIRED_PYTHON or running != REQUIRED_PYTHON:
        raise SystemExit(
            f"BLOCKED: Python pin mismatch. file={pinned} running={running} required={REQUIRED_PYTHON}"
        )


def isolated_env(data_dir: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in DROPPED_ENV}
    env.update(
        {
            "MNEMOSYNE_DATA_DIR": str(data_dir),
            "MNEMOSYNE_PERSONA_FILE": str(data_dir / "persona.md"),
            "MNEMOSYNE_FASTEMBED_CACHE_DIR": str(data_dir / "fastembed-cache"),
            "MNEMOSYNE_NO_EMBEDDINGS": "1",
            "MNEMOSYNE_EMBEDDINGS_OFF": "1",
            "MNEMOSYNE_SKIP_EMBEDDINGS": "1",
            "MNEMOSYNE_EMBEDDINGS_VIA_API": "0",
            "MNEMOSYNE_LLM_ENABLED": "0",
            "MNEMOSYNE_HOST_LLM_ENABLED": "0",
            "MNEMOSYNE_FORCE_LOCAL": "1",
            "MNEMOSYNE_SLEEP_MODEL_REFRESH_ENABLED": "false",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    if extra:
        env.update(extra)
    return env


def default_targets() -> dict[str, str]:
    home = Path.home()
    hermes_root = Path(os.environ["HOME"]) / ".hermes" if os.environ.get("HOME") else home / ".hermes"
    if os.environ.get("HERMES_HOME"):
        hermes_root = Path(os.environ["HERMES_HOME"])
    return {
        "home": str(home),
        "HOME": os.environ.get("HOME", ""),
        "HERMES_HOME": os.environ.get("HERMES_HOME", ""),
        "unset_data_dir": str(hermes_root / "mnemosyne" / "data"),
        "unset_db": str(hermes_root / "mnemosyne" / "data" / "mnemosyne.db"),
        "unset_config": str(hermes_root / "mnemosyne" / "config.yaml"),
        "unset_data_config": str(hermes_root / "mnemosyne" / "data" / "config.yaml"),
        "unset_persona": str(hermes_root / "memory" / "persona.md"),
        "unset_fastembed": str(hermes_root / "cache" / "fastembed"),
        "hermes_mnemosyne": str(hermes_root / "mnemosyne"),
        "hermes_memory": str(hermes_root / "memory"),
        "hermes_cache": str(hermes_root / "cache"),
        "profile_hermes": str(home / ".hermes"),
        "profile_mnemosyne": str(home / ".mnemosyne"),
    }


def file_stat(path: Path) -> dict:
    if not path.exists():
        return {"path": str(path), "exists": False}
    stat = path.stat()
    return {
        "path": str(path),
        "exists": True,
        "is_dir": path.is_dir(),
        "size": stat.st_size if path.is_file() else None,
        "mtime_ns": stat.st_mtime_ns,
    }


def tree_digest(root: Path) -> dict:
    if not root.exists():
        return {"path": str(root), "exists": False, "file_count": 0, "digest": None}
    digest = hashlib.sha256()
    count = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        stat = path.stat()
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(str(stat.st_size).encode())
        digest.update(str(stat.st_mtime_ns).encode())
        count += 1
    return {"path": str(root), "exists": True, "file_count": count, "digest": digest.hexdigest()}


def snapshot_defaults() -> dict:
    targets = default_targets()
    files = [
        "unset_db",
        "unset_config",
        "unset_data_config",
        "unset_persona",
    ]
    trees = [
        "unset_data_dir",
        "unset_fastembed",
        "hermes_mnemosyne",
        "hermes_memory",
        "hermes_cache",
        "profile_hermes",
        "profile_mnemosyne",
    ]
    return {
        "targets": targets,
        "files": {name: file_stat(Path(targets[name])) for name in files},
        "trees": {name: tree_digest(Path(targets[name])) for name in trees},
    }


def open_kernel(data_dir: Path):
    os.environ.update(isolated_env(data_dir))
    from mnemosyne import Mnemosyne
    from mnemosyne.core.beam import _default_db_path
    from mnemosyne.core.config import _default_config_path
    from mnemosyne.core.persona import DEFAULT_PERSONA_FILE

    db_path = data_dir / "mnemosyne.db"
    mem = Mnemosyne(session_id=SESSION_ID, db_path=db_path)
    resolved = {
        "requested_db": str(db_path.resolve()),
        "instance_db": str(Path(mem.db_path).resolve()),
        "library_default_db": str(Path(_default_db_path()).resolve()),
        "config_path": str(Path(_default_config_path()).resolve()),
        "persona_file": str(Path(DEFAULT_PERSONA_FILE).resolve()),
        "fastembed_cache": os.environ["MNEMOSYNE_FASTEMBED_CACHE_DIR"],
        "data_dir": os.environ["MNEMOSYNE_DATA_DIR"],
    }
    if Path(mem.db_path).resolve() != db_path.resolve():
        raise SystemExit(f"Kernel opened unexpected database: {mem.db_path}")
    return mem, resolved


def remember(mem, content: str, **kwargs) -> str:
    memory_id = mem.remember(
        content,
        source="zhiwo-p0.2",
        importance=0.9,
        scope="global",
        extract=False,
        extract_entities=False,
        **kwargs,
    )
    if not memory_id:
        raise RuntimeError(f"remember() returned no id for {content}")
    return memory_id


def connect(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def table_counts(db_path: Path) -> dict[str, int | None]:
    names = [
        "working_memory",
        "episodic_memory",
        "memories",
        "memoria_facts",
        "memoria_timelines",
        "memoria_kg",
        "annotations",
        "triples",
        "memory_embeddings",
    ]
    with connect(db_path) as connection:
        present = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        counts: dict[str, int | None] = {}
        for name in names:
            if name not in present:
                counts[name] = None
                continue
            counts[name] = connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        return counts


def content_count(db_path: Path, content: str) -> int:
    with connect(db_path) as connection:
        return connection.execute(
            "SELECT COUNT(*) FROM working_memory WHERE content = ?",
            (content,),
        ).fetchone()[0]


def row_meta(db_path: Path, memory_id: str) -> dict | None:
    with connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT id, content, memory_type, scope, valid_until, superseded_by, consolidated_at
            FROM working_memory WHERE id = ?
            """,
            (memory_id,),
        ).fetchone()
    return dict(row) if row else None


def init_control(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE operations (
            operation_id TEXT PRIMARY KEY,
            content_hash TEXT NOT NULL,
            kernel_id TEXT,
            status TEXT NOT NULL
        );
        CREATE TABLE memory_refs (
            product_memory_id TEXT NOT NULL,
            revision INTEGER NOT NULL,
            kernel_id TEXT NOT NULL,
            lifecycle TEXT NOT NULL,
            scenario TEXT,
            kind TEXT NOT NULL,
            PRIMARY KEY (product_memory_id, revision)
        );
        """
    )
    return connection


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def ids_from_recall(mem, query: str) -> list[str]:
    rows = mem.recall(query, top_k=5)
    return [row.get("id") for row in rows if isinstance(row, dict)]


def check(results: list[dict], name: str, ok: bool, evidence: dict) -> None:
    results.append({"name": name, "ok": bool(ok), "evidence": evidence})


def run_core(data_dir: Path) -> dict:
    data_dir.mkdir(parents=True, exist_ok=True)
    mem, resolved = open_kernel(data_dir)
    db_path = Path(resolved["instance_db"])
    results: list[dict] = []
    control_path = data_dir / "control.db"
    control = init_control(control_path)

    old_id = remember(mem, SAMPLES["old_preference"])
    new_id = remember(mem, SAMPLES["new_preference"])
    formal_id = remember(mem, SAMPLES["formal_report"])
    event_id = remember(mem, SAMPLES["event"])
    preference_product = str(uuid.uuid4())
    formal_product = str(uuid.uuid4())
    event_product = str(uuid.uuid4())
    control.execute(
        "INSERT INTO memory_refs VALUES (?, 1, ?, 'superseded', ?, 'fact')",
        (preference_product, old_id, "日常回复"),
    )
    control.execute(
        "INSERT INTO memory_refs VALUES (?, 2, ?, 'active', ?, 'fact')",
        (preference_product, new_id, "日常回复"),
    )
    control.execute(
        "INSERT INTO memory_refs VALUES (?, 1, ?, 'active', ?, 'fact')",
        (formal_product, formal_id, "正式报告"),
    )
    control.execute(
        "INSERT INTO memory_refs VALUES (?, 1, ?, 'active', ?, 'event')",
        (event_product, event_id, None),
    )
    control.commit()

    old_row = mem.get(old_id)
    new_row = mem.get(new_id)
    check(
        results,
        "version_bodies_stay_in_kernel",
        old_id != new_id
        and old_row
        and old_row.get("content") == SAMPLES["old_preference"]
        and new_row
        and new_row.get("content") == SAMPLES["new_preference"]
        and old_row.get("memory_store") == "working",
        {
            "class": "mnemosyne.Mnemosyne",
            "write": "remember(extract=False, extract_entities=False)",
            "read": "get(memory_id)",
            "table": "working_memory",
            "old_id": old_id,
            "new_id": new_id,
            "control_has_body": False,
        },
    )
    active = list(
        control.execute(
            """
            SELECT product_memory_id, revision, kernel_id, scenario, kind
            FROM memory_refs WHERE lifecycle = 'active' ORDER BY kind, scenario
            """
        )
    )
    check(
        results,
        "current_history_and_coexistence",
        {row["kernel_id"] for row in active} == {new_id, formal_id, event_id}
        and old_id not in {row["kernel_id"] for row in active}
        and len({row["scenario"] for row in active if row["kind"] == "fact"}) == 2,
        {
            "active": [dict(row) for row in active],
            "historical_kernel_id": old_id,
            "kernel_scope": {
                old_id: row_meta(db_path, old_id),
                formal_id: row_meta(db_path, formal_id),
            },
        },
    )
    recalled_old = ids_from_recall(mem, "ZW-P02-PREF-OLD")
    recalled_new = ids_from_recall(mem, "ZW-P02-PREF-NEW")
    check(
        results,
        "same_store_is_recallable_by_token",
        old_id in recalled_old and new_id in recalled_new,
        {
            "method": "recall(token, top_k=5)",
            "old": recalled_old,
            "new": recalled_new,
            "note": "Token hit only. This is not a Chinese Hit@5 result.",
        },
    )

    inplace_id = remember(mem, SAMPLES["inplace_before"])
    updated = mem.update(inplace_id, content=SAMPLES["inplace_after"])
    inplace_row = mem.get(inplace_id)
    check(
        results,
        "kernel_update_overwrites_body",
        updated
        and inplace_row
        and inplace_row.get("content") == SAMPLES["inplace_after"]
        and content_count(db_path, SAMPLES["inplace_before"]) == 0,
        {
            "method": "Mnemosyne.update -> BeamMemory.update_working",
            "same_id": inplace_id,
            "old_body_remaining": content_count(db_path, SAMPLES["inplace_before"]),
            "adapter": "Do not use update() for a product version. remember() a new row.",
        },
    )

    expired_id = remember(mem, SAMPLES["already_expired"], valid_until="2000-01-01T00:00:00")
    future_id = remember(
        mem,
        SAMPLES["still_valid"],
        valid_until=(datetime.now() + timedelta(days=30)).isoformat(timespec="seconds"),
    )
    expired_get = mem.get(expired_id)
    context_ids = [row.get("id") for row in mem.get_context(limit=20)]
    expired_recall = ids_from_recall(mem, "ZW-P02-EXPIRE")
    future_recall = ids_from_recall(mem, "ZW-P02-VALID")
    check(
        results,
        "expiry_hides_from_recall_but_keeps_history",
        expired_get
        and expired_get.get("content") == SAMPLES["already_expired"]
        and expired_id not in expired_recall
        and expired_id not in context_ids
        and future_id in future_recall,
        {
            "kernel_stores_valid_until": row_meta(db_path, expired_id),
            "get_returns_expired_body": True,
            "recall_ids": expired_recall,
            "future_recall_ids": future_recall,
            "get_context_contains_expired": expired_id in context_ids,
            "adapter": "Product expiry also lives on the control row. get() does not return valid_until.",
        },
    )
    invalidated = mem.invalidate(old_id, replacement_id=new_id)
    old_after = mem.get(old_id)
    old_recall_after = ids_from_recall(mem, "ZW-P02-PREF-OLD")
    check(
        results,
        "invalidate_keeps_old_body_and_hides_it",
        invalidated
        and old_after
        and old_after.get("content") == SAMPLES["old_preference"]
        and old_id not in old_recall_after,
        {
            "method": "invalidate(old_id, replacement_id=new_id)",
            "meta": row_meta(db_path, old_id),
            "recall_after": old_recall_after,
        },
    )

    operation_id = str(uuid.uuid4())
    idem_id = remember(mem, SAMPLES["idempotent"])
    # Kernel accepted the write. The caller crashes before the control record.
    retry_id = remember(mem, SAMPLES["idempotent"])
    control.execute(
        "INSERT INTO operations VALUES (?, ?, ?, 'accepted')",
        (operation_id, content_hash(SAMPLES["idempotent"]), retry_id),
    )
    control.commit()
    check(
        results,
        "retry_same_payload_finds_original",
        idem_id == retry_id and content_count(db_path, SAMPLES["idempotent"]) == 1,
        {
            "method": "BeamMemory.remember exact-content dedup within session_id",
            "operation_id": operation_id,
            "kernel_id": retry_id,
            "row_count": content_count(db_path, SAMPLES["idempotent"]),
            "kernel_operation_id_field": False,
        },
    )
    conflict = control.execute(
        "SELECT content_hash, kernel_id FROM operations WHERE operation_id = ?",
        (operation_id,),
    ).fetchone()
    other_hash = content_hash(SAMPLES["idempotent_other"])
    mismatch_blocked = conflict["content_hash"] != other_hash
    other_id = None
    if not mismatch_blocked:
        other_id = remember(mem, SAMPLES["idempotent_other"])
    lost_record_other = remember(mem, SAMPLES["idempotent_other"])
    check(
        results,
        "operation_mismatch_and_lost_record",
        mismatch_blocked
        and lost_record_other != retry_id
        and content_count(db_path, SAMPLES["idempotent_other"]) == 1,
        {
            "control_blocks_different_payload": mismatch_blocked,
            "kernel_has_no_operation_id": True,
            "changed_payload_without_control_record_creates_new_row": lost_record_other,
            "adapter": "Retry the original payload. Persist operation_id only after remember() returns. A changed payload with no control row cannot be detected by the kernel.",
        },
    )

    before_delete = table_counts(db_path)
    delete_id = remember(mem, SAMPLES["delete_me"])
    facts_before = 0
    with connect(db_path) as connection:
        facts_before = connection.execute(
            "SELECT COUNT(*) FROM memoria_facts WHERE source_memory_id = ?",
            (delete_id,),
        ).fetchone()[0]
    forgotten = mem.forget(delete_id)
    facts_after = 0
    annotations_after = 0
    timelines_after = 0
    with connect(db_path) as connection:
        facts_after = connection.execute(
            "SELECT COUNT(*) FROM memoria_facts WHERE source_memory_id = ?",
            (delete_id,),
        ).fetchone()[0]
        annotations_after = connection.execute(
            "SELECT COUNT(*) FROM annotations WHERE memory_id = ?",
            (delete_id,),
        ).fetchone()[0]
        timelines_after = connection.execute(
            "SELECT COUNT(*) FROM memoria_timelines WHERE source_memory_id = ?",
            (delete_id,),
        ).fetchone()[0]
    legacy_after = 0
    with connect(db_path) as connection:
        legacy_after = connection.execute(
            "SELECT COUNT(*) FROM memories WHERE id = ?",
            (delete_id,),
        ).fetchone()[0]
    check(
        results,
        "forget_removes_working_body",
        forgotten and mem.get(delete_id) is None and content_count(db_path, SAMPLES["delete_me"]) == 0,
        {
            "method": "Mnemosyne.forget -> delete memories + BeamMemory.forget_working",
            "facts_for_id_before": facts_before,
            "facts_for_id_after": facts_after,
            "annotations_for_id_after": annotations_after,
            "timelines_for_id_after": timelines_after,
            "legacy_rows_after": legacy_after,
            "counts_before_delete_sample": before_delete,
            "counts_after": table_counts(db_path),
            "vector_index": "sqlite-vec / fastembed not installed; cleanup not claimed",
        },
    )

    episodic = table_counts(db_path)["episodic_memory"]
    persona_path = Path(resolved["persona_file"])
    check(
        results,
        "remember_does_not_sleep_or_write_persona",
        episodic == 0 and not persona_path.exists(),
        {
            "episodic_rows_after_remember": episodic,
            "persona_file_exists": persona_path.exists(),
            "config_flags_are_not_execution": True,
        },
    )
    types = {
        "preference": row_meta(db_path, new_id),
        "event": row_meta(db_path, event_id),
    }
    check(
        results,
        "kernel_type_is_not_product_kind",
        types["preference"] is not None and types["event"] is not None,
        {
            "kernel_memory_type": {
                "preference_sample": types["preference"]["memory_type"] if types["preference"] else None,
                "event_sample": types["event"]["memory_type"] if types["event"] else None,
            },
            "product_kind_stored_in": "control.memory_refs.kind",
        },
    )
    control.close()
    return {"resolved_paths": resolved, "checks": results, "control_db": str(control_path)}


def run_ttl(data_dir: Path) -> dict:
    data_dir.mkdir(parents=True, exist_ok=True)
    mem, resolved = open_kernel(data_dir)
    db_path = Path(resolved["instance_db"])
    from mnemosyne.core.beam import WORKING_MEMORY_TTL_HOURS

    doomed_id = remember(mem, SAMPLES["ttl_old"])
    backdated = (datetime.now() - timedelta(hours=200)).isoformat(timespec="seconds")
    with connect(db_path) as connection:
        connection.execute(
            "UPDATE working_memory SET timestamp = ? WHERE id = ?",
            (backdated, doomed_id),
        )
    remember(mem, SAMPLES["ttl_keeper"])
    remaining = mem.get(doomed_id)
    return {
        "ttl_hours_seen_at_import": WORKING_MEMORY_TTL_HOURS,
        "doomed_id": doomed_id,
        "survived": remaining is not None,
        "content": remaining.get("content") if remaining else None,
    }


def run_sleep(data_dir: Path) -> dict:
    data_dir.mkdir(parents=True, exist_ok=True)
    mem, resolved = open_kernel(data_dir)
    db_path = Path(resolved["instance_db"])
    source_id = remember(mem, SAMPLES["sleep_source"])
    summary = mem.sleep(force=True)
    source_after = mem.get(source_id)
    with connect(db_path) as connection:
        episodic = [
            dict(row)
            for row in connection.execute(
                "SELECT id, content, summary_of FROM episodic_memory"
            )
        ]
    return {
        "sleep_result_status": summary.get("status") if isinstance(summary, dict) else str(summary),
        "original_content": source_after.get("content") if source_after else None,
        "original_unchanged": bool(source_after and source_after.get("content") == SAMPLES["sleep_source"]),
        "consolidated_at": row_meta(db_path, source_id),
        "episodic": [
            {
                "id": row["id"],
                "summary_of": row["summary_of"],
                "content_equals_source": row["content"] == SAMPLES["sleep_source"],
                "content_preview": row["content"][:180],
            }
            for row in episodic
        ],
    }


def emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def run_mode() -> int:
    assert_native_windows()
    mode = sys.argv[1]
    data_dir = Path(sys.argv[2])
    if mode == "core":
        emit(run_core(data_dir))
    elif mode == "ttl":
        emit(run_ttl(data_dir))
    elif mode == "sleep":
        emit(run_sleep(data_dir))
    else:
        raise SystemExit(f"unknown mode {mode}")
    return 0


def spawn(mode: str, data_dir: Path, extra: dict[str, str] | None = None) -> dict:
    completed = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), mode, str(data_dir)],
        cwd=str(SPIKE_ROOT),
        env=isolated_env(data_dir, extra),
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        return {
            "ok": False,
            "exit_code": completed.returncode,
            "stderr": completed.stderr[-4000:],
            "stdout": completed.stdout[-2000:],
        }
    payload = json.loads(completed.stdout)
    payload["exit_code"] = completed.returncode
    return payload


def matrix_from(checks: list[dict], ttl_default: dict, ttl_disabled: dict, sleep: dict) -> list[dict]:
    by_name = {item["name"]: item for item in checks}
    ttl_disabled_ok = ttl_disabled.get("survived") is True and ttl_disabled.get("ttl_hours_seen_at_import") == 1000000
    ttl_default_ok = ttl_default.get("survived") is False and ttl_default.get("ttl_hours_seen_at_import") == 168
    sleep_ok = sleep.get("original_unchanged") is True and bool(sleep.get("episodic"))
    rows = [
        (
            "存取路径",
            "Mnemosyne.remember / get / recall",
            by_name["version_bodies_stay_in_kernel"]["ok"] and by_name["same_store_is_recallable_by_token"]["ok"],
            "Adapter 只把已发布 kernel_id 交给读取；recall 命中标记词只说明同一张 working_memory 可被检索。",
            "中文 Hit@5 未测。",
        ),
        (
            "自动 sleep / persona",
            "remember 不调用 sleep；persona.md 未生成",
            by_name["remember_does_not_sleep_or_write_persona"]["ok"],
            "产品写入路径不要调用 sleep()。配置项本身不是触发证据。",
            "显式 sleep() 仍会生成压缩后的 episodic 行。",
        ),
        (
            "显式 sleep",
            "Mnemosyne.sleep(force=True)",
            sleep_ok,
            "不把 episodic 摘要当作正式正文。未登记的 kernel_id 一律丢弃。",
            "默认 sleep 还会尝试模型刷新；本次用环境变量关掉了该分支。",
        ),
        (
            "版本",
            "新 remember 行 + control.memory_refs",
            by_name["version_bodies_stay_in_kernel"]["ok"] and by_name["current_history_and_coexistence"]["ok"],
            "更新时新建 kernel 行。当前指针和场景放在控制记录。",
            "Mnemosyne.update 会原地覆盖，不能当作版本接口。",
        ),
        (
            "有效期",
            "remember(valid_until) + invalidate + recall/get_context 过滤",
            by_name["expiry_hides_from_recall_but_keeps_history"]["ok"]
            and by_name["invalidate_keeps_old_body_and_hides_it"]["ok"],
            "Kernel 负责检索过滤。知我仍保存有效期，并用 get(kernel_id) 读取历史正文。",
            "get() 不返回 valid_until。",
        ),
        (
            "删除",
            "Mnemosyne.forget",
            by_name["forget_removes_working_body"]["ok"],
            "正式正文走 forget。派生表残留不能当作正式记忆读取。",
            "forget() 会留下 memoria_facts 派生行。向量索引未安装，不宣称已清理。",
        ),
        (
            "幂等",
            "同会话相同正文的 remember 去重 + control.operations",
            by_name["retry_same_payload_finds_original"]["ok"]
            and by_name["operation_mismatch_and_lost_record"]["ok"],
            "重试必须提交原始正文。operation_id 写在控制记录，不写进第二份正文库。",
            "Kernel 没有 operation_id。控制记录丢失时，不同正文会再插入一行。",
        ),
        (
            "工作记忆清理",
            "remember 后的 _trim_working_memory",
            ttl_default_ok and ttl_disabled_ok,
            "进程启动前设置 MNEMOSYNE_WM_TTL_HOURS 与足够大的 MNEMOSYNE_WM_MAX_ITEMS。",
            "默认 168 小时会删除未巩固的旧行。常量在导入时读取。",
        ),
        (
            "事实/事件",
            "control.memory_refs.kind；kernel memory_type 仅观察",
            by_name["kernel_type_is_not_product_kind"]["ok"],
            "中文事实/事件由知我指定 kind。不要读取 kernel 的英文规则分类作为产品类别。",
            "本次中文样本的 kernel memory_type 见结果，不能当成产品映射。",
        ),
    ]
    return [
        {
            "check": name,
            "api": api,
            "measured_ok": ok,
            "adapter": adapter,
            "limit": limit,
        }
        for name, api, ok, adapter, limit in rows
    ]


def main() -> int:
    if len(sys.argv) == 3:
        return run_mode()
    assert_native_windows()
    assert_python_pin()
    if RUNS.exists():
        import shutil

        shutil.rmtree(RUNS)
    RUNS.mkdir(parents=True)
    before = snapshot_defaults()
    core = spawn("core", RUNS / "core")
    ttl_default = spawn("ttl", RUNS / "ttl-default")
    ttl_disabled = spawn(
        "ttl",
        RUNS / "ttl-disabled",
        {"MNEMOSYNE_WM_TTL_HOURS": "1000000"},
    )
    sleep = spawn("sleep", RUNS / "sleep")
    after = snapshot_defaults()
    checks = core.get("checks", []) if "checks" in core else []
    matrix = matrix_from(checks, ttl_default, ttl_disabled, sleep) if checks else []
    measured_ok = bool(matrix) and all(row["measured_ok"] for row in matrix)
    paths_ok = before == after
    decision = "GO" if measured_ok and paths_ok and core.get("exit_code") == 0 else "BLOCKED"
    result = {
        "task": "P0.2",
        "decision": decision,
        "p0_overall": "NOT_PASSED",
        "native_windows": True,
        "python": sys.version.split()[0],
        "python_pin_file": REQUIRED_PYTHON,
        "mnemosyne": "3.15.1",
        "commands": [
            "uv python pin 3.12.13",
            "uv lock",
            "uv sync",
            "uv run python p0_2_boundaries.py",
        ],
        "default_path_snapshot_unchanged": paths_ok,
        "default_paths_before": before,
        "default_paths_after": after,
        "core": core,
        "ttl_default": ttl_default,
        "ttl_disabled": ttl_disabled,
        "explicit_sleep": sleep,
        "matrix": matrix,
        "not_verified": [
            "Chinese Hit@5 and offline embeddings (P0.3)",
            "vector index deletion (P0.3; embeddings extra not installed)",
            "stdio MCP (P0.4)",
            "product UI, full zhiwo.db, Electron",
        ],
    }
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"decision": decision, "result": str(RESULT_PATH)}, ensure_ascii=False))
    return 0 if decision == "GO" else 1


if __name__ == "__main__":
    raise SystemExit(main())
