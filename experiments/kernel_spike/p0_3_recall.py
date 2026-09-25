"""P0.3: Chinese local recall, offline retest, and vector-index deletion.

Twenty synthetic memories stay in one Mnemosyne database. Each version uses
zhiwo:{memory_id}:r{revision}. A single reader session calls recall().
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
from pathlib import Path

SPIKE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = SPIKE_ROOT.parents[1]
sys.path.insert(0, str(REPO_ROOT / "server"))

from p0_2_boundaries import DROPPED_ENV, assert_native_windows, assert_python_pin
from zhiwo.adapters.derived_cleanup import cleanup_derived_rows
from zhiwo.adapters.kernel_session import kernel_session
from zhiwo.adapters.retention import remember_within_cap

FIXTURE = json.loads((SPIKE_ROOT / "fixtures" / "p0_3_queries.json").read_text(encoding="utf-8"))
RUNS = SPIKE_ROOT / "runs" / "p0_3"
RESULT_PATH = SPIKE_ROOT / "results" / "p0_3_windows.json"
CACHE_DIR = RUNS / "fastembed-cache"
READER = "zhiwo:p03-reader"
LIMIT = 1_000_000
TOP_K = 5
FIRST_MODEL = "BAAI/bge-small-zh-v1.5"
SECOND_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DELETE_TARGET = "m11"
KEEP_TARGET = "m01"
IRRELEVANT = "如何校准实验室里的激光干涉仪"
EXPIRED_TEXT = "这座城市的公共图书馆每周三全天闭馆。"
EXPIRED_QUERY = "公共图书馆周几不开放"
OLD_ADDRESS = "旧地址在城西的老邮局旁边。"
OLD_QUERY = "城西老邮局旁边的旧地址"


def emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def local_env(data_dir: Path, model: str, offline: bool) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key not in DROPPED_ENV}
    for key in (
        "MNEMOSYNE_NO_EMBEDDINGS",
        "MNEMOSYNE_EMBEDDINGS_OFF",
        "MNEMOSYNE_SKIP_EMBEDDINGS",
        "MNEMOSYNE_EMBEDDING_API_URL",
        "MNEMOSYNE_EMBEDDING_API_KEY",
        "MNEMOSYNE_EMBEDDING_DIM",
    ):
        env.pop(key, None)
    env.update(
        {
            "MNEMOSYNE_DATA_DIR": str(data_dir),
            "MNEMOSYNE_PERSONA_FILE": str(data_dir / "persona.md"),
            "MNEMOSYNE_FASTEMBED_CACHE_DIR": str(CACHE_DIR),
            "MNEMOSYNE_EMBEDDING_MODEL": model,
            "MNEMOSYNE_EMBEDDINGS_VIA_API": "0",
            "MNEMOSYNE_LLM_ENABLED": "0",
            "MNEMOSYNE_HOST_LLM_ENABLED": "0",
            "MNEMOSYNE_FORCE_LOCAL": "1",
            "MNEMOSYNE_SLEEP_MODEL_REFRESH_ENABLED": "false",
            "MNEMOSYNE_WM_TTL_HOURS": "1000000",
            "MNEMOSYNE_WM_MAX_ITEMS": "1000000",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    if offline:
        env["HF_HUB_OFFLINE"] = "1"
        env["TRANSFORMERS_OFFLINE"] = "1"
    return env


def block_network() -> None:
    """Refuse new TCP connections in this process. Local SQLite is unaffected."""

    class _Blocked(socket.socket):
        def connect(self, address):  # type: ignore[override]
            raise OSError(f"offline guard refused {address}")

        def connect_ex(self, address):  # type: ignore[override]
            raise OSError(f"offline guard refused {address}")

    socket.socket = _Blocked

    def _create_connection(*_args, **_kwargs):
        raise OSError("offline guard refused create_connection")

    socket.create_connection = _create_connection


def network_probe() -> str:
    try:
        socket.create_connection(("huggingface.co", 443), timeout=5)
    except OSError as exc:
        return f"blocked:{type(exc).__name__}:{exc}"
    return "REACHABLE"


def package_versions() -> dict[str, str]:
    import importlib.metadata as metadata

    found = {}
    for name in ("mnemosyne-memory", "fastembed", "sqlite-vec", "numpy", "onnxruntime"):
        try:
            found[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            found[name] = "MISSING"
    return found


def open_memory(data_dir: Path, session_id: str):
    os.environ["MNEMOSYNE_DATA_DIR"] = str(data_dir)
    os.environ["MNEMOSYNE_PERSONA_FILE"] = str(data_dir / "persona.md")
    from mnemosyne import Mnemosyne

    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "mnemosyne.db"
    return Mnemosyne(session_id=session_id, db_path=db_path), db_path


def assert_queries_have_no_markers() -> None:
    for query in FIXTURE["queries"]:
        text = query["text"]
        if "ZW-" in text or "标记" in text:
            raise SystemExit(f"query {query['id']} contains a test marker")


def write_control(data_dir: Path, rows: list[dict]) -> None:
    (data_dir / "control.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def index_snapshot(connection) -> dict:
    from mnemosyne.core.embeddings import _DEFAULT_MODEL

    embeddings = connection.execute(
        "SELECT model, COUNT(*) AS n FROM memory_embeddings GROUP BY model"
    ).fetchall()
    vec_count = None
    vec_error = None
    try:
        vec_count = connection.execute("SELECT COUNT(*) FROM vec_working").fetchone()[0]
    except sqlite3.Error as exc:
        vec_error = f"{type(exc).__name__}: {exc}"
    return {
        "configured_model": _DEFAULT_MODEL,
        "memory_embeddings": [{"model": row[0], "count": row[1]} for row in embeddings],
        "vec_working_count": vec_count,
        "vec_working_error": vec_error,
        "public_api": "Mnemosyne.recall(explain=True)",
        "engine": "linear",
        "working_path": (
            "CJK queries miss unicode61 FTS terms and fall back to per-character LIKE, "
            "then merge sqlite-vec vec_working (or memory_embeddings cosine if vec0 is absent). "
            "A kept working-memory row still has to pass the lexical overlap gate; "
            "vector similarity is then blended into that score."
        ),
    }


def vector_topk(connection, query: str, k: int = TOP_K) -> list[str]:
    from mnemosyne.core.beam import _wm_vec_search
    from mnemosyne.core.embeddings import embed_query

    vector = embed_query(query)
    if vector is None:
        return []
    rows = _wm_vec_search(connection, vector, k=k)
    return [row["id"] for row in rows]


def score_queries(reader, id_by_product: dict[str, str]) -> dict:
    cases = []
    for query in FIXTURE["queries"]:
        explained = reader.recall(query["text"], top_k=TOP_K, explain=True)
        results = explained["results"]
        target = id_by_product[query["target"]]
        top = []
        for row in results[:TOP_K]:
            top.append(
                {
                    "id": row.get("id"),
                    "dense_score": row.get("dense_score"),
                    "keyword_score": row.get("keyword_score"),
                    "fts_score": row.get("fts_score"),
                    "score": row.get("score"),
                    "preview": str(row.get("content") or "")[:40],
                }
            )
        vector_ids = vector_topk(reader.beam.conn, query["text"])
        cases.append(
            {
                "id": query["id"],
                "group": query["group"],
                "target_product": query["target"],
                "target_kernel": target,
                "query": query["text"],
                "hit": target in [row["id"] for row in top],
                "vector_hit": target in vector_ids,
                "top": top,
                "vector_top": vector_ids,
                "embedding": explained["explain"]["embedding"],
                "stages": explained["explain"]["stages"],
            }
        )
    return {"cases": cases, **summarize(cases)}


def summarize(cases: list[dict]) -> dict:
    summary = {}
    for group in ("direct", "rewrite"):
        selected = [case for case in cases if case["group"] == group]
        hits = sum(case["hit"] for case in selected)
        vector_hits = sum(case["vector_hit"] for case in selected)
        summary[group] = {
            "queries": len(selected),
            "hits": hits,
            "hit_rate": hits / len(selected) if selected else 0,
            "vector_hits": vector_hits,
            "vector_hit_rate": vector_hits / len(selected) if selected else 0,
            "failures": [case["id"] for case in selected if not case["hit"]],
            "vector_misses": [case["id"] for case in selected if not case["vector_hit"]],
        }
    hits = sum(case["hit"] for case in cases)
    vector_hits = sum(case["vector_hit"] for case in cases)
    summary["combined"] = {
        "queries": len(cases),
        "hits": hits,
        "hit_at_5": hits / len(cases) if cases else 0,
        "pass_80": hits >= 16,
        "vector_hits": vector_hits,
        "vector_hit_at_5": vector_hits / len(cases) if cases else 0,
        "vector_pass_80": vector_hits >= 16,
    }
    return summary


def checkpoint(db_path: Path) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()


def run_online(data_dir: Path) -> dict:
    assert_queries_have_no_markers()
    supported = None
    support_error = None
    try:
        from fastembed import TextEmbedding

        supported = sorted(model["model"] for model in TextEmbedding.list_supported_models())
    except Exception as exc:
        support_error = f"{type(exc).__name__}: {exc}"
    model = os.environ["MNEMOSYNE_EMBEDDING_MODEL"]
    if supported is not None and model not in supported:
        return {
            "status": "MODEL_UNSUPPORTED",
            "model": model,
            "error": f"{model} is not in the installed fastembed model list",
        }
    if support_error and "fastembed" in support_error:
        return {"status": "IMPORT_FAILED", "model": model, "error": support_error}

    if data_dir.exists():
        shutil.rmtree(data_dir)
    data_dir.mkdir(parents=True)
    control = []
    id_by_product = {}
    db_path = data_dir / "mnemosyne.db"
    for item in FIXTURE["memories"]:
        session_id = kernel_session(item["id"], 1)
        row = {
            "memory_id": item["id"],
            "revision": 1,
            "scenario": item["scenario"],
            "valid_until": "2099-01-01T00:00:00",
            "content": item["content"],
            "session": session_id,
        }
        control.append(row)
        write_control(data_dir, control)
        mem, db_path = open_memory(data_dir, session_id)
        id_by_product[item["id"]] = remember_within_cap(
            mem,
            db_path,
            session_id,
            item["content"],
            LIMIT,
            valid_until=row["valid_until"],
        )
    reader, db_path = open_memory(data_dir, READER)
    snapshot = index_snapshot(reader.beam.conn)
    checkpoint(db_path)
    snapshot_dir = data_dir / "before-recall"
    if snapshot_dir.exists():
        shutil.rmtree(snapshot_dir)
    snapshot_dir.mkdir()
    shutil.copy2(db_path, snapshot_dir / "mnemosyne.db")
    scored = score_queries(reader, id_by_product)
    return {
        "status": "SCORED",
        "model": model,
        "versions": package_versions(),
        "kernel_ids": id_by_product,
        "index": snapshot,
        "fastembed_import_error": support_error,
        **scored,
    }


def run_offline(data_dir: Path) -> dict:
    block_network()
    probe = network_probe()
    snapshot_db = data_dir / "before-recall" / "mnemosyne.db"
    offline_dir = data_dir / "offline"
    if offline_dir.exists():
        shutil.rmtree(offline_dir)
    offline_dir.mkdir()
    shutil.copy2(snapshot_db, offline_dir / "mnemosyne.db")
    control = json.loads((data_dir / "control.json").read_text(encoding="utf-8"))
    id_by_product = {}
    # Kernel ids are not in control.json; recover them from working_memory sessions.
    reader, _db_path = open_memory(offline_dir, READER)
    for row in control:
        found = reader.beam.conn.execute(
            "SELECT id FROM working_memory WHERE session_id = ?",
            (row["session"],),
        ).fetchone()
        id_by_product[row["memory_id"]] = found[0]
    scored = score_queries(reader, id_by_product)
    return {"network_probe": probe, "model": os.environ["MNEMOSYNE_EMBEDDING_MODEL"], **scored}


def owned_count(connection, table: str, column: str, kernel_id: str) -> int | None:
    present = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE name = ?",
        (table,),
    ).fetchone()
    if present is None:
        return None
    return connection.execute(
        f"SELECT COUNT(*) FROM {table} WHERE {column} = ?",
        (kernel_id,),
    ).fetchone()[0]


def run_delete(data_dir: Path) -> dict:
    control = json.loads((data_dir / "control.json").read_text(encoding="utf-8"))
    by_product = {row["memory_id"]: row for row in control}
    drop = by_product[DELETE_TARGET]
    keep = by_product[KEEP_TARGET]
    owner, db_path = open_memory(data_dir, drop["session"])
    connection = owner.beam.conn
    drop_id = connection.execute(
        "SELECT id, rowid FROM working_memory WHERE session_id = ?",
        (drop["session"],),
    ).fetchone()
    keep_id = connection.execute(
        "SELECT id FROM working_memory WHERE session_id = ?",
        (keep["session"],),
    ).fetchone()[0]
    before = {
        "working": owned_count(connection, "working_memory", "id", drop_id["id"]),
        "memories": owned_count(connection, "memories", "id", drop_id["id"]),
        "memory_embeddings": owned_count(connection, "memory_embeddings", "memory_id", drop_id["id"]),
        "fts_working": owned_count(connection, "fts_working", "id", drop_id["id"]),
        "gists": owned_count(connection, "gists", "memory_id", drop_id["id"]),
        "memoria_facts": owned_count(connection, "memoria_facts", "source_memory_id", drop_id["id"]),
        "vec_rowid": drop_id["rowid"],
        "vec_working": owned_count(connection, "vec_working", "rowid", drop_id["rowid"]),
    }
    forgot = owner.forget(drop_id["id"])
    cleaned = cleanup_derived_rows(db_path, drop_id["id"])
    after = {
        "working": owned_count(connection, "working_memory", "id", drop_id["id"]),
        "memories": owned_count(connection, "memories", "id", drop_id["id"]),
        "memory_embeddings": owned_count(connection, "memory_embeddings", "memory_id", drop_id["id"]),
        "fts_working": owned_count(connection, "fts_working", "id", drop_id["id"]),
        "gists": owned_count(connection, "gists", "memory_id", drop_id["id"]),
        "memoria_facts": owned_count(connection, "memoria_facts", "source_memory_id", drop_id["id"]),
        "vec_working": owned_count(connection, "vec_working", "rowid", drop_id["rowid"]),
    }
    keep_row = connection.execute(
        "SELECT content, valid_until FROM working_memory WHERE id = ?",
        (keep_id,),
    ).fetchone()
    keep_vec = connection.execute(
        "SELECT rowid FROM working_memory WHERE id = ?",
        (keep_id,),
    ).fetchone()
    other = []
    for table in ("facts", "graph_edges", "annotations"):
        count = None
        present = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = ?",
            (table,),
        ).fetchone()
        if present is None:
            continue
        columns = [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
        for record in connection.execute(f"SELECT * FROM {table}"):
            blob = " ".join("" if value is None else str(value) for value in record)
            if drop_id["id"] in blob or drop["content"] in blob:
                other.append({"table": table, "columns": columns})
                count = (count or 0) + 1
                break
    return {
        "forgot": forgot,
        "cleaned": cleaned,
        "drop_kernel": drop_id["id"],
        "keep_kernel": keep_id,
        "before": before,
        "after": after,
        "keep_content": keep_row["content"] if keep_row else None,
        "keep_valid_until": keep_row["valid_until"] if keep_row else None,
        "keep_embedding": owned_count(connection, "memory_embeddings", "memory_id", keep_id),
        "keep_vec_working": owned_count(connection, "vec_working", "rowid", keep_vec["rowid"]),
        "keep_gists": owned_count(connection, "gists", "memory_id", keep_id),
        "other_tables_with_target_text": other,
    }


def run_filters(data_dir: Path) -> dict:
    expired_session = kernel_session("p03-expired", 1)
    expired_mem, db_path = open_memory(data_dir, expired_session)
    expired_id = remember_within_cap(
        expired_mem,
        db_path,
        expired_session,
        EXPIRED_TEXT,
        LIMIT,
        valid_until="2000-01-01T00:00:00",
    )
    old_session = kernel_session("p03-old-address", 1)
    old_mem, db_path = open_memory(data_dir, old_session)
    old_id = remember_within_cap(old_mem, db_path, old_session, OLD_ADDRESS, LIMIT)
    old_mem.invalidate(old_id)
    reader, _db_path = open_memory(data_dir, READER)
    expired_hits = [row.get("id") for row in reader.recall(EXPIRED_QUERY, top_k=TOP_K)]
    old_hits = [row.get("id") for row in reader.recall(OLD_QUERY, top_k=TOP_K)]
    irrelevant = reader.recall(IRRELEVANT, top_k=20)
    return {
        "expired_id": expired_id,
        "expired_recalled": expired_id in expired_hits,
        "expired_get_content": (reader.get(expired_id) or {}).get("content"),
        "invalidated_id": old_id,
        "invalidated_recalled": old_id in old_hits,
        "invalidated_get_content": (reader.get(old_id) or {}).get("content"),
        "irrelevant_query": IRRELEVANT,
        "irrelevant_returned": len(irrelevant),
        "irrelevant_ids": [row.get("id") for row in irrelevant],
    }


def child(mode: str, data_dir: Path) -> int:
    if mode == "online":
        payload = run_online(data_dir)
    elif mode == "offline":
        payload = run_offline(data_dir)
    elif mode == "filters":
        payload = run_filters(data_dir)
    elif mode == "delete":
        payload = run_delete(data_dir)
    else:
        raise SystemExit(f"unknown mode {mode}")
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / f"{mode}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    emit({"mode": mode, "status": payload.get("status", "ok"), "path": str(data_dir / f"{mode}.json")})
    return 0 if payload.get("status") != "IMPORT_FAILED" else 1


def spawn(mode: str, data_dir: Path, model: str, offline: bool) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), mode, str(data_dir)],
        cwd=str(SPIKE_ROOT),
        env=local_env(data_dir, model, offline),
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def load_child(data_dir: Path, mode: str, completed: subprocess.CompletedProcess[str]) -> dict:
    path = data_dir / f"{mode}.json"
    payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    payload["exit_code"] = completed.returncode
    if completed.returncode != 0:
        payload["stderr_tail"] = (completed.stderr or "")[-2000:]
        payload["stdout_tail"] = (completed.stdout or "")[-1000:]
    return payload


def run_config(model: str, folder: str) -> dict:
    data_dir = RUNS / folder
    online = spawn("online", data_dir, model, offline=False)
    online_payload = load_child(data_dir, "online", online)
    result = {"model": model, "online": online_payload}
    if online_payload.get("status") != "SCORED":
        return result
    offline = spawn("offline", data_dir, model, offline=True)
    result["offline"] = load_child(data_dir, "offline", offline)
    filters = spawn("filters", data_dir, model, offline=False)
    result["filters"] = load_child(data_dir, "filters", filters)
    deleted = spawn("delete", data_dir, model, offline=False)
    result["delete"] = load_child(data_dir, "delete", deleted)
    return result


def accept(config: dict) -> bool:
    online = config.get("online") or {}
    offline = config.get("offline") or {}
    filters = config.get("filters") or {}
    deleted = config.get("delete") or {}
    combined = (online.get("combined") or {})
    offline_combined = (offline.get("combined") or {})
    after = deleted.get("after") or {}
    return bool(
        combined.get("pass_80")
        and combined.get("vector_pass_80")
        and offline_combined.get("pass_80")
        and str(offline.get("network_probe", "")).startswith("blocked:")
        and filters.get("expired_recalled") is False
        and filters.get("invalidated_recalled") is False
        and filters.get("expired_get_content")
        and filters.get("invalidated_get_content")
        and deleted.get("forgot") is True
        and after.get("working") == 0
        and after.get("memory_embeddings") == 0
        and after.get("vec_working") == 0
        and after.get("gists") == 0
        and after.get("memoria_facts") == 0
        and deleted.get("keep_content")
        and deleted.get("keep_embedding") == 1
        and deleted.get("keep_vec_working") == 1
    )


def main() -> int:
    assert_native_windows()
    assert_python_pin()
    if len(sys.argv) > 1:
        os.environ.update(local_env(Path(sys.argv[2]), os.environ["MNEMOSYNE_EMBEDDING_MODEL"], sys.argv[1] == "offline"))
        if sys.argv[1] == "offline":
            block_network()
        return child(sys.argv[1], Path(sys.argv[2]))

    RUNS.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    first = run_config(FIRST_MODEL, "zh")
    second = None
    if not accept(first):
        second = run_config(SECOND_MODEL, "multi")
    chosen = second if second and accept(second) else first
    report = {
        "task": "P0.3",
        "first_model": FIRST_MODEL,
        "second_model": SECOND_MODEL if second else "NOT_RUN",
        "first": slim(first),
        "second": slim(second) if second else None,
        "accepted_model": chosen.get("model"),
        "pass": accept(chosen),
    }
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    emit({"pass": report["pass"], "result": str(RESULT_PATH), "model": report["accepted_model"]})
    return 0 if report["pass"] else 2


def slim(config: dict | None) -> dict | None:
    if config is None:
        return None
    online = config.get("online") or {}
    offline = config.get("offline") or {}
    return {
        "model": config.get("model"),
        "online_status": online.get("status"),
        "versions": online.get("versions"),
        "index": online.get("index"),
        "kernel_ids": online.get("kernel_ids"),
        "direct": online.get("direct"),
        "rewrite": online.get("rewrite"),
        "combined": online.get("combined"),
        "cases": online.get("cases"),
        "online_error": online.get("error") or online.get("stderr_tail"),
        "offline_network": offline.get("network_probe"),
        "offline_direct": offline.get("direct"),
        "offline_rewrite": offline.get("rewrite"),
        "offline_combined": offline.get("combined"),
        "offline_error": offline.get("stderr_tail"),
        "filters": config.get("filters"),
        "delete": config.get("delete"),
    }


if __name__ == "__main__":
    raise SystemExit(main())
