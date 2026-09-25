"""P2.1: connection identity and permissions. No memory filtering and no MCP."""

from __future__ import annotations

import json
import os
import platform
import socket
import sqlite3
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
RESULT_PATH = REPO / "tests" / "results" / "p2_1_windows.json"
PYTHON = REPO / "server" / ".venv" / "Scripts" / "python.exe"
OWNER_ROUTES = (
    ("GET", "/api/v1/memories", None),
    ("GET", "/api/v1/proposals", None),
    ("GET", "/api/v1/agents", None),
    ("GET", "/api/v1/sources/" + str(uuid.uuid4()), None),
    ("POST", "/api/v1/memories", {"content": "不应写入", "kind": "fact", "category": "preference"}),
)


def _request(url: str, token: str | None = None, payload: dict | None = None, key: str | None = None, headers: dict | None = None, method: str | None = None) -> tuple[int, str]:
    request_headers = dict(headers or {})
    data = None
    if token is not None:
        request_headers["Authorization"] = f"Bearer {token}"
    if key is not None:
        request_headers["Idempotency-Key"] = key
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    verb = method or ("POST" if payload is not None or key is not None else "GET")
    request = urllib.request.Request(url, data=data, headers=request_headers, method=verb)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def _wait_ok(url: str, proc: subprocess.Popen, log_path: Path) -> None:
    deadline = time.time() + 90
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


def _code(body: str) -> str:
    return json.loads(body)["error"]["code"]


def test_agent_identity_and_permissions() -> None:
    if sys.platform != "win32" or platform.system() != "Windows" or os.environ.get("WSL_DISTRO_NAME"):
        raise AssertionError("P2.1 requires native Windows")
    if not PYTHON.exists():
        raise AssertionError(f"server interpreter is missing: {PYTHON}")
    owner = "p21-owner-" + uuid.uuid4().hex
    port = _port()
    base = f"http://127.0.0.1:{port}"
    secrets: list[str] = []
    with tempfile.TemporaryDirectory(prefix="zhiwo-p21-", ignore_cleanup_errors=True) as raw:
        data_dir = Path(raw)
        log_path = data_dir / "first.log"
        proc = _start(port, data_dir, owner, log_path)
        try:
            _wait_ok(f"{base}/health", proc, log_path)
            first_pid = proc.pid
            create_key = str(uuid.uuid4())
            status, body = _request(
                f"{base}/api/v1/agents",
                owner,
                {"name": "合成连接甲"},
                create_key,
            )
            assert status == 200, body
            created = json.loads(body)
            secret_a = created["credential"]
            secrets.append(secret_a)
            agent_a = created["id"]
            replay_status, replay_body = _request(
                f"{base}/api/v1/agents",
                owner,
                {"name": "合成连接甲"},
                create_key,
            )
            conflict_status, conflict_body = _request(
                f"{base}/api/v1/agents",
                owner,
                {"name": "另一个名字"},
                create_key,
            )
            status_b, body_b = _request(
                f"{base}/api/v1/agents",
                owner,
                {"name": "合成连接乙"},
                str(uuid.uuid4()),
            )
            assert status_b == 200, body_b
            created_b = json.loads(body_b)
            secret_b = created_b["credential"]
            secrets.append(secret_b)
            agent_b = created_b["id"]
            list_status, list_body = _request(f"{base}/api/v1/agents", owner)
            listed = json.loads(list_body)
            forged_query = urllib.parse.urlencode({"agent_id": agent_b, "name": "合成连接乙"})
            forged_status, forged_body = _request(
                f"{base}/api/v1/agent/session?{forged_query}",
                secret_a,
                headers={"X-Agent-Name": "forged-name"},
            )
            forged = json.loads(forged_body)
            other_status, other_body = _request(f"{base}/api/v1/agent/session", secret_b)
            owner_on_session, _owner_session_body = _request(f"{base}/api/v1/agent/session", owner)
            owner_api = {}
            denied_routes = OWNER_ROUTES + (
                ("PATCH", f"/api/v1/agents/{agent_a}", {"enabled": False}),
                ("POST", f"/api/v1/agents/{agent_a}/rotate-credential", None),
            )
            for method, path, payload in denied_routes:
                route_status, route_body = _request(
                    base + path,
                    secret_a,
                    payload,
                    str(uuid.uuid4()) if method in {"POST", "PATCH"} else None,
                    method=method,
                )
                owner_api[f"{method} {path}"] = {
                    "status": route_status,
                    "code": _code(route_body),
                    "has_items": "items" in route_body,
                }
            untouched_status, untouched_body = _request(f"{base}/api/v1/agent/session", secret_a)
            untouched = json.loads(untouched_body)
            category_status, category_body = _request(
                f"{base}/api/v1/agents/{agent_a}",
                owner,
                {"allowed_categories": ["secret"]},
                str(uuid.uuid4()),
                method="PATCH",
            )
            bad_status, bad_body = _request(
                f"{base}/api/v1/agents/{agent_a}",
                owner,
                {"allowed_tools": ["delete_memory"], "allowed_categories": ["preference"]},
                str(uuid.uuid4()),
                method="PATCH",
            )
            grant_key = str(uuid.uuid4())
            grant_status, grant_body = _request(
                f"{base}/api/v1/agents/{agent_a}",
                owner,
                {"allowed_tools": ["search_memory"], "allowed_categories": ["preference"]},
                grant_key,
                method="PATCH",
            )
            grant_replay_status, grant_replay_body = _request(
                f"{base}/api/v1/agents/{agent_a}",
                owner,
                {"allowed_tools": ["search_memory"], "allowed_categories": ["preference"]},
                grant_key,
                method="PATCH",
            )
            name_status, name_body = _request(
                f"{base}/api/v1/agents/{agent_a}",
                owner,
                {"name": "合成连接甲改名"},
                str(uuid.uuid4()),
                method="PATCH",
            )
            disable_status, disable_body = _request(
                f"{base}/api/v1/agents/{agent_a}",
                owner,
                {"enabled": False},
                str(uuid.uuid4()),
                method="PATCH",
            )
            disabled_session, disabled_session_body = _request(f"{base}/api/v1/agent/session", secret_a)
            rotate_key = str(uuid.uuid4())
            rotate_status, rotate_body = _request(
                f"{base}/api/v1/agents/{agent_a}/rotate-credential",
                owner,
                key=rotate_key,
                method="POST",
            )
            rotated = json.loads(rotate_body)
            secret_new = rotated["credential"]
            secrets.append(secret_new)
            rotate_replay_status, rotate_replay_body = _request(
                f"{base}/api/v1/agents/{agent_a}/rotate-credential",
                owner,
                key=rotate_key,
                method="POST",
            )
            old_after_rotate, old_after_body = _request(f"{base}/api/v1/agent/session", secret_a)
            new_while_disabled, new_disabled_body = _request(f"{base}/api/v1/agent/session", secret_new)
            enable_status, enable_body = _request(
                f"{base}/api/v1/agents/{agent_a}",
                owner,
                {"enabled": True},
                str(uuid.uuid4()),
                method="PATCH",
            )
            new_enabled, _new_enabled_body = _request(f"{base}/api/v1/agent/session", secret_new)
            old_after_enable, _old_enable_body = _request(f"{base}/api/v1/agent/session", secret_a)
        finally:
            _stop(proc)
        restart_log = data_dir / "second.log"
        restarted = _start(port, data_dir, owner, restart_log)
        try:
            _wait_ok(f"{base}/health", restarted, restart_log)
            second_pid = restarted.pid
            restart_list_status, restart_list_body = _request(f"{base}/api/v1/agents", owner)
            restart_session, _restart_session_body = _request(f"{base}/api/v1/agent/session", secret_new)
            restart_old, _restart_old_body = _request(f"{base}/api/v1/agent/session", secret_a)
        finally:
            _stop(restarted)
        db_bytes = (data_dir / "zhiwo.db").read_bytes()
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
        log_text += restart_log.read_text(encoding="utf-8", errors="replace")
        connection = sqlite3.connect(data_dir / "zhiwo.db")
        try:
            hashes = [row[0] for row in connection.execute("SELECT credential_hash FROM agents ORDER BY created_at").fetchall()]
            ref_count = connection.execute("SELECT COUNT(*) FROM memory_refs").fetchone()[0]
            agent_count = connection.execute("SELECT COUNT(*) FROM agents").fetchone()[0]
        finally:
            connection.close()
        replay = json.loads(replay_body)
        grant = json.loads(grant_body)
        grant_replay = json.loads(grant_replay_body)
        renamed = json.loads(name_body)
        disabled = json.loads(disable_body)
        enabled = json.loads(enable_body)
        rotate_replay = json.loads(rotate_replay_body)
        restart_list = json.loads(restart_list_body)
        restarted_a = next(item for item in restart_list["agents"] if item["id"] == agent_a)
        result = {
            "task": "P2.1",
            "platform": platform.platform(),
            "python": platform.python_version(),
            "service": "official FastAPI on 127.0.0.1",
            "connect_only": True,
            "memory_filter": "NOT_RUN",
            "mcp": "NOT_RUN",
            "access_log": "NOT_RUN",
            "pid_changed": first_pid != second_pid,
            "create_status": status,
            "default_tools": created["allowed_tools"],
            "default_categories": created["allowed_categories"],
            "default_enabled": created["enabled"],
            "default_policy_version": created["policy_version"],
            "replay_status": replay_status,
            "replay_same_id": replay["id"] == agent_a,
            "replay_has_credential": "credential" in replay,
            "create_conflict_status": conflict_status,
            "create_conflict_code": _code(conflict_body),
            "independent_ids": agent_a != agent_b,
            "list_status": list_status,
            "list_count": len(listed["agents"]),
            "list_has_credential": any("credential" in item or "credential_hash" in item for item in listed["agents"]),
            "forged_status": forged_status,
            "forged_agent_id": forged["agent_id"],
            "forged_name": forged["name"],
            "forged_identity_source": forged["identity_source"],
            "other_agent_status": other_status,
            "other_agent_id": json.loads(other_body)["agent_id"],
            "owner_token_on_session": owner_on_session,
            "agent_on_owner_api": owner_api,
            "unknown_tool_status": bad_status,
            "unknown_tool_code": _code(bad_body),
            "unknown_category_status": category_status,
            "unknown_category_code": _code(category_body),
            "after_denied_owner_calls_status": untouched_status,
            "after_denied_owner_calls_policy_version": untouched["policy_version"],
            "after_denied_owner_calls_enabled": untouched["enabled"],
            "grant_policy_version": grant["policy_version"],
            "grant_tools": grant["allowed_tools"],
            "grant_categories": grant["allowed_categories"],
            "grant_replay_status": grant_replay_status,
            "grant_replay_policy_version": grant_replay["policy_version"],
            "rename_policy_version": renamed["policy_version"],
            "disable_status": disable_status,
            "disable_policy_version": disabled["policy_version"],
            "disable_enabled": disabled["enabled"],
            "disabled_session_status": disabled_session,
            "disabled_session_code": _code(disabled_session_body),
            "rotate_status": rotate_status,
            "rotate_enabled": rotated["enabled"],
            "rotate_policy_version": rotated["policy_version"],
            "rotate_replay_status": rotate_replay_status,
            "rotate_replay_has_credential": "credential" in rotate_replay,
            "rotate_replay_policy_version": rotate_replay["policy_version"],
            "old_credential_after_rotate": old_after_rotate,
            "old_credential_code": _code(old_after_body),
            "new_credential_while_disabled": new_while_disabled,
            "new_credential_disabled_code": _code(new_disabled_body),
            "enable_policy_version": enabled["policy_version"],
            "enable_enabled": enabled["enabled"],
            "new_credential_after_enable": new_enabled,
            "old_credential_after_enable": old_after_enable,
            "restart_list_status": restart_list_status,
            "restart_enabled": restarted_a["enabled"],
            "restart_policy_version": restarted_a["policy_version"],
            "restart_tools": restarted_a["allowed_tools"],
            "restart_categories": restarted_a["allowed_categories"],
            "restart_name": restarted_a["name"],
            "restart_session_status": restart_session,
            "restart_old_credential_status": restart_old,
            "agent_count": agent_count,
            "distinct_hashes": len(set(hashes)) == len(hashes) == 2,
            "memory_ref_count": ref_count,
            "credential_in_database": any(secret.encode("utf-8") in db_bytes for secret in secrets),
            "credential_in_logs": any(secret in log_text for secret in secrets),
            "owner_credential_in_logs": owner in log_text,
        }
        result["pass"] = all(
            [
                result["pid_changed"],
                created["allowed_tools"] == [] and created["allowed_categories"] == [],
                created["enabled"] is True and created["policy_version"] == 1,
                replay_status == 200 and replay["id"] == agent_a and "credential" not in replay,
                conflict_status == 409 and result["create_conflict_code"] == "CONFLICT",
                agent_a != agent_b and secret_a != secret_b,
                list_status == 200 and result["list_count"] == 2 and not result["list_has_credential"],
                forged_status == 200 and forged["agent_id"] == agent_a and forged["name"] == "合成连接甲",
                forged["identity_source"] == "credential",
                other_status == 200 and json.loads(other_body)["agent_id"] == agent_b,
                owner_on_session == 401,
                all(item["status"] == 401 and item["code"] == "UNAUTHENTICATED" and not item["has_items"] for item in owner_api.values()),
                bad_status == 400 and result["unknown_tool_code"] == "VALIDATION_ERROR",
                category_status == 400 and result["unknown_category_code"] == "VALIDATION_ERROR",
                untouched_status == 200 and untouched["enabled"] is True and untouched["policy_version"] == 1,
                grant_status == 200 and grant["policy_version"] == 2,
                grant["allowed_tools"] == ["search_memory"] and grant["allowed_categories"] == ["preference"],
                grant_replay_status == 200 and grant_replay["policy_version"] == 2,
                name_status == 200 and renamed["policy_version"] == 2 and renamed["name"] == "合成连接甲改名",
                disable_status == 200 and disabled["enabled"] is False and disabled["policy_version"] == 3,
                disabled_session == 403 and result["disabled_session_code"] == "FORBIDDEN",
                rotate_status == 200 and rotated["enabled"] is False and rotated["policy_version"] == 4,
                rotate_replay_status == 200 and "credential" not in rotate_replay and rotate_replay["policy_version"] == 4,
                old_after_rotate == 401 and result["old_credential_code"] == "UNAUTHENTICATED",
                new_while_disabled == 403 and result["new_credential_disabled_code"] == "FORBIDDEN",
                enable_status == 200 and enabled["enabled"] is True and enabled["policy_version"] == 5,
                new_enabled == 200 and old_after_enable == 401,
                restart_list_status == 200,
                restarted_a["enabled"] is True and restarted_a["policy_version"] == 5,
                restarted_a["allowed_tools"] == ["search_memory"] and restarted_a["allowed_categories"] == ["preference"],
                restarted_a["name"] == "合成连接甲改名",
                restart_session == 200 and restart_old == 401,
                agent_count == 2 and result["distinct_hashes"] and ref_count == 0,
                not result["credential_in_database"],
                not result["credential_in_logs"] and not result["owner_credential_in_logs"],
            ]
        )
        text = json.dumps(result, ensure_ascii=False, indent=2)
        leaked = any(secret in text for secret in secrets) or owner in text
        result["credential_in_result"] = leaked
        result["pass"] = result["pass"] and not leaked
        RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
        RESULT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        if not result["pass"]:
            raise AssertionError(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    test_agent_identity_and_permissions()
