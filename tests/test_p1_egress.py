"""Formal service with external HTTP blocked and loopback kept.

This is not an A12 full disconnect. The network interface stays up.
"""

from __future__ import annotations

import json
import os
import platform
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RESULT_PATH = REPO / "tests" / "results" / "p1_egress_windows.json"
PYTHON = REPO / "server" / ".venv" / "Scripts" / "python.exe"
CACHE = REPO / "experiments" / "kernel_spike" / "runs" / "p0_3" / "fastembed-cache"
MEMORY = "外网受限探针：回环仍可保存这条合成偏好。"
QUERY = "外网受限探针"


def _local_request(url: str, token: str | None = None, payload: dict | None = None, key: str | None = None) -> tuple[int, str]:
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
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=60) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def _restricted_env(data_dir: Path, token: str) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "ZHIWO_DATA_DIR": str(data_dir),
            "ZHIWO_OWNER_CREDENTIAL": token,
            "ZHIWO_HOST": "127.0.0.1",
            "ZHIWO_FASTEMBED_CACHE_DIR": str(CACHE),
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "ALL_PROXY": "http://127.0.0.1:9",
            "NO_PROXY": "127.0.0.1,localhost",
            "HF_HUB_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "PYTHONUTF8": "1",
            "PYTHONUNBUFFERED": "1",
            "PYTHONPATH": str(REPO / "server"),
        }
    )
    env.pop("ZHIWO_KERNEL_CONNECT_ONLY", None)
    env.pop("ZHIWO_EXTRACTOR_API_KEY", None)
    env.pop("ZHIWO_EXTRACTOR_BASE_URL", None)
    env.pop("ZHIWO_EXTRACTOR_MODEL", None)
    return env


def _external_http(env: dict[str, str]) -> dict:
    script = (
        "import urllib.request\n"
        "try:\n"
        "    urllib.request.urlopen('https://example.com', timeout=8)\n"
        "except Exception as exc:\n"
        "    print(type(exc).__name__)\n"
        "    raise SystemExit(0)\n"
        "raise SystemExit(2)\n"
    )
    completed = subprocess.run(
        [str(PYTHON), "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    return {
        "url": "https://example.com",
        "exit_code": completed.returncode,
        "blocked": completed.returncode == 0,
        "error": (completed.stdout or completed.stderr).strip().splitlines()[-1:] or [""],
    }


def _raw_tcp() -> dict:
    sock = socket.socket()
    sock.settimeout(5)
    try:
        sock.connect(("1.1.1.1", 443))
    except OSError as exc:
        return {"host": "1.1.1.1", "port": 443, "connected": False, "error": type(exc).__name__}
    else:
        return {"host": "1.1.1.1", "port": 443, "connected": True, "error": None}
    finally:
        sock.close()


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_egress_restricted_loopback_kept() -> None:
    if sys.platform != "win32" or platform.system() != "Windows" or os.environ.get("WSL_DISTRO_NAME"):
        raise AssertionError("egress check requires native Windows")
    if not PYTHON.exists():
        raise AssertionError(f"server interpreter is missing: {PYTHON}")
    cache_ready = CACHE.exists() and any(CACHE.rglob("*"))
    if not cache_ready:
        raise AssertionError(f"local embedding cache is missing: {CACHE}")
    owner = "p1-egress-" + uuid.uuid4().hex
    port = _port()
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="zhiwo-egress-") as raw:
        data_dir = Path(raw)
        env = _restricted_env(data_dir, owner)
        external = _external_http(env)
        raw_tcp = _raw_tcp()
        log_path = data_dir / "service.log"
        log = log_path.open("w", encoding="utf-8")
        proc = subprocess.Popen(
            [str(PYTHON), "-m", "uvicorn", "zhiwo.api.app:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=str(REPO / "server"),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        log.close()
        try:
            deadline = time.time() + 120
            ready = False
            while time.time() < deadline:
                if proc.poll() is not None:
                    break
                try:
                    status, _body = _local_request(f"{base}/health")
                except (urllib.error.URLError, TimeoutError, ConnectionError):
                    time.sleep(0.4)
                    continue
                if status == 200:
                    ready = True
                    break
                time.sleep(0.4)
            if not ready:
                detail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
                raise AssertionError(f"service did not become ready\n{detail}")
            save_status, save_body = _local_request(
                f"{base}/api/v1/memories",
                owner,
                {"content": MEMORY, "kind": "fact", "category": "preference"},
                str(uuid.uuid4()),
            )
            saved = json.loads(save_body)
            read_status, read_body = _local_request(f"{base}/api/v1/memories/{saved['memory_id']}", owner)
            read_item = json.loads(read_body)
            query = urllib.parse.quote(QUERY)
            search_status, search_body = _local_request(
                f"{base}/api/v1/memories?query={query}&state=current&limit=5",
                owner,
            )
            found = json.loads(search_body)
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=10)
        hits = [item.get("content") for item in found.get("items", [])]
        result = {
            "task": "P1 egress restriction",
            "platform": platform.platform(),
            "python": platform.python_version(),
            "service": "official FastAPI on 127.0.0.1",
            "new_process": True,
            "model_cache_present_before_start": True,
            "cache_dir": str(CACHE),
            "restriction": {
                "method": "HTTP_PROXY, HTTPS_PROXY and ALL_PROXY point at http://127.0.0.1:9. NO_PROXY keeps 127.0.0.1 and localhost. HF_HUB_OFFLINE=1.",
                "loopback": "kept",
                "external_http": external,
                "raw_tcp": raw_tcp,
                "nic_disconnected": False,
            },
            "a12_full_disconnect": "NOT_RUN",
            "save_status": save_status,
            "read_status": read_status,
            "read_matches": read_item.get("content") == MEMORY,
            "search_status": search_status,
            "search_hit": MEMORY in hits,
            "credential_in_result": False,
        }
        result["pass"] = all(
            [
                external["blocked"],
                ready,
                save_status == 200,
                read_status == 200 and result["read_matches"],
                search_status == 200 and result["search_hit"],
                result["a12_full_disconnect"] == "NOT_RUN",
            ]
        )
        RESULT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        if not result["pass"]:
            raise AssertionError(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    test_egress_restricted_loopback_kept()
