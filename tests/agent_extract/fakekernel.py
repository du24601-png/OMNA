"""In-memory-ish Kernel stand-in. Same table names the adapter reads directly."""

from __future__ import annotations

import sqlite3
import sys
import uuid
from pathlib import Path

VERSION = "fake-3.15.1"


class _Memory:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def recall(self, query: str, top_k: int = 40):
        return recall(self.db_path, query, top_k)


def _db(db_path: Path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS working_memory (id TEXT PRIMARY KEY, session_id TEXT, content TEXT, valid_until TEXT)"
    )
    connection.execute("CREATE TABLE IF NOT EXISTS memory_embeddings (memory_id TEXT PRIMARY KEY)")
    return connection


def connect(kernel_dir: Path, *, cache_dir: Path, connect_only: bool):
    from zhiwo.adapters.kernel_client import KernelHandle

    kernel_dir = Path(kernel_dir)
    kernel_dir.mkdir(parents=True, exist_ok=True)
    db_path = (kernel_dir / "mnemosyne.db").resolve()
    _db(db_path).close()
    return KernelHandle(
        version=VERSION,
        db_path=db_path,
        embeddings_loaded=not connect_only,
        connect_only=connect_only,
        memory=_Memory(db_path),
    )


def write_version(db_path, session_id: str, content: str, valid_until=None) -> str:
    connection = _db(db_path)
    try:
        kernel_id = uuid.uuid4().hex
        connection.execute(
            "INSERT INTO working_memory (id, session_id, content, valid_until) VALUES (?, ?, ?, ?)",
            (kernel_id, session_id, content, valid_until),
        )
        connection.execute("INSERT INTO memory_embeddings (memory_id) VALUES (?)", (kernel_id,))
        connection.commit()
        return kernel_id
    finally:
        connection.close()


def read_version(db_path, session_id: str, kernel_id: str):
    connection = _db(db_path)
    try:
        row = connection.execute("SELECT content FROM working_memory WHERE id = ?", (kernel_id,)).fetchone()
        return None if row is None else str(row[0])
    finally:
        connection.close()


def discard_version(db_path, session_id: str, kernel_id: str) -> None:
    connection = _db(db_path)
    try:
        connection.execute("DELETE FROM working_memory WHERE id = ?", (kernel_id,))
        connection.execute("DELETE FROM memory_embeddings WHERE memory_id = ?", (kernel_id,))
        connection.commit()
    finally:
        connection.close()


def _grams(text: str) -> set[str]:
    text = "".join(text.split())
    return {text[i : i + 2] for i in range(max(0, len(text) - 1))}


def recall(db_path, query: str, top_k: int = 40) -> list[dict]:
    connection = _db(db_path)
    try:
        rows = connection.execute("SELECT id, content FROM working_memory").fetchall()
    finally:
        connection.close()
    wanted = _grams(query)
    scored = []
    for kernel_id, content in rows:
        score = len(wanted & _grams(content)) / (len(wanted) or 1)
        scored.append((score, kernel_id, content))
    scored.sort(key=lambda item: -item[0])
    return [{"id": kernel_id, "content": content} for _, kernel_id, content in scored[:top_k]]


def recall_rows(handle, query: str, top_k: int = 40) -> list[dict]:
    return recall(handle.db_path, query, top_k)


def install() -> None:
    import zhiwo.adapters.kernel_client as kc

    kc.connect = connect
    kc.write_version = write_version
    kc.read_version = read_version
    kc.discard_version = discard_version
    kc.recall_rows = recall_rows
    kc._close_transient = lambda memory: None
    # Modules that did `from kernel_client import x` before install.
    for name, module in list(sys.modules.items()):
        if not name.startswith("zhiwo.") or module is kc:
            continue
        for attr, fn in (
            ("connect", connect),
            ("write_version", write_version),
            ("read_version", read_version),
            ("discard_version", discard_version),
            ("recall_rows", recall_rows),
        ):
            if hasattr(module, attr):
                setattr(module, attr, fn)


def patch_loaded() -> None:
    """Call again after importing service modules."""
    install()
