"""P1.1: service start, migration, Owner auth, and an independent Kernel directory."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "server"))

from pydantic import ValidationError  # noqa: E402

from zhiwo.contracts.memory import MemoryRef  # noqa: E402
from zhiwo.repositories.migrate import migrate  # noqa: E402

RESULT_PATH = REPO / "tests" / "results" / "p1_1_windows.json"
SPIKE = REPO / "experiments" / "kernel_spike"


def _request(url: str, token: str | None = None) -> tuple[int, str]:
    headers = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def _wait_ok(url: str, proc: subprocess.Popen, log_path: Path) -> None:
    deadline = time.time() + 40
    last = ""
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        try:
            status, body = _request(url)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = str(exc)
            time.sleep(0.2)
            continue
        if status == 200:
            return
        last = body
        time.sleep(0.2)
    detail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
    raise AssertionError(f"service did not become ready: {last}\n{detail}")


def _fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    if not root.exists():
        return "missing"
    for current, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name not in {".venv", "__pycache__"}]
        for name in files:
            path = Path(current) / name
            if path.suffix not in {".db", ".json", ".py"} and name != "control.json":
                continue
            stat = path.stat()
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(str(stat.st_size).encode())
            digest.update(str(stat.st_mtime_ns).encode())
    return digest.hexdigest()


def _hermes_db() -> Path | None:
    candidates = []
    hermes = os.environ.get("HERMES_HOME", "").strip()
    if hermes:
        candidates.append(Path(hermes))
    local_app = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app:
        candidates.append(Path(local_app) / "hermes")
    candidates.append(Path.home() / ".hermes")
    for root in candidates:
        db_path = root / "mnemosyne" / "data" / "mnemosyne.db"
        if db_path.exists():
            return db_path
    return None


def _start(port: int, data_dir: Path, token: str, log_path: Path) -> subprocess.Popen:
    env = os.environ.copy()
    env["ZHIWO_DATA_DIR"] = str(data_dir)
    env["ZHIWO_OWNER_CREDENTIAL"] = token
    env["ZHIWO_HOST"] = "127.0.0.1"
    env["ZHIWO_KERNEL_CONNECT_ONLY"] = "1"
    env.pop("ZHIWO_EXTRACTOR_API_KEY", None)
    env["PYTHONUTF8"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONPATH"] = str(REPO / "server")
    log = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "zhiwo.api.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(REPO / "server"),
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    log.close()
    return proc


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


def test_repeat_migration_and_control_contract() -> None:
    if sys.platform != "win32":
        raise AssertionError("P1.1 requires native Windows")
    with tempfile.TemporaryDirectory(prefix="zhiwo-p11-migrate-") as raw:
        db_path = Path(raw) / "zhiwo.db"
        assert migrate(db_path) == 4
        assert migrate(db_path) == 4
        connection = sqlite3.connect(db_path)
        try:
            versions = connection.execute("SELECT version, name FROM schema_migrations").fetchall()
            assert versions == [
                (1, "control_identity"),
                (2, "sources_and_publish"),
                (3, "operation_payload"),
                (4, "review_intent"),
            ]
            columns = [row[1] for row in connection.execute("PRAGMA table_info(memory_refs)")]
            assert "content" not in columns
            assert {"kind", "category", "scope", "lifecycle", "revision", "kernel_id"} <= set(columns)
            connection.execute(
                """
                INSERT INTO memory_refs (
                    memory_id, revision, kernel_id, kernel_session, kind, category,
                    scope, lifecycle, share_enabled, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "mem-1",
                    1,
                    "kernel-1",
                    "zhiwo:mem-1:r1",
                    "fact",
                    "preference",
                    "周末骑行",
                    "active",
                    0,
                    "2026-09-25T00:00:00+00:00",
                ),
            )
            connection.commit()
            for kind, category in (("unspecified", "preference"), ("fact", "运动")):
                try:
                    connection.execute(
                        """
                        INSERT INTO memory_refs (
                            memory_id, revision, kernel_session, kind, category,
                            lifecycle, share_enabled, created_at
                        ) VALUES ('mem-bad', 1, 'zhiwo:mem-bad:r1', ?, ?, 'active', 0, '2026-09-25T00:00:00+00:00')
                        """,
                        (kind, category),
                    )
                    connection.commit()
                except sqlite3.IntegrityError:
                    continue
                raise AssertionError(f"accepted experiment label kind={kind} category={category}")
            try:
                connection.execute(
                    """
                    INSERT INTO memory_refs (
                        memory_id, revision, kernel_session, kind, category,
                        lifecycle, share_enabled, created_at
                    ) VALUES ('mem-open', 1, 'zhiwo:mem-open:r1', 'fact', 'preference', 'active', 0, '2026-09-25T00:00:00+00:00')
                    """
                )
                connection.commit()
            except sqlite3.IntegrityError:
                connection.rollback()
            else:
                raise AssertionError("active memory_ref accepted a missing kernel_id")
            try:
                connection.execute(
                    """
                    INSERT INTO memory_refs (
                        memory_id, revision, kernel_id, kernel_session, kind, category,
                        lifecycle, share_enabled, created_at
                    ) VALUES ('mem-1', 2, 'kernel-2', 'zhiwo:mem-1:r2', 'fact', 'preference', 'active', 0, '2026-09-25T00:00:00+00:00')
                    """
                )
                connection.commit()
            except sqlite3.IntegrityError:
                connection.rollback()
            else:
                raise AssertionError("a second active version was accepted")
        finally:
            connection.close()
    for relative in ("zhiwo/api/app.py", "zhiwo/adapters/kernel_client.py", "zhiwo/repositories/migrate.py"):
        text = (REPO / "server" / relative).read_text(encoding="utf-8")
        if "memory_search" in text or "control.json" in text or "kernel_spike" in text:
            raise AssertionError(f"{relative} depends on the experiment path")
    try:
        MemoryRef(
            memory_id="mem-1",
            revision=1,
            kernel_session="zhiwo:mem-1:r1",
            kind="fact",
            category="preference",
            scope="周末骑行",
            lifecycle="active",
            content="should not be stored here",
        )
    except ValidationError:
        return
    raise AssertionError("MemoryRef accepted a body field")


def test_service_start_restart_auth_and_adapter() -> None:
    if sys.platform != "win32" or platform.system() != "Windows" or os.environ.get("WSL_DISTRO_NAME"):
        raise AssertionError("P1.1 requires native Windows")
    before = _fingerprint(SPIKE)
    hermes = _hermes_db()
    hermes_before = hermes.stat().st_mtime_ns if hermes else None
    token = "p11-owner-" + secrets.token_urlsafe(24)
    wrong = "p11-wrong-owner"
    with tempfile.TemporaryDirectory(prefix="zhiwo-p11-svc-") as raw:
        data_dir = Path(raw)
        logs = data_dir / "logs"
        logs.mkdir()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        first_log = logs / "first.log"
        first = _start(port, data_dir, token, first_log)
        try:
            _wait_ok(f"{base}/health", first, first_log)
            public_status, public_body = _request(f"{base}/health")
            wrong_status, wrong_body = _request(f"{base}/api/v1/health", wrong)
            missing_status, _missing_body = _request(f"{base}/api/v1/health")
            good_status, good_body = _request(f"{base}/api/v1/health", token)
            first_pid = first.pid
        finally:
            _stop(first)
        time.sleep(0.5)
        second_log = logs / "second.log"
        second = _start(port, data_dir, token, second_log)
        try:
            _wait_ok(f"{base}/health", second, second_log)
            restart_status, restart_body = _request(f"{base}/api/v1/health", token)
            second_pid = second.pid
        finally:
            _stop(second)
        log_text = first_log.read_text(encoding="utf-8", errors="replace")
        log_text += second_log.read_text(encoding="utf-8", errors="replace")
        good = json.loads(good_body)
        restarted = json.loads(restart_body)
        kernel_db = Path(good["kernel"]["db_path"])
        cache = data_dir / "kernel" / "fastembed-cache"
        large_cache = [
            str(path)
            for path in cache.rglob("*")
            if path.is_file() and path.stat().st_size > 1000
        ] if cache.exists() else []
        connection = sqlite3.connect(data_dir / "zhiwo.db")
        try:
            migration_rows = connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
            ref_rows = connection.execute("SELECT COUNT(*) FROM memory_refs").fetchone()[0]
        finally:
            connection.close()
        result = {
            "task": "P1.1",
            "platform": platform.platform(),
            "python": platform.python_version(),
            "pass": True,
            "public_health": public_status,
            "start_pid": first_pid,
            "restart_pid": second_pid,
            "pid_changed": first_pid != second_pid,
            "restart_status": restart_status,
            "schema_version": good["schema_version"],
            "restart_schema_version": restarted["schema_version"],
            "migration_rows": migration_rows,
            "memory_ref_count": ref_rows,
            "wrong_owner_status": wrong_status,
            "missing_owner_status": missing_status,
            "wrong_owner_code": json.loads(wrong_body)["error"]["code"],
            "wrong_owner_has_kernel_path": str(data_dir) in wrong_body,
            "protected_status": good_status,
            "kernel_version": good["kernel"]["version"],
            "kernel_under_data_dir": kernel_db.is_relative_to(data_dir.resolve()),
            "embeddings_loaded": good["embeddings_loaded"],
            "embedding_model": good["embedding_model"],
            "large_cache_files": large_cache,
            "experiment_unchanged": _fingerprint(SPIKE) == before,
            "hermes_unchanged": (hermes.stat().st_mtime_ns if hermes else None) == hermes_before,
            "credential_in_logs": token in log_text,
            "public_body_has_kernel": "kernel" in json.loads(public_body),
        }
        result["pass"] = all(
            [
                public_status == 200,
                result["pid_changed"],
                restart_status == 200,
                good["schema_version"] == 4,
                restarted["schema_version"] == 4,
                migration_rows == 4,
                ref_rows == 0,
                wrong_status == 401,
                missing_status == 401,
                result["wrong_owner_code"] == "UNAUTHENTICATED",
                not result["wrong_owner_has_kernel_path"],
                good_status == 200,
                good["kernel"]["version"] == "3.15.1",
                result["kernel_under_data_dir"],
                good["embeddings_loaded"] is False,
                good["embedding_model"] == "BAAI/bge-small-zh-v1.5",
                not large_cache,
                result["experiment_unchanged"],
                result["hermes_unchanged"],
                not result["credential_in_logs"],
                not result["public_body_has_kernel"],
                "experiments" not in str(kernel_db),
            ]
        )
        RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
        RESULT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if not result["pass"]:
            raise AssertionError(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    test_repeat_migration_and_control_contract()
    test_service_start_restart_auth_and_adapter()
    print("OK")
