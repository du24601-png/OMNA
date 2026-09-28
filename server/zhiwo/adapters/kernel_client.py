"""Open Mnemosyne against a caller-supplied directory.

Official writes load the frozen local embedding model. The no-embedding
flags are only applied when the process is explicitly in connect-only mode.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from zhiwo.adapters.derived_cleanup import REQUIRED_VERSION, cleanup_derived_rows
from zhiwo.adapters.kernel_session import kernel_session
from zhiwo.adapters.retention import remember_within_cap

_log = logging.getLogger(__name__)
SERVICE_SESSION = "zhiwo-service"
EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"
SESSION_CAP = 1_000_000
_DROPPED_ENV = (
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
_EMBEDDING_OFF = (
    "MNEMOSYNE_NO_EMBEDDINGS",
    "MNEMOSYNE_EMBEDDINGS_OFF",
    "MNEMOSYNE_SKIP_EMBEDDINGS",
)


class KernelConnectError(RuntimeError):
    """The Adapter could not open the requested directory."""


@dataclass
class SessionRow:
    kernel_id: str
    content: str


@dataclass
class KernelHandle:
    version: str
    db_path: Path
    embeddings_loaded: bool
    connect_only: bool
    memory: object

    def session_for(self, memory_id: str, revision: int) -> str:
        return kernel_session(memory_id, revision)


def connect(kernel_dir: Path, *, cache_dir: Path, connect_only: bool) -> KernelHandle:
    kernel_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    db_path = (kernel_dir / "mnemosyne.db").resolve()
    _isolate(kernel_dir, cache_dir, connect_only=connect_only)
    import mnemosyne
    from mnemosyne import Mnemosyne
    from mnemosyne.core import embeddings as embedding_module

    if mnemosyne.__version__ != REQUIRED_VERSION:
        raise KernelConnectError(f"kernel version {mnemosyne.__version__} is not {REQUIRED_VERSION}")
    if embedding_module._DEFAULT_MODEL != EMBEDDING_MODEL:
        raise KernelConnectError("embedding model was fixed after Mnemosyne had already been imported")
    memory = Mnemosyne(session_id=SERVICE_SESSION, db_path=db_path)
    opened = Path(memory.db_path).resolve()
    if opened != db_path or not opened.is_relative_to(kernel_dir.resolve()):
        raise KernelConnectError("kernel database is outside the requested directory")
    loaded = False
    if not connect_only:
        loaded = _probe_embeddings(embedding_module)
    return KernelHandle(
        version=mnemosyne.__version__,
        db_path=opened,
        embeddings_loaded=loaded,
        connect_only=connect_only,
        memory=memory,
    )


def recall_rows(handle: KernelHandle, query: str, top_k: int = 40) -> list[dict]:
    rows = handle.memory.recall(query, top_k=top_k)
    if isinstance(rows, dict):
        rows = rows.get("results") or []
    found = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        found.append({"id": str(row.get("id") or ""), "content": str(row.get("content") or "")})
    return found


def session_rows(db_path: Path, session_id: str) -> list[SessionRow]:
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(
            "SELECT id, content FROM working_memory WHERE session_id = ?",
            (session_id,),
        ).fetchall()
    finally:
        connection.close()
    return [SessionRow(kernel_id=str(row[0]), content=str(row[1])) for row in rows]


def has_embedding(db_path: Path, kernel_id: str) -> bool:
    connection = sqlite3.connect(db_path)
    try:
        row = connection.execute(
            "SELECT COUNT(*) FROM memory_embeddings WHERE memory_id = ?",
            (kernel_id,),
        ).fetchone()
        return bool(row and row[0])
    finally:
        connection.close()


def write_version(db_path: Path, session_id: str, content: str, valid_until: str | None) -> str:
    from mnemosyne import Mnemosyne

    memory = Mnemosyne(session_id=session_id, db_path=db_path)
    try:
        kwargs = {}
        if valid_until:
            kwargs["valid_until"] = valid_until
        kernel_id = remember_within_cap(
            memory,
            db_path,
            session_id,
            content,
            SESSION_CAP,
            **kwargs,
        )
        if not kernel_id:
            raise KernelConnectError("kernel did not accept the memory")
        return str(kernel_id)
    finally:
        _close_transient(memory)


def read_version(db_path: Path, session_id: str, kernel_id: str) -> str | None:
    from mnemosyne import Mnemosyne

    memory = Mnemosyne(session_id=session_id, db_path=db_path)
    try:
        row = memory.get(kernel_id)
        if not row:
            return None
        return str(row.get("content") or "")
    finally:
        _close_transient(memory)


def discard_version(db_path: Path, session_id: str, kernel_id: str) -> None:
    from mnemosyne import Mnemosyne

    memory = Mnemosyne(session_id=session_id, db_path=db_path)
    try:
        memory.forget(kernel_id)
        cleanup_derived_rows(db_path, kernel_id)
    finally:
        _close_transient(memory)


def _close_transient(memory) -> None:
    """Drop the short-lived Kernel connection on this thread.

    Mnemosyne keeps that connection in thread-local storage. Leaving it open
    holds the database file, and Windows then refuses to replace a backup.
    """
    import mnemosyne.core.beam as beam
    import mnemosyne.core.memory as memory_module

    for connection in (
        getattr(memory, "conn", None),
        getattr(getattr(memory, "beam", None), "conn", None),
    ):
        if connection is None:
            continue
        try:
            connection.close()
        except sqlite3.Error:
            pass
    memory.conn = None
    beam_memory = getattr(memory, "beam", None)
    if beam_memory is not None:
        beam_memory.conn = None
    for module in (memory_module, beam):
        local = getattr(module, "_thread_local", None)
        if local is None or getattr(local, "conn", None) is None:
            continue
        try:
            local.conn.close()
        except sqlite3.Error:
            pass
        local.conn = None


def _probe_embeddings(embedding_module) -> bool:
    try:
        vector = embedding_module.embed(["就绪"])
    except Exception as exc:
        _log.warning("local embedding model failed to load: %s: %s", type(exc).__name__, exc)
        return False
    if vector is not None and len(vector) == 1:
        return True
    # Mnemosyne returns None when fastembed cannot be imported and drops the reason.
    try:
        from fastembed import TextEmbedding  # noqa: F401
    except Exception as exc:
        _log.warning("local embedding runtime could not be imported: %s: %s", type(exc).__name__, exc)
    else:
        _log.warning("local embedding model returned no vector")
    return False


def _isolate(kernel_dir: Path, cache_dir: Path, *, connect_only: bool) -> None:
    for key in _DROPPED_ENV:
        os.environ.pop(key, None)
    for key in _EMBEDDING_OFF:
        os.environ.pop(key, None)
    os.environ.update(
        {
            "MNEMOSYNE_DATA_DIR": str(kernel_dir),
            "MNEMOSYNE_PERSONA_FILE": str(kernel_dir / "persona.md"),
            "MNEMOSYNE_FASTEMBED_CACHE_DIR": str(cache_dir),
            "MNEMOSYNE_EMBEDDING_MODEL": EMBEDDING_MODEL,
            "MNEMOSYNE_EMBEDDINGS_VIA_API": "0",
            "MNEMOSYNE_LLM_ENABLED": "0",
            "MNEMOSYNE_HOST_LLM_ENABLED": "0",
            "MNEMOSYNE_FORCE_LOCAL": "1",
            "MNEMOSYNE_SLEEP_MODEL_REFRESH_ENABLED": "false",
            "MNEMOSYNE_WM_TTL_HOURS": "1000000",
            "MNEMOSYNE_WM_MAX_ITEMS": "1000000",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "HF_HUB_OFFLINE": "1",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    if connect_only:
        os.environ.update(
            {
                "MNEMOSYNE_NO_EMBEDDINGS": "1",
                "MNEMOSYNE_EMBEDDINGS_OFF": "1",
                "MNEMOSYNE_SKIP_EMBEDDINGS": "1",
            }
        )
