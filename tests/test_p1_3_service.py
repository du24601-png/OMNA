"""P1.3: review once, keep history, and recover an interrupted publish."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
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
RESULT_PATH = REPO / "tests" / "results" / "p1_3_windows.json"
SPIKE = REPO / "experiments" / "kernel_spike"
CACHE = SPIKE / "runs" / "p0_3" / "fastembed-cache"
PYTHON = REPO / "server" / ".venv" / "Scripts" / "python.exe"
EVIDENCE = "证据片段甲"

INITIAL = "合成编号甲蓝封面笔记本只在周日打开"
UPDATE_B = "合成编号乙绿封面笔记本改到周一打开"
UPDATE_C = "合成编号丙红封面笔记本改到周五打开"
KEEP = "合成编号丁正式报告要写详细论据"
REJECT = "合成编号戊紫藤密码只留在被拒绝的候选"
ACCEPT = "合成编号己每周四晚上去社区游泳池"
CANDIDATE = "合成编号庚候选原文写喜欢红茶"
EDITED = "合成编号辛改成喜欢不加糖的绿茶"
CRASH_KERNEL = "合成编号壬内核已写控制库尚未提交"
CRASH_COMMIT = "合成编号癸控制库已提交响应尚未返回"
PAYLOAD = "合成编号子同决定的原始正文"
PAYLOAD_CHANGED = "合成编号丑同决定被改成另一句"


def _request(
    url: str,
    token: str | None = None,
    payload: dict | None = None,
    key: str | None = None,
    timeout: int = 120,
) -> tuple[int, str]:
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
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def _request_alive(url: str, token: str, payload: dict, key: str) -> tuple[int, str]:
    try:
        return _request(url, token, payload, key)
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        return 0, ""


def _wait_ok(url: str, proc: subprocess.Popen, log_path: Path) -> None:
    deadline = time.time() + 120
    last = ""
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        try:
            status, body = _request(url, timeout=5)
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
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
        "ZHIWO_CRASH_AFTER",
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


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=5)
    connection.row_factory = sqlite3.Row
    return connection


def _kernel_rows(kernel_db: Path, content: str) -> int:
    connection = _connect(kernel_db)
    try:
        return int(
            connection.execute("SELECT COUNT(*) FROM working_memory WHERE content = ?", (content,)).fetchone()[0]
        )
    finally:
        connection.close()


def _active_for_content(control_db: Path, kernel_db: Path, content: str) -> int:
    kernel = _connect(kernel_db)
    control = _connect(control_db)
    try:
        ids = [
            row[0]
            for row in kernel.execute("SELECT id FROM working_memory WHERE content = ?", (content,))
        ]
        if not ids:
            return 0
        marks = ",".join("?" for _ in ids)
        return int(
            control.execute(
                f"SELECT COUNT(*) FROM memory_refs WHERE lifecycle = 'active' AND kernel_id IN ({marks})",
                ids,
            ).fetchone()[0]
        )
    finally:
        kernel.close()
        control.close()


class _Extractor(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
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


def _import(base: str, token: str, sentence: str) -> dict:
    status, body = _request(
        f"{base}/api/v1/imports",
        token,
        {"kind": "paste", "text": f"{EVIDENCE}\n建议：{sentence}"},
        str(uuid.uuid4()),
    )
    parsed = json.loads(body)
    if status != 200 or parsed.get("status") != "extracted" or len(parsed.get("proposals") or []) != 1:
        raise AssertionError(f"import failed for a synthetic source: {status} {body[:500]}")
    proposal = parsed["proposals"][0]
    if proposal["payload"]["content"] != sentence:
        raise AssertionError("the local extractor double did not keep the synthetic candidate")
    return proposal


def _search(base: str, token: str, query: str) -> list[dict]:
    url = f"{base}/api/v1/memories?{urllib.parse.urlencode({'query': query})}"
    status, body = _request(url, token)
    parsed = json.loads(body)
    if status != 200:
        raise AssertionError(f"search failed: {status} {body[:500]}")
    return parsed["items"]


def _exact(items: list[dict], content: str) -> list[dict]:
    return [item for item in items if item["content"] == content]


def _fault_gate() -> dict:
    from zhiwo.services.publish import crash_injection_armed

    saved = {name: os.environ.get(name) for name in ("ZHIWO_CRASH_AFTER", "ZHIWO_TEST_MODE", "ZHIWO_DATA_DIR")}
    try:
        os.environ["ZHIWO_CRASH_AFTER"] = "kernel"
        os.environ["ZHIWO_TEST_MODE"] = "1"
        os.environ["ZHIWO_DATA_DIR"] = tempfile.mkdtemp(prefix="zhiwo-fault-")
        armed = crash_injection_armed("kernel")
        os.environ["ZHIWO_TEST_MODE"] = "0"
        without_mode = crash_injection_armed("kernel")
        os.environ["ZHIWO_TEST_MODE"] = "1"
        os.environ["ZHIWO_DATA_DIR"] = str(REPO)
        outside_temp = crash_injection_armed("kernel")
        os.environ.pop("ZHIWO_CRASH_AFTER", None)
        os.environ["ZHIWO_DATA_DIR"] = tempfile.mkdtemp(prefix="zhiwo-fault-")
        without_point = crash_injection_armed("kernel")
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    return {
        "armed_in_temp_test_mode": armed,
        "blocked_without_test_mode": without_mode is False,
        "blocked_outside_temp": outside_temp is False,
        "blocked_without_point": without_point is False,
    }


def test_review_conflict_and_recovery() -> None:
    if sys.platform != "win32" or platform.system() != "Windows" or os.environ.get("WSL_DISTRO_NAME"):
        raise AssertionError("P1.3 requires native Windows")
    sys.path.insert(0, str(REPO / "server"))
    if not CACHE.exists():
        raise AssertionError(f"local embedding cache is missing: {CACHE}")
    fault_gate = _fault_gate()
    before = _fingerprint(SPIKE)
    token = "p13-owner-" + uuid.uuid4().hex
    extractor_key = "p13-extractor-" + uuid.uuid4().hex
    result: dict = {
        "task": "P1.3",
        "platform": platform.platform(),
        "python": platform.python_version(),
        "real_cloud_extraction": "NOT_RUN",
        "extractor_double": "local-http",
    }
    stub = None
    proc = None
    try:
        with tempfile.TemporaryDirectory(prefix="zhiwo-p13-") as raw:
            root = Path(raw)
            cache_dir = root / "fastembed-cache"
            shutil.copytree(CACHE, cache_dir)
            data_dir = root / "data"
            data_dir.mkdir()
            logs = root / "logs"
            logs.mkdir()
            control_db = data_dir / "zhiwo.db"
            kernel_db = data_dir / "kernel" / "mnemosyne.db"
            stub_port = _port()
            stub = ThreadingHTTPServer(("127.0.0.1", stub_port), _Extractor)
            threading.Thread(target=stub.serve_forever, daemon=True).start()
            extra = {
                "ZHIWO_FASTEMBED_CACHE_DIR": str(cache_dir),
                "ZHIWO_EXTRACTOR_BASE_URL": f"http://127.0.0.1:{stub_port}",
                "ZHIWO_EXTRACTOR_MODEL": "test-extractor",
                "ZHIWO_EXTRACTOR_API_KEY": extractor_key,
            }
            port = _port()
            log_path = logs / "review.log"
            proc = _start(port, data_dir, token, log_path, extra)
            base = f"http://127.0.0.1:{port}"
            _wait_ok(f"{base}/health", proc, log_path)

            save_key = str(uuid.uuid4())
            save_status, save_body = _request(
                f"{base}/api/v1/memories",
                token,
                {"content": INITIAL, "kind": "fact", "category": "preference"},
                save_key,
            )
            saved = json.loads(save_body)
            memory_id = saved["memory_id"]
            versions_before = json.loads(_request(f"{base}/api/v1/memories/{memory_id}/versions", token)[1])

            accepted = _import(base, token, ACCEPT)
            rejected = _import(base, token, REJECT)
            edited = _import(base, token, CANDIDATE)
            update_b = _import(base, token, UPDATE_B)
            update_c = _import(base, token, UPDATE_C)
            kept = _import(base, token, KEEP)
            payload_proposal = _import(base, token, PAYLOAD)

            accept_key = str(uuid.uuid4())
            accept_body_json = {"decision": "accept"}
            accept_status, accept_body = _request(
                f"{base}/api/v1/proposals/{accepted['id']}/decision",
                token,
                accept_body_json,
                accept_key,
            )
            repeat_key = str(uuid.uuid4())
            repeat_status, repeat_body = _request(
                f"{base}/api/v1/proposals/{accepted['id']}/decision",
                token,
                accept_body_json,
                repeat_key,
            )
            reject_after_status, reject_after_body = _request(
                f"{base}/api/v1/proposals/{accepted['id']}/decision",
                token,
                {"decision": "reject"},
                str(uuid.uuid4()),
            )
            accept_hits = _exact(_search(base, token, ACCEPT), ACCEPT)

            reject_status, reject_body = _request(
                f"{base}/api/v1/proposals/{rejected['id']}/decision",
                token,
                {"decision": "reject"},
                str(uuid.uuid4()),
            )
            reject_again_status, reject_again_body = _request(
                f"{base}/api/v1/proposals/{rejected['id']}/decision",
                token,
                {"decision": "accept"},
                str(uuid.uuid4()),
            )
            versions_after_reject = json.loads(_request(f"{base}/api/v1/memories/{memory_id}/versions", token)[1])
            reject_hits = _exact(_search(base, token, REJECT), REJECT)

            payload_status, payload_body = _request(
                f"{base}/api/v1/proposals/{payload_proposal['id']}/decision",
                token,
                {"decision": "accept"},
                str(uuid.uuid4()),
            )
            payload_conflict_status, payload_conflict_body = _request(
                f"{base}/api/v1/proposals/{payload_proposal['id']}/decision",
                token,
                {"decision": "accept", "content": PAYLOAD_CHANGED},
                str(uuid.uuid4()),
            )

            edit_status, edit_body = _request(
                f"{base}/api/v1/proposals/{edited['id']}/decision",
                token,
                {"decision": "edit", "content": EDITED},
                str(uuid.uuid4()),
            )
            edit_hits = _exact(_search(base, token, EDITED), EDITED)
            candidate_hits = _exact(_search(base, token, CANDIDATE), CANDIDATE)

            race: dict[str, tuple[int, str]] = {}
            barrier = threading.Barrier(2)

            def _update(name: str, proposal_id: str, content: str) -> None:
                barrier.wait()
                race[name] = _request(
                    f"{base}/api/v1/proposals/{proposal_id}/decision",
                    token,
                    {
                        "decision": "update",
                        "target_id": memory_id,
                        "base_revision": 1,
                        "content": content,
                    },
                    str(uuid.uuid4()),
                )

            threads = [
                threading.Thread(target=_update, args=("b", update_b["id"], UPDATE_B)),
                threading.Thread(target=_update, args=("c", update_c["id"], UPDATE_C)),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            winner_name = "b" if race["b"][0] == 200 else "c"
            loser_name = "c" if winner_name == "b" else "b"
            winner = json.loads(race[winner_name][1]) if race[winner_name][0] == 200 else {}
            loser_body = json.loads(race[loser_name][1]) if race[loser_name][1] else {}
            winner_content = UPDATE_B if winner_name == "b" else UPDATE_C
            loser_content = UPDATE_C if winner_name == "b" else UPDATE_B
            loser_proposal = update_c["id"] if winner_name == "b" else update_b["id"]
            loser_view = json.loads(_request(f"{base}/api/v1/proposals/{loser_proposal}", token)[1])
            versions = json.loads(_request(f"{base}/api/v1/memories/{memory_id}/versions", token)[1])
            current_hits = _exact(_search(base, token, winner_content), winner_content)
            loser_hits = _exact(_search(base, token, loser_content), loser_content)
            old_hits = _exact(_search(base, token, INITIAL), INITIAL)

            missing_scope_status, missing_scope_body = _request(
                f"{base}/api/v1/proposals/{kept['id']}/decision",
                token,
                {"decision": "keep_both"},
                str(uuid.uuid4()),
            )
            missing_scope_view = json.loads(_request(f"{base}/api/v1/proposals/{kept['id']}", token)[1])
            keep_status, keep_body = _request(
                f"{base}/api/v1/proposals/{kept['id']}/decision",
                token,
                {"decision": "keep_both", "scope": "正式报告"},
                str(uuid.uuid4()),
            )
            kept_memory = json.loads(keep_body)
            original_versions = json.loads(_request(f"{base}/api/v1/memories/{memory_id}/versions", token)[1])
            kept_versions = json.loads(
                _request(f"{base}/api/v1/memories/{kept_memory['memory_id']}/versions", token)[1]
            )
            keep_hits = _exact(_search(base, token, KEEP), KEEP)
            original_still = _exact(_search(base, token, winner_content), winner_content)
            _stop(proc)
            proc = None
            time.sleep(0.5)

            kernel_crash = _crash_case(
                data_dir, token, logs, extra, "kernel", CRASH_KERNEL, control_db, kernel_db
            )
            commit_crash = _crash_case(
                data_dir, token, logs, extra, "commit", CRASH_COMMIT, control_db, kernel_db
            )

            log_text = "".join(path.read_text(encoding="utf-8", errors="replace") for path in logs.glob("*.log"))
            accept_result = json.loads(accept_body)
            repeat_result = json.loads(repeat_body)
            result.update(
                {
                    "save_status": save_status,
                    "fault_gate": fault_gate,
                    "accept_once": accept_status == 200
                    and repeat_status == 200
                    and accept_result["memory_id"] == repeat_result["memory_id"]
                    and accept_result["kernel_id"] == repeat_result["kernel_id"]
                    and accept_result["operation_id"] == accept_key
                    and repeat_result["operation_id"] == accept_key
                    and repeat_result["decision"] == "accept"
                    and repeat_result["status"] == "accepted"
                    and _kernel_rows(kernel_db, ACCEPT) == 1
                    and len(accept_hits) == 1,
                    "accept_then_reject": reject_after_status == 409
                    and json.loads(reject_after_body)["error"]["code"] == "CONFLICT"
                    and "已经通过" in json.loads(reject_after_body)["error"]["message"]
                    and _kernel_rows(kernel_db, ACCEPT) == 1
                    and len(accept_hits) == 1,
                    "reject_then_accept": reject_status == 200
                    and json.loads(reject_body)["status"] == "rejected"
                    and reject_again_status == 409
                    and json.loads(reject_again_body)["error"]["code"] == "CONFLICT"
                    and "已经拒绝" in json.loads(reject_again_body)["error"]["message"]
                    and reject_hits == []
                    and _kernel_rows(kernel_db, REJECT) == 0
                    and versions_after_reject == versions_before,
                    "same_decision_different_payload": payload_status == 200
                    and payload_conflict_status == 409
                    and json.loads(payload_conflict_body)["error"]["code"] == "CONFLICT"
                    and _kernel_rows(kernel_db, PAYLOAD) == 1
                    and _kernel_rows(kernel_db, PAYLOAD_CHANGED) == 0,
                    "edit_saved": edit_status == 200
                    and json.loads(edit_body)["revision"] == 1
                    and len(edit_hits) == 1
                    and edit_hits[0]["content"] == EDITED
                    and candidate_hits == []
                    and _kernel_rows(kernel_db, CANDIDATE) == 0
                    and _kernel_rows(kernel_db, EDITED) == 1,
                    "update_conflict": race["b"][0] in {200, 409}
                    and race["c"][0] in {200, 409}
                    and {race["b"][0], race["c"][0]} == {200, 409}
                    and loser_body.get("error", {}).get("code") == "CONFLICT"
                    and loser_view["status"] == "pending"
                    and winner.get("revision") == 2
                    and winner.get("memory_id") == memory_id
                    and [item["lifecycle"] for item in versions["versions"]] == ["superseded", "active"]
                    and versions["versions"][0]["content"] == INITIAL
                    and versions["versions"][1]["content"] == winner_content
                    and versions["versions"][1]["revision"] == 2
                    and len(current_hits) == 1
                    and current_hits[0]["revision"] == 2
                    and loser_hits == []
                    and old_hits == []
                    and _kernel_rows(kernel_db, INITIAL) == 1
                    and _kernel_rows(kernel_db, winner_content) == 1
                    and _kernel_rows(kernel_db, loser_content) == 0,
                    "keep_both": missing_scope_status == 400
                    and json.loads(missing_scope_body)["error"]["code"] == "VALIDATION_ERROR"
                    and missing_scope_view["status"] == "pending"
                    and keep_status == 200
                    and kept_memory["memory_id"] != memory_id
                    and kept_memory["scope"] == "正式报告"
                    and len(kept_versions["versions"]) == 1
                    and kept_versions["versions"][0]["lifecycle"] == "active"
                    and kept_versions["versions"][0]["content"] == KEEP
                    and original_versions["versions"][-1]["lifecycle"] == "active"
                    and original_versions["versions"][-1]["content"] == winner_content
                    and all(item["content"] != KEEP for item in original_versions["versions"])
                    and len(keep_hits) == 1
                    and keep_hits[0]["id"] == kept_memory["memory_id"]
                    and len(original_still) == 1
                    and original_still[0]["id"] == memory_id
                    and _kernel_rows(kernel_db, KEEP) == 1,
                    "kernel_crash": kernel_crash,
                    "commit_crash": commit_crash,
                    "experiment_unchanged": _fingerprint(SPIKE) == before,
                    "credential_in_logs": token in log_text or extractor_key in log_text,
                }
            )
    finally:
        if proc is not None:
            _stop(proc)
        if stub is not None:
            stub.shutdown()
        RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
        gate = result.get("fault_gate") or {}
        result["pass"] = bool(
            result.get("accept_once")
            and result.get("accept_then_reject")
            and result.get("reject_then_accept")
            and result.get("same_decision_different_payload")
            and result.get("edit_saved")
            and result.get("update_conflict")
            and result.get("keep_both")
            and _crash_ok(result.get("kernel_crash"))
            and _crash_ok(result.get("commit_crash"))
            and result.get("experiment_unchanged")
            and not result.get("credential_in_logs")
            and gate.get("armed_in_temp_test_mode")
            and gate.get("blocked_without_test_mode")
            and gate.get("blocked_outside_temp")
            and gate.get("blocked_without_point")
        )
        RESULT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not result["pass"]:
        raise AssertionError(json.dumps(result, ensure_ascii=False, indent=2))


def _crash_ok(item) -> bool:
    if not isinstance(item, dict) or item.get("recovered") is not True or item.get("published_once") is not True:
        return False
    if item.get("process_exited") is not True:
        return False
    if item.get("point") == "kernel":
        return item.get("hidden_before_recovery") is True
    return item.get("already_visible") is True


def _crash_case(
    data_dir: Path,
    token: str,
    logs: Path,
    extra: dict[str, str],
    point: str,
    sentence: str,
    control_db: Path,
    kernel_db: Path,
) -> dict:
    port = _port()
    log_path = logs / f"crash-{point}.log"
    proc = _start(
        port,
        data_dir,
        token,
        log_path,
        {**extra, "ZHIWO_CRASH_AFTER": point, "ZHIWO_TEST_MODE": "1"},
    )
    outcome = {
        "point": point,
        "process_exited": False,
        "hidden_before_recovery": False,
        "recovered": False,
        "published_once": False,
    }
    try:
        base = f"http://127.0.0.1:{port}"
        _wait_ok(f"{base}/health", proc, log_path)
        proposal = _import(base, token, sentence)
        operation_id = str(uuid.uuid4())
        status, _body = _request_alive(
            f"{base}/api/v1/proposals/{proposal['id']}/decision",
            token,
            {"decision": "accept"},
            operation_id,
        )
        if proc.poll() is None:
            _stop(proc)
        outcome["request_status"] = status
        outcome["process_exited"] = proc.poll() is not None and status == 0
    finally:
        _stop(proc)
    time.sleep(0.5)
    port = _port()
    log_path = logs / f"recover-{point}.log"
    proc = _start(port, data_dir, token, log_path, extra)
    try:
        base = f"http://127.0.0.1:{port}"
        _wait_ok(f"{base}/health", proc, log_path)
        before_hits = _exact(_search(base, token, sentence), sentence)
        proposal_view = json.loads(_request(f"{base}/api/v1/proposals/{proposal['id']}", token)[1])
        operation_view = json.loads(_request(f"{base}/api/v1/operations/{operation_id}", token)[1])
        written = _kernel_rows(kernel_db, sentence)
        visible = _active_for_content(control_db, kernel_db, sentence)
        if point == "kernel":
            outcome["hidden_before_recovery"] = (
                before_hits == []
                and proposal_view["status"] == "publishing"
                and operation_view["status"] == "prepared"
                and written == 1
                and visible == 0
            )
        else:
            outcome["already_visible"] = (
                len(before_hits) == 1
                and proposal_view["status"] == "accepted"
                and operation_view["status"] == "completed"
                and written == 1
                and visible == 1
            )
            outcome["visible_before_retry"] = before_hits[0]["id"] if before_hits else None
        retry_key = str(uuid.uuid4())
        retry_status, retry_body = _request(
            f"{base}/api/v1/proposals/{proposal['id']}/decision",
            token,
            {"decision": "accept"},
            retry_key,
        )
        retried = json.loads(retry_body)
        after_hits = _exact(_search(base, token, sentence), sentence)
        outcome["recovered"] = (
            retry_status == 200
            and retried.get("operation_id") == operation_id
            and retried.get("decision") == "accept"
            and retried.get("status") == "accepted"
            and len(after_hits) == 1
            and after_hits[0]["content"] == sentence
        )
        outcome["published_once"] = (
            _kernel_rows(kernel_db, sentence) == 1
            and _active_for_content(control_db, kernel_db, sentence) == 1
            and (not before_hits or before_hits[0]["id"] == retried.get("memory_id"))
            and after_hits[0]["id"] == retried.get("memory_id")
        )
        outcome["retry_key_ignored"] = retry_key != operation_id
    finally:
        _stop(proc)
    time.sleep(0.5)
    return outcome


if __name__ == "__main__":
    test_review_conflict_and_recovery()
    print("OK")
