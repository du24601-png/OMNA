"""D1: a short-lived Kernel write must not close the service handle's connection.

Uses the real Mnemosyne package in connect-only mode (no embedding model).
Before the fix, a write or delete on the thread that opened the handle closed
the handle's shared connection, and every later search failed with
"Cannot operate on a closed database" until the service restarted.
"""

from __future__ import annotations

import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from zhiwo.adapters import kernel_client as kc  # noqa: E402


def _handle(root: Path):
    return kc.connect(root / "kernel", cache_dir=root / "cache", connect_only=True)


def test_same_thread_write_keeps_handle_open() -> None:
    with tempfile.TemporaryDirectory(prefix="zhiwo-kernel-handle-", ignore_cleanup_errors=True) as raw:
        handle = _handle(Path(raw))
        kernel_id = kc.write_version(handle.db_path, "zhiwo:h1:r1", "句柄探针：周末骑车。", None)
        assert kc.read_version(handle.db_path, "zhiwo:h1:r1", kernel_id) == "句柄探针：周末骑车。"
        rows = kc.recall_rows(handle, "周末骑车")
        assert any(row["id"] == kernel_id for row in rows)


def test_startup_delete_then_worker_searches() -> None:
    """Like resume_deletions on the startup thread, then requests on worker threads."""
    with tempfile.TemporaryDirectory(prefix="zhiwo-kernel-handle-", ignore_cleanup_errors=True) as raw:
        handle = _handle(Path(raw))
        doomed = kc.write_version(handle.db_path, "zhiwo:h2:r1", "句柄探针：要删的一条。", None)
        kc.discard_version(handle.db_path, "zhiwo:h2:r1", doomed)

        def job(index: int) -> int:
            kc.write_version(handle.db_path, f"zhiwo:w{index}:r1", f"句柄探针：第 {index} 条早起记录。", None)
            return len(kc.recall_rows(handle, "早起记录"))

        with ThreadPoolExecutor(4) as pool:
            counts = list(pool.map(job, range(6)))
        assert all(count >= 1 for count in counts)
        assert len(kc.recall_rows(handle, "早起记录")) >= 6


if __name__ == "__main__":
    test_same_thread_write_keeps_handle_open()
    test_startup_delete_then_worker_searches()
    print("OK")
