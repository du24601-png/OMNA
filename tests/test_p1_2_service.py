"""P1.2: publish one memory, keep import candidates out of search."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "server"))

RESULT_PATH = REPO / "tests" / "results" / "p1_2_windows.json"
SPIKE = REPO / "experiments" / "kernel_spike"
CACHE = SPIKE / "runs" / "p0_3" / "fastembed-cache"
MEMORY = "周末习惯把合成记录写在蓝色笔记本的第一页。"
QUERY = "蓝色笔记本的第一页"
CANDIDATE = "候选人把购物清单贴在冰箱门上。"
EVIDENCE = "证据片段甲"
PYTHON = REPO / "server" / ".venv" / "Scripts" / "python.exe"


def _request(url: str, token: str | None = None, payload: dict | None = None, key: str | None = None) -> tuple[int, str]:
    headers = {}
    data = None
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if key is not None:
        headers["Idempotency-Key"] = key
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if payload is not None else "GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def _wait_ok(url: str, proc: subprocess.Popen, log_path: Path) -> None:
    deadline = time.time() + 120
    last = ""
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        try:
            status, body = _request(url)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = str(exc)
            time.sleep(0.3)
            continue
        if status == 200:
            return
        last = body
        time.sleep(0.3)
    detail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
    raise AssertionError(f"service did not become ready: {last}\n{detail}")


def _start(port: int, data_dir: Path, token: str, log_path: Path, extra: dict[str, str]) -> subprocess.Popen:
    env = os.environ.copy()
    env["ZHIWO_DATA_DIR"] = str(data_dir)
    env["ZHIWO_OWNER_CREDENTIAL"] = token
    env["ZHIWO_HOST"] = "127.0.0.1"
    env["PYTHONUTF8"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONPATH"] = str(REPO / "server")
    for name in (
        "ZHIWO_KERNEL_CONNECT_ONLY",
        "ZHIWO_EXTRACTOR_BASE_URL",
        "ZHIWO_EXTRACTOR_MODEL",
        "ZHIWO_EXTRACTOR_API_KEY",
        "ZHIWO_FASTEMBED_CACHE_DIR",
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


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


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


class _Extractor(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        if "提取失败样本" in raw:
            message = "not-json"
        else:
            message = json.dumps(
                {
                    "candidates": [
                        {
                            "content": CANDIDATE,
                            "kind": "fact",
                            "category": "preference",
                            "scope": "个人记录",
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


def test_publish_import_and_search() -> None:
    if sys.platform != "win32" or platform.system() != "Windows" or os.environ.get("WSL_DISTRO_NAME"):
        raise AssertionError("P1.2 requires native Windows")
    if not CACHE.exists():
        raise AssertionError(f"local embedding cache is missing: {CACHE}")
    before = _fingerprint(SPIKE)
    token = "p12-owner-" + uuid.uuid4().hex
    extractor_key = "p12-extractor-" + uuid.uuid4().hex
    save_key = str(uuid.uuid4())
    with tempfile.TemporaryDirectory(prefix="zhiwo-p12-") as raw:
        root = Path(raw)
        cache_dir = root / "fastembed-cache"
        shutil.copytree(CACHE, cache_dir)
        data_dir = root / "data"
        data_dir.mkdir()
        logs = root / "logs"
        logs.mkdir()
        connect_dir = root / "connect-only"
        connect_dir.mkdir()

        connect_port = _port()
        connect_log = logs / "connect.log"
        connect_proc = _start(
            connect_port,
            connect_dir,
            token,
            connect_log,
            {"ZHIWO_KERNEL_CONNECT_ONLY": "1"},
        )
        try:
            base = f"http://127.0.0.1:{connect_port}"
            _wait_ok(f"{base}/health", connect_proc, connect_log)
            rejected_status, rejected_body = _request(
                f"{base}/api/v1/memories",
                token,
                {"content": MEMORY, "kind": "fact", "category": "preference"},
                save_key,
            )
            huge_status, _huge_body = _request(
                f"{base}/api/v1/imports",
                token,
                {"kind": "paste", "text": "甲" * (1024 * 1024)},
                str(uuid.uuid4()),
            )
            bad_status, _bad_body = _request(
                f"{base}/api/v1/imports",
                token,
                {
                    "kind": "file",
                    "name": "notes.txt",
                    "content_base64": base64.b64encode(b"\xff\xfe").decode("ascii"),
                },
                str(uuid.uuid4()),
            )
        finally:
            _stop(connect_proc)
        time.sleep(0.3)

        embed_port = _port()
        embed_log = logs / "embed.log"
        embed = _start(
            embed_port,
            data_dir,
            token,
            embed_log,
            {"ZHIWO_FASTEMBED_CACHE_DIR": str(cache_dir)},
        )
        try:
            base = f"http://127.0.0.1:{embed_port}"
            _wait_ok(f"{base}/health", embed, embed_log)
            health_status, health_body = _request(f"{base}/api/v1/health", token)
            first_status, first_body = _request(
                f"{base}/api/v1/memories",
                token,
                {
                    "content": MEMORY,
                    "kind": "fact",
                    "category": "preference",
                    "scope": "周末记录",
                },
                save_key,
            )
            second_status, second_body = _request(
                f"{base}/api/v1/memories",
                token,
                {
                    "content": MEMORY,
                    "kind": "fact",
                    "category": "preference",
                    "scope": "周末记录",
                },
                save_key,
            )
            conflict_status, conflict_body = _request(
                f"{base}/api/v1/memories",
                token,
                {"content": "另一条不同的合成记忆。", "kind": "fact", "category": "preference"},
                save_key,
            )
            unavailable_status, unavailable_body = _request(
                f"{base}/api/v1/imports",
                token,
                {"kind": "paste", "text": "这段粘贴在没有提取模型时也要留下来。"},
                str(uuid.uuid4()),
            )
            first_pid = embed.pid
        finally:
            _stop(embed)
        time.sleep(0.5)

        restart_log = logs / "restart.log"
        restart = _start(
            embed_port,
            data_dir,
            token,
            restart_log,
            {"ZHIWO_FASTEMBED_CACHE_DIR": str(cache_dir)},
        )
        try:
            base = f"http://127.0.0.1:{embed_port}"
            _wait_ok(f"{base}/health", restart, restart_log)
            search_url = f"{base}/api/v1/memories?{urllib.parse.urlencode({'query': QUERY})}"
            search_status, search_body = _request(search_url, token)
            restart_pid = restart.pid
        finally:
            _stop(restart)
        time.sleep(0.5)

        stub_port = _port()
        stub = ThreadingHTTPServer(("127.0.0.1", stub_port), _Extractor)
        thread = threading.Thread(target=stub.serve_forever, daemon=True)
        thread.start()
        extract_log = logs / "extract.log"
        extract_proc = _start(
            embed_port,
            data_dir,
            token,
            extract_log,
            {
                "ZHIWO_FASTEMBED_CACHE_DIR": str(cache_dir),
                "ZHIWO_EXTRACTOR_BASE_URL": f"http://127.0.0.1:{stub_port}",
                "ZHIWO_EXTRACTOR_MODEL": "test-extractor",
                "ZHIWO_EXTRACTOR_API_KEY": extractor_key,
            },
        )
        try:
            base = f"http://127.0.0.1:{embed_port}"
            _wait_ok(f"{base}/health", extract_proc, extract_log)
            imports = []
            samples = [
                ("paste", None, f"粘贴来源。{EVIDENCE}。周末会整理纸质笔记。"),
                ("file", "notes.txt", f"文本来源。{EVIDENCE}。这里是 TXT。"),
                ("file", "notes.md", f"# 标题\n{EVIDENCE}\n<script>alert(1)</script>"),
            ]
            for kind, name, text in samples:
                payload = {"kind": kind, "text": text}
                if name:
                    payload["name"] = name
                status, body = _request(f"{base}/api/v1/imports", token, payload, str(uuid.uuid4()))
                imports.append((status, json.loads(body)))
            failed_status, failed_body = _request(
                f"{base}/api/v1/imports",
                token,
                {"kind": "paste", "text": f"提取失败样本。{EVIDENCE}"},
                str(uuid.uuid4()),
            )
            failed = json.loads(failed_body)
            retry_status, retry_body = _request(
                f"{base}/api/v1/imports/{failed['job_id']}/retry",
                token,
                {},
            )
            again_status, again_body = _request(
                f"{base}/api/v1/memories",
                token,
                {
                    "content": MEMORY,
                    "kind": "fact",
                    "category": "preference",
                    "scope": "周末记录",
                },
                save_key,
            )
            candidate_url = f"{base}/api/v1/memories?{urllib.parse.urlencode({'query': CANDIDATE})}"
            candidate_status, candidate_body = _request(candidate_url, token)
            saved_url = f"{base}/api/v1/memories?{urllib.parse.urlencode({'query': QUERY})}"
            saved_status, saved_body = _request(saved_url, token)
        finally:
            _stop(extract_proc)
            stub.shutdown()

        log_text = "".join(path.read_text(encoding="utf-8", errors="replace") for path in logs.glob("*.log"))
        connection = sqlite3.connect(data_dir / "zhiwo.db")
        kernel = sqlite3.connect(data_dir / "kernel" / "mnemosyne.db")
        try:
            active = connection.execute(
                "SELECT COUNT(*) FROM memory_refs WHERE lifecycle = 'active'"
            ).fetchone()[0]
            sources = connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
            proposals = connection.execute("SELECT COUNT(*) FROM proposals").fetchone()[0]
            operations = connection.execute(
                "SELECT status FROM operations WHERE id = ?",
                (save_key,),
            ).fetchone()[0]
            markdown = connection.execute(
                "SELECT content FROM sources WHERE name = 'notes.md'"
            ).fetchone()[0]
            kernel_rows = kernel.execute(
                "SELECT COUNT(*) FROM working_memory WHERE content = ?",
                (MEMORY,),
            ).fetchone()[0]
            candidate_rows = kernel.execute(
                "SELECT COUNT(*) FROM working_memory WHERE content = ?",
                (CANDIDATE,),
            ).fetchone()[0]
            first = json.loads(first_body)
            embedding_rows = kernel.execute(
                "SELECT COUNT(*) FROM memory_embeddings WHERE memory_id = ?",
                (first["kernel_id"],),
            ).fetchone()[0]
        finally:
            connection.close()
            kernel.close()
        connect_db = sqlite3.connect(connect_dir / "zhiwo.db")
        try:
            connect_refs = connect_db.execute("SELECT COUNT(*) FROM memory_refs").fetchone()[0]
            connect_sources = connect_db.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
        finally:
            connect_db.close()

        search = json.loads(search_body)
        saved = json.loads(saved_body)
        candidate = json.loads(candidate_body)
        second = json.loads(second_body)
        again = json.loads(again_body)
        unavailable = json.loads(unavailable_body)
        health = json.loads(health_body)
        result = {
            "task": "P1.2",
            "platform": platform.platform(),
            "python": platform.python_version(),
            "real_cloud_extraction": "NOT_RUN",
            "extractor_double": "local-http",
            "rejected_without_embeddings": rejected_status,
            "rejected_code": json.loads(rejected_body)["error"]["code"],
            "oversize_status": huge_status,
            "invalid_utf8_status": bad_status,
            "connect_only_refs": connect_refs,
            "connect_only_sources": connect_sources,
            "health_embeddings_loaded": health["embeddings_loaded"],
            "first_status": first_status,
            "second_status": second_status,
            "same_memory_id": first["memory_id"] == second["memory_id"] == again["memory_id"],
            "same_kernel_id": first["kernel_id"] == second["kernel_id"] == again["kernel_id"],
            "conflict_status": conflict_status,
            "conflict_code": json.loads(conflict_body)["error"]["code"],
            "unavailable_status": unavailable["status"],
            "unavailable_proposals": len(unavailable["proposals"]),
            "restart_pid_changed": first_pid != restart_pid,
            "search_after_restart": [item["id"] for item in search["items"]],
            "imports": [
                {"status": item["status"], "format": item["format"], "proposals": len(item["proposals"])}
                for _status, item in imports
            ],
            "failed_status": failed["status"],
            "failed_code": failed["error_code"],
            "retry_status": json.loads(retry_body)["status"],
            "candidate_search_count": len(candidate["items"]),
            "saved_search_count": len(saved["items"]),
            "active_refs": active,
            "sources": sources,
            "proposals": proposals,
            "operation_status": operations,
            "kernel_rows": kernel_rows,
            "candidate_rows": candidate_rows,
            "embedding_rows": embedding_rows,
            "markdown_kept_script": "<script>alert(1)</script>" in markdown,
            "experiment_unchanged": _fingerprint(SPIKE) == before,
            "credential_in_logs": token in log_text or extractor_key in log_text,
            "published_kind": saved["items"][0]["kind"] if saved["items"] else None,
            "published_category": saved["items"][0]["category"] if saved["items"] else None,
        }
        proposal_evidence = all(
            item["proposals"]
            and item["proposals"][0]["evidence"]["text"] == EVIDENCE
            and item["proposals"][0]["evidence"]["source_id"] == item["source_id"]
            and item["proposals"][0]["status"] == "pending"
            for _status, item in imports
        )
        result["proposal_evidence"] = proposal_evidence
        result["pass"] = all(
            [
                rejected_status == 503,
                result["rejected_code"] == "MODEL_UNAVAILABLE",
                huge_status == 400,
                bad_status == 400,
                connect_refs == 0,
                connect_sources == 0,
                health["embeddings_loaded"] is True,
                health["connect_only"] is False,
                first_status == 200,
                second_status == 200,
                again_status == 200,
                result["same_memory_id"],
                result["same_kernel_id"],
                conflict_status == 409,
                result["conflict_code"] == "CONFLICT",
                unavailable_status == 200,
                unavailable["status"] == "extractor_unavailable",
                unavailable["proposals"] == [],
                result["restart_pid_changed"],
                search_status == 200,
                search["items"] and search["items"][0]["content"] == MEMORY,
                search["items"][0]["kind"] == "fact",
                search["items"][0]["category"] == "preference",
                all(status == 200 for status, _item in imports),
                [item["format"] for _status, item in imports] == ["paste", "txt", "markdown"],
                all(len(item["proposals"]) == 1 for _status, item in imports),
                proposal_evidence,
                failed_status == 200,
                failed["status"] == "failed",
                failed["error_code"] == "VALIDATION_ERROR",
                failed["proposals"] == [],
                retry_status == 200,
                json.loads(retry_body)["status"] == "failed",
                candidate_status == 200,
                candidate["items"] == [],
                saved_status == 200,
                len(saved["items"]) == 1,
                active == 1,
                sources == 5,
                proposals == 3,
                operations == "completed",
                kernel_rows == 1,
                candidate_rows == 0,
                embedding_rows == 1,
                result["markdown_kept_script"],
                result["experiment_unchanged"],
                not result["credential_in_logs"],
            ]
        )
        RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
        RESULT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if not result["pass"]:
            raise AssertionError(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    test_publish_import_and_search()
    print("OK")
