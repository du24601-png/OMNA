"""P3.3: settings, export, backup, restore, wipe, and interrupted delete."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RESULT_PATH = REPO / "tests" / "results" / "p3_3_windows.json"
CACHE = REPO / "experiments" / "kernel_spike" / "runs" / "p0_3" / "fastembed-cache"
PYTHON = REPO / "server" / ".venv" / "Scripts" / "python.exe"
EVIDENCE = "证据片段甲"
SENTENCE = "合成编号甲蓝封面笔记本只在周日打开"
OTHER = "合成编号乙绿封面笔记本改到周一打开"


class _Extractor(BaseHTTPRequestHandler):
    calls: list[dict] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        auth = self.headers.get("Authorization") or ""
        _Extractor.calls.append({"pong": "Reply with pong." in raw, "auth": auth, "body": raw})
        if "Reply with pong." in raw:
            message = "pong"
        else:
            match = re.search(r"建议：([^\"\\]+)", raw)
            content = match.group(1) if match else "未能解析建议"
            message = json.dumps(
                {
                    "candidates": [
                        {
                            "content": content,
                            "kind": "fact",
                            "category": "preference",
                            "scope": None,
                            "evidence": EVIDENCE,
                        }
                    ]
                },
                ensure_ascii=False,
            )
        body = json.dumps({"choices": [{"message": {"content": message}}]}, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        return


def _call(
    url: str,
    token: str | None = None,
    method: str = "GET",
    payload: dict | None = None,
    raw: bytes | None = None,
    key: str | None = None,
    extra: dict[str, str] | None = None,
    timeout: int = 180,
) -> tuple[int, bytes]:
    headers = {}
    data = raw
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if key is not None:
        headers["Idempotency-Key"] = key
    if extra:
        headers.update(extra)
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _json(status: int, body: bytes) -> dict:
    parsed = json.loads(body.decode("utf-8"))
    if status != 200:
        message = str(parsed.get("error", {}).get("message") or "")
        raise AssertionError(f"unexpected status {status}: {parsed.get('error', {}).get('code')}: {message[:500]}")
    return parsed


def _hidden(blob: bytes, secret: str, where: str) -> None:
    if secret.encode("utf-8") in blob:
        raise AssertionError(f"a secret was visible in {where}")


def _start(port: int, data_dir: Path, token: str, log_path: Path, extra: dict[str, str]) -> subprocess.Popen:
    env = os.environ.copy()
    env["ZHIWO_DATA_DIR"] = str(data_dir)
    env["ZHIWO_OWNER_CREDENTIAL"] = token
    env["ZHIWO_HOST"] = "127.0.0.1"
    env["PYTHONUTF8"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONPATH"] = str(REPO / "server")
    env["HF_HUB_OFFLINE"] = "1"
    for name in (
        "ZHIWO_KERNEL_CONNECT_ONLY",
        "ZHIWO_EXTRACTOR_BASE_URL",
        "ZHIWO_EXTRACTOR_MODEL",
        "ZHIWO_EXTRACTOR_API_KEY",
        "ZHIWO_FASTEMBED_CACHE_DIR",
        "ZHIWO_CRASH_AFTER",
        "ZHIWO_TEST_MODE",
    ):
        env.pop(name, None)
    env.update(extra)
    log = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [str(PYTHON), "-m", "uvicorn", "zhiwo.api.app:app", "--host", "127.0.0.1", "--port", str(port)],
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
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


def _wait_ok(url: str, proc: subprocess.Popen, log_path: Path) -> None:
    deadline = time.time() + 180
    last = ""
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        try:
            status, body = _call(url, timeout=5)
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            last = str(exc)
            time.sleep(0.4)
            continue
        if status == 200:
            return
        last = body.decode("utf-8", errors="replace")[:300]
        time.sleep(0.4)
    detail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
    raise AssertionError(f"service did not become ready: {last}\n{detail}")


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _kernel_count(kernel_db: Path, content: str) -> int:
    connection = sqlite3.connect(kernel_db)
    try:
        return int(connection.execute("SELECT COUNT(*) FROM working_memory WHERE content = ?", (content,)).fetchone()[0])
    finally:
        connection.close()


def _save_memory(base: str, token: str, content: str, source_refs: list[str] | None = None) -> str:
    body = {"content": content, "kind": "fact", "category": "preference", "share_enabled": True}
    if source_refs:
        body["source_refs"] = source_refs
    saved = _json(*_call(f"{base}/api/v1/memories", token, "POST", body, key=str(uuid.uuid4())))
    if not saved.get("memory_id"):
        raise AssertionError("memory was not saved")
    return saved["memory_id"]


def _import_source(base: str, token: str, sentence: str) -> str:
    status, body = _call(
        f"{base}/api/v1/imports",
        token,
        "POST",
        {"kind": "paste", "text": f"{EVIDENCE}\n建议：{sentence}"},
        key=str(uuid.uuid4()),
    )
    parsed = json.loads(body.decode("utf-8"))
    if status != 200 or not parsed.get("source_id"):
        raise AssertionError(f"import failed: {status} {parsed.get('error', {}).get('code')}")
    return parsed["source_id"]


def main() -> None:
    if platform.system() != "Windows":
        raise AssertionError("P3.3 requires native Windows")
    if not PYTHON.exists():
        raise AssertionError(f"server interpreter is missing: {PYTHON}")
    if not CACHE.exists():
        raise AssertionError("local embedding cache is missing")
    owner = "p33-owner-" + uuid.uuid4().hex
    env_key = "env-key-" + uuid.uuid4().hex
    ui_key = "ui-key-" + uuid.uuid4().hex
    result = {
        "task": "P3.3",
        "platform": platform.platform(),
        "pass": False,
    }
    with tempfile.TemporaryDirectory(prefix="zhiwo-p33-", ignore_cleanup_errors=True) as raw:
        root = Path(raw)
        cache_dir = root / "fastembed-cache"
        shutil.copytree(CACHE, cache_dir)
        data_dir = root / "data"
        data_dir.mkdir()
        outside = root / "kept-backup.zip"
        logs = root / "logs"
        logs.mkdir()
        stub_port = _port()
        stub = ThreadingHTTPServer(("127.0.0.1", stub_port), _Extractor)
        threading.Thread(target=stub.serve_forever, daemon=True).start()
        extra = {
            "ZHIWO_FASTEMBED_CACHE_DIR": str(cache_dir),
            "ZHIWO_EXTRACTOR_BASE_URL": f"http://127.0.0.1:{stub_port}",
            "ZHIWO_EXTRACTOR_MODEL": "test-extractor",
            "ZHIWO_EXTRACTOR_API_KEY": env_key,
        }
        port = _port()
        base = f"http://127.0.0.1:{port}"
        proc = _start(port, data_dir, owner, logs / "first.log", extra)
        try:
            _wait_ok(f"{base}/health", proc, logs / "first.log")
            before = _json(*_call(f"{base}/api/v1/settings", owner))
            before_bytes = json.dumps(before).encode("utf-8")
            _hidden(before_bytes, env_key, "settings before save")
            _hidden(before_bytes, ui_key, "settings before save")
            if before["extractor"]["key_saved"] or not before["extractor"]["configured"]:
                raise AssertionError("environment extractor was not reported without a saved key")
            if before["schema_version"] != 8:
                raise AssertionError("schema version changed")
            saved = _json(
                *_call(
                    f"{base}/api/v1/settings",
                    owner,
                    "PATCH",
                    {
                        "extractor_base_url": extra["ZHIWO_EXTRACTOR_BASE_URL"],
                        "extractor_model": "test-extractor",
                        "extractor_api_key": ui_key,
                    },
                    key=str(uuid.uuid4()),
                )
            )
            saved_bytes = json.dumps(saved).encode("utf-8")
            _hidden(saved_bytes, ui_key, "settings response")
            _hidden(saved_bytes, env_key, "settings response")
            if not saved["extractor"]["key_saved"] or not saved["extractor"]["configured"]:
                raise AssertionError("saved extractor was not marked configured")
            control_db = data_dir / "zhiwo.db"
            _hidden(control_db.read_bytes(), ui_key, "zhiwo.db")
            _hidden(control_db.read_bytes(), env_key, "zhiwo.db")
            key_file = data_dir / "extractor.key"
            if not key_file.is_file():
                raise AssertionError("extractor key file was not written")
            _hidden(key_file.read_bytes(), ui_key, "extractor.key")
            tested = _json(*_call(f"{base}/api/v1/settings/test-model", owner, "POST", key=str(uuid.uuid4())))
            if tested.get("ok") is not True:
                raise AssertionError("model test did not succeed")
            pong = [item for item in _Extractor.calls if item["pong"]]
            if not pong or pong[-1]["auth"] != f"Bearer {ui_key}" or SENTENCE in pong[-1]["body"]:
                raise AssertionError("model test did not use the saved key without memory text")
            memory_id = _save_memory(base, owner, SENTENCE)
            source_id = _import_source(base, owner, SENTENCE)
            other_id = _save_memory(base, owner, OTHER, [source_id])
            preview = _json(*_call(f"{base}/api/v1/memories/{memory_id}/deletion-preview", owner))
            if source_id not in {item["id"] for item in preview["sources"]}:
                raise AssertionError("deletion preview did not list the related source")
            export_status, export_bytes = _call(f"{base}/api/v1/exports", owner, "POST", key=str(uuid.uuid4()))
            if export_status != 200:
                raise AssertionError("export failed")
            _hidden(export_bytes, ui_key, "export")
            _hidden(export_bytes, env_key, "export")
            with zipfile.ZipFile(BytesIO(export_bytes)) as archive:
                exported = archive.read("memories.json")
            if SENTENCE.encode("utf-8") not in exported:
                raise AssertionError("export did not include the saved memory")
            created = _json(*_call(f"{base}/api/v1/agents", owner, "POST", {"name": "合成连接甲"}, key=str(uuid.uuid4())))
            credential = created["credential"]
            _hidden(export_bytes, credential, "export")
            patched = _call(
                f"{base}/api/v1/agents/{created['id']}",
                owner,
                "PATCH",
                {"allowed_tools": ["search_memory"], "allowed_categories": ["preference"]},
                key=str(uuid.uuid4()),
            )
            if patched[0] != 200:
                raise AssertionError("agent permissions were not saved")
            before_restore = _call(
                f"{base}/api/v1/agent/tools/search_memory",
                credential,
                "POST",
                {"query": SENTENCE},
            )
            if before_restore[0] != 200:
                raise AssertionError("agent credential did not work before restore")
            backup_status, backup_bytes = _call(f"{base}/api/v1/backups", owner, "POST", key=str(uuid.uuid4()))
            if backup_status != 200:
                raise AssertionError("backup failed")
            _hidden(backup_bytes, ui_key, "backup")
            _hidden(backup_bytes, credential, "backup")
            outside.write_bytes(backup_bytes)
            restored = _json(
                *_call(
                    f"{base}/api/v1/restores?" + urllib.parse.urlencode({"confirm": "恢复备份"}),
                    owner,
                    "POST",
                    raw=backup_bytes,
                    key=str(uuid.uuid4()),
                )
            )
            if restored.get("status") != "restored":
                raise AssertionError("restore did not report success")
            after = _json(*_call(f"{base}/api/v1/settings", owner))
            if after["extractor"]["configured"] or after["extractor"]["key_saved"] or key_file.exists():
                raise AssertionError("restore kept the extractor key or the environment key")
            _hidden(json.dumps(after).encode("utf-8"), env_key, "settings after restore")
            agent_after = _call(
                f"{base}/api/v1/agent/tools/search_memory",
                credential,
                "POST",
                {"query": SENTENCE},
            )
            agent_body = json.loads(agent_after[1].decode("utf-8"))
            if agent_after[0] != 401 or agent_body.get("error", {}).get("code") != "UNAUTHENTICATED":
                raise AssertionError("old agent credential still worked after restore")
            agents = _json(*_call(f"{base}/api/v1/agents", owner))
            if any(item.get("enabled") for item in agents["agents"]):
                raise AssertionError("an agent stayed enabled after restore")
            listed = _json(*_call(f"{base}/api/v1/memories?limit=50", owner))
            if not any(item["id"] == memory_id for item in listed["items"]):
                raise AssertionError("restore did not bring the memory back")
            reset = _json(
                *_call(
                    f"{base}/api/v1/data/reset",
                    owner,
                    "POST",
                    {"confirm": "清空数据"},
                    key=str(uuid.uuid4()),
                )
            )
            if reset.get("status") != "reset":
                raise AssertionError("wipe did not report success")
            emptied = _json(*_call(f"{base}/api/v1/memories?" + urllib.parse.urlencode({"query": SENTENCE}), owner))
            if emptied["items"]:
                raise AssertionError("a memory remained after wipe")
            if not outside.is_file() or outside.read_bytes() != backup_bytes:
                raise AssertionError("the backup saved outside the data directory was removed or changed")
            result["settings_key_hidden"] = True
            result["export_has_no_key"] = True
            result["restore_invalidates_agent"] = True
            result["wipe_removes_memories"] = True
            result["outside_backup_kept"] = True
            result["preview_lists_source"] = True
            result["other_memory_id_present_before_wipe"] = bool(other_id)
        finally:
            _stop(proc)
            stub.shutdown()

        delete_dir = root / "delete-data"
        delete_dir.mkdir()
        delete_port = _port()
        delete_base = f"http://127.0.0.1:{delete_port}"
        delete_extra = {
            **extra,
            "ZHIWO_TEST_MODE": "1",
            "ZHIWO_CRASH_AFTER": "delete_marked",
        }
        delete_proc = _start(delete_port, delete_dir, owner, logs / "delete.log", delete_extra)
        crashed = False
        try:
            _wait_ok(f"{delete_base}/health", delete_proc, logs / "delete.log")
            doomed = _save_memory(delete_base, owner, SENTENCE)
            source_id = _import_source(delete_base, owner, SENTENCE)
            kept = _save_memory(delete_base, owner, OTHER, [source_id])
            preview = _json(*_call(f"{delete_base}/api/v1/memories/{doomed}/deletion-preview", owner))
            if source_id not in {item["id"] for item in preview["sources"]}:
                raise AssertionError("deletion preview missed the source on the crash run")
            try:
                _call(
                    f"{delete_base}/api/v1/memories/{doomed}",
                    owner,
                    "DELETE",
                    {"confirm": True},
                    key=str(uuid.uuid4()),
                    timeout=30,
                )
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
                crashed = True
            else:
                crashed = delete_proc.poll() is not None
            deadline = time.time() + 20
            while delete_proc.poll() is None and time.time() < deadline:
                time.sleep(0.2)
            if delete_proc.poll() is None:
                raise AssertionError("delete crash did not stop the process")
            result["delete_process_exit"] = delete_proc.returncode
        finally:
            _stop(delete_proc)
        if not crashed and result.get("delete_process_exit") not in {86, -86}:
            raise AssertionError("delete did not interrupt the process")
        resume_port = _port()
        resume_base = f"http://127.0.0.1:{resume_port}"
        resume_proc = _start(resume_port, delete_dir, owner, logs / "resume.log", extra)
        try:
            _wait_ok(f"{resume_base}/health", resume_proc, logs / "resume.log")
            missing, missing_body = _call(f"{resume_base}/api/v1/memories/{doomed}", owner)
            if missing != 404:
                raise AssertionError("deleted memory was still readable after restart")
            kept_status, kept_body = _call(f"{resume_base}/api/v1/memories/{kept}", owner)
            kept_memory = json.loads(kept_body.decode("utf-8"))
            if kept_status != 200 or SENTENCE in json.dumps(kept_memory, ensure_ascii=False):
                raise AssertionError("the other memory did not survive deletion")
            source_status, source_body = _call(f"{resume_base}/api/v1/sources/{source_id}", owner)
            source_parsed = json.loads(source_body.decode("utf-8"))
            if source_status != 404 or source_parsed.get("error", {}).get("message") != "source not found":
                raise AssertionError("the related source was not removed")
            kernel_db = delete_dir / "kernel" / "mnemosyne.db"
            if _kernel_count(kernel_db, SENTENCE) != 0:
                raise AssertionError("deleted text remained in the memory store")
            connection = sqlite3.connect(delete_dir / "zhiwo.db")
            try:
                rows = connection.execute(
                    "SELECT status, payload_json FROM operations WHERE action = 'delete'"
                ).fetchall()
                source_left = connection.execute("SELECT COUNT(*) FROM sources WHERE id = ?", (source_id,)).fetchone()[0]
            finally:
                connection.close()
            if source_left:
                raise AssertionError("source row remained")
            if len(rows) != 1 or rows[0][0] != "completed":
                raise AssertionError("delete did not finish after restart")
            if SENTENCE.encode("utf-8") in (rows[0][1] or "").encode("utf-8"):
                raise AssertionError("delete operation still stored the memory text")
            result["delete_resumed"] = True
            result["other_memory_kept"] = True
            result["source_removed"] = True
        finally:
            _stop(resume_proc)
    result["pass"] = True
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"pass": True, "result": str(RESULT_PATH)}))


if __name__ == "__main__":
    main()
