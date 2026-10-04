"""P3.2: the Windows installer, installed and run like a user would.

Installs silently into a temp directory, starts OMNA.exe with its own temp
userData, a System32-only PATH and hostile variables (a fake extractor key,
test mode, PYTHONHOME and a PYTHONPATH that shadows zhiwo), then checks the
bundled service, the bundled bridge (plain MCP and a real OpenCode call),
quit without leftovers, restart persistence and silent uninstall.
Everything is synthetic. A clean machine without dev tools is NOT_RUN here.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_p2_4_client import OPENCODE, OPENCODE_MODEL, _collect_tools, _parse_payload, _request, _tool_text  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
VERSION = json.loads((REPO / "apps/desktop/package.json").read_text(encoding="utf-8"))["version"]
INSTALLER = REPO / "apps" / "desktop" / "dist" / f"OMNA-Setup-{VERSION}.exe"
RESULT_PATH = REPO / "tests" / "results" / "p3_2_windows.json"
ORIGIN = "http://127.0.0.1:8765"
SYSTEM32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
POWERSHELL = SYSTEM32 / "WindowsPowerShell" / "v1.0" / "powershell.exe"
TARGET = "安装版探针：周末喜欢在湖边慢跑五公里。"
QUERY = "周末是不是喜欢在湖边慢跑"
KEEP = (
    "SystemRoot", "windir", "SystemDrive", "TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
    "USERNAME", "HOMEDRIVE", "HOMEPATH", "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS", "PATHEXT", "ComSpec",
)


def _minimal_env(extra: dict[str, str]) -> dict[str, str]:
    env = {key: os.environ[key] for key in KEEP if os.environ.get(key)}
    env["PATH"] = str(SYSTEM32)
    env.update(extra)
    return env


def _port_free() -> bool:
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", 8765))
        except OSError:
            return False
    return True


def _wait(predicate, seconds: float, step: float = 0.5) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return False


def _healthy() -> bool:
    try:
        status, body = _request("GET", f"{ORIGIN}/health")
    except OSError:
        return False
    return status == 200 and isinstance(body, dict) and body.get("status") == "ok"


def _processes(name: str) -> list[dict]:
    script = (
        f"Get-CimInstance Win32_Process -Filter \"Name='{name}'\" | "
        "Select-Object ProcessId,ParentProcessId,ExecutablePath,CommandLine | ConvertTo-Json -Compress"
    )
    completed = subprocess.run([str(POWERSHELL), "-NoProfile", "-Command", script], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    raw = completed.stdout.strip()
    if not raw:
        return []
    loaded = json.loads(raw)
    return loaded if isinstance(loaded, list) else [loaded]


def _bundled_python(app: Path) -> list[dict]:
    root = str(app).lower()
    return [row for row in _processes("python.exe") if (row.get("ExecutablePath") or "").lower().startswith(root)]


def _loaded_modules(pid: int, names: tuple[str, ...]) -> dict[str, list[str]]:
    wanted = ",".join(f"'{name}'" for name in names)
    script = (
        f"(Get-Process -Id {pid}).Modules | Where-Object {{ @({wanted}) -contains $_.ModuleName.ToLower() }} | "
        "Select-Object ModuleName,FileName | ConvertTo-Json -Compress"
    )
    completed = subprocess.run([str(POWERSHELL), "-NoProfile", "-Command", script], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    raw = completed.stdout.strip()
    rows = json.loads(raw) if raw else []
    rows = rows if isinstance(rows, list) else [rows]
    found: dict[str, list[str]] = {name: [] for name in names}
    for row in rows:
        found.setdefault(row["ModuleName"].lower(), []).append(row["FileName"])
    return found


def _shortcut_paths() -> dict[str, Path]:
    script = "[Environment]::GetFolderPath('Desktop'); [Environment]::GetFolderPath('Programs')"
    completed = subprocess.run([str(POWERSHELL), "-NoProfile", "-Command", script], capture_output=True, text=True, encoding="utf-8", check=False)
    desktop, programs = (completed.stdout.strip().splitlines() + ["", ""])[:2]
    return {"desktop": Path(desktop) / "OMNA.lnk", "start_menu": Path(programs) / "OMNA.lnk"}


def _uninstall_entry() -> bool:
    script = (
        "@(Get-ChildItem HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall | "
        "Where-Object { (Get-ItemProperty $_.PSPath).DisplayName -like 'OMNA*' }).Count"
    )
    completed = subprocess.run([str(POWERSHELL), "-NoProfile", "-Command", script], capture_output=True, text=True, check=False)
    return completed.stdout.strip() not in ("", "0")


def _size_mb(path: Path) -> float:
    return round(sum(item.stat().st_size for item in path.rglob("*") if item.is_file()) / 1024 / 1024, 1)


async def _mcp(command: list[str], env: dict[str, str], cwd: Path, calls: list[tuple[str, dict]]) -> dict:
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(command=command[0], args=command[1:], env=env, cwd=str(cwd))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            outputs = []
            for name, arguments in calls:
                called = await session.call_tool(name, arguments, read_timeout_seconds=180)
                outputs.append(_tool_text(called))
    return {"tools": sorted(tool.name for tool in listed.tools), "outputs": outputs}


def _opencode(run: Path, command: list[str], environment: dict[str, str]) -> dict:
    config = {
        "$schema": "https://opencode.ai/config.json",
        "model": OPENCODE_MODEL,
        "mcp": {"omna": {"type": "local", "command": command, "enabled": True, "environment": environment}},
    }
    (run / "opencode.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    prompt = f"请调用 omna 的 search_memory 工具，query 使用这句话：{QUERY}\nlimit 为 5。不要编造工具没有返回的个人记忆。"
    completed = subprocess.run(
        [str(OPENCODE), "run", prompt, "--dir", str(run), "--model", OPENCODE_MODEL, "--format", "json", "--auto", "--pure"],
        check=False, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=240, cwd=str(run),
    )
    tools = _collect_tools(completed.stdout)
    payload = next((item["output"] for item in tools if "search_memory" in item["name"] and item["output"]), "")
    return {"exit_code": completed.returncode, "payload": payload}


def _create_agent(owner: str, name: str, secrets: list[str]) -> dict:
    status, body = _request("POST", f"{ORIGIN}/api/v1/agents", owner, {"name": name}, idem=True)
    if status != 200 or not isinstance(body, dict) or not body.get("credential"):
        raise RuntimeError(f"create agent failed: {status}")
    secrets.append(body["credential"])
    patched, _ = _request(
        "PATCH", f"{ORIGIN}/api/v1/agents/{body['id']}", owner,
        {"allowed_tools": ["search_memory", "get_context"], "allowed_categories": ["preference"]}, idem=True,
    )
    if patched != 200:
        raise RuntimeError(f"grant agent failed: {patched}")
    return body


def _agent_status(owner: str, agent_id: str) -> str | None:
    _status, body = _request("GET", f"{ORIGIN}/api/v1/agents", owner)
    items = body.get("agents", []) if isinstance(body, dict) else []
    return next((item.get("client_status") for item in items if item.get("id") == agent_id), None)


def _search_has_target(owner: str) -> bool:
    _status, body = _request("GET", f"{ORIGIN}/api/v1/memories?query={urllib.parse.quote(QUERY)}", owner)
    return TARGET in json.dumps(body, ensure_ascii=False)


def test_installed_desktop() -> None:
    if sys.platform != "win32" or platform.system() != "Windows":
        raise AssertionError("P3.2 requires native Windows")
    if not INSTALLER.is_file():
        raise AssertionError(f"installer missing: {INSTALLER}")
    if not _port_free():
        raise AssertionError("127.0.0.1:8765 is in use; stop the dev service first")
    signature = subprocess.run(
        [str(POWERSHELL), "-NoProfile", "-Command", f"(Get-AuthenticodeSignature '{INSTALLER}').Status"],
        capture_output=True, text=True, check=False,
    ).stdout.strip()
    result: dict = {
        "task": "P3.2",
        "platform": platform.platform(),
        "installer": INSTALLER.name,
        "installer_mb": round(INSTALLER.stat().st_size / 1024 / 1024, 1),
        "installer_sha256": hashlib.sha256(INSTALLER.read_bytes()).hexdigest(),
        "signature": signature,
        "clean_machine": "NOT_RUN",
        "offline_after_install": "NOT_RUN",
        "stage_complete": False,
    }
    secrets: list[str] = []
    app = electron = None
    uninstalled = False
    # resolve() expands the 8.3 short temp path so paths compare with what the service reports.
    raw = Path(tempfile.mkdtemp(prefix="omna-p32-")).resolve()
    links = _shortcut_paths()
    shortcuts = lambda: {name: path.exists() for name, path in links.items()}  # noqa: E731
    result["preexisting_install"] = _uninstall_entry() or any(shortcuts().values())
    try:
        if result["preexisting_install"]:
            raise RuntimeError("OMNA is already installed for this user; uninstall it before this test")
        app = raw / "app"
        user_data = raw / "user-data"
        shadow = raw / "shadow"
        for package in ("zhiwo", "uvicorn", "mcp"):
            (shadow / package).mkdir(parents=True)
            (shadow / package / "__init__.py").write_text("raise ImportError('shadowed by PYTHONPATH')\n", encoding="utf-8")
        hostile = {
            "PYTHONPATH": str(shadow),
            "PYTHONHOME": str(raw / "no-python-home"),
            "ZHIWO_EXTRACTOR_API_KEY": "p32-leak-" + uuid.uuid4().hex,
            "ZHIWO_EXTRACTOR_BASE_URL": "http://127.0.0.1:9/v1",
            "ZHIWO_EXTRACTOR_MODEL": "p32-leak-model",
            "ZHIWO_TEST_MODE": "1",
            "MNEMOSYNE_DATA_DIR": str(raw / "mnemosyne-leak"),
        }
        secrets.append(hostile["ZHIWO_EXTRACTOR_API_KEY"])
        env = _minimal_env(hostile)
        exe = app / "OMNA.exe"

        started = time.time()
        installed = subprocess.run([str(INSTALLER), "/S", f"/D={app}"], check=False, timeout=600)
        result["install_exit"] = installed.returncode
        result["install_seconds"] = round(time.time() - started, 1)
        result["installed_files"] = exe.is_file() and (app / "resources" / "python" / "python.exe").is_file()
        result["installed_mb"] = _size_mb(app) if app.exists() else None
        result["shortcuts_after_install"] = shortcuts()
        result["uninstall_entry_after_install"] = _uninstall_entry()
        if not result["installed_files"]:
            raise RuntimeError("installer did not place OMNA.exe and the bundled python")

        def launch() -> float:
            nonlocal electron
            began = time.time()
            electron = subprocess.Popen([str(exe), f"--omna-user-data={user_data}"], env=env)
            if not _wait(_healthy, 180):
                raise RuntimeError("installed service did not become ready")
            return round(time.time() - began, 1)

        def quit_app() -> dict:
            began = time.time()
            subprocess.run([str(exe), f"--omna-user-data={user_data}", "--quit"], env=env, check=False, timeout=60)
            exited = True
            try:
                electron.wait(timeout=60)
            except subprocess.TimeoutExpired:
                exited = False
            return {
                "electron_exited": exited,
                "python_left": len(_bundled_python(app)),
                "port_free": _wait(_port_free, 15),
                "seconds": round(time.time() - began, 1),
            }

        result["first_ready_seconds"] = launch()
        credential_file = user_data / "owner.credential"
        owner = credential_file.read_text(encoding="utf-8").strip()
        secrets.append(owner)
        health_status, health = _request("GET", f"{ORIGIN}/api/v1/health", owner)
        unauth_status, _ = _request("GET", f"{ORIGIN}/api/v1/health")
        runtime = ((health.get("mcp_runtime") or {}).get("command") or []) if isinstance(health, dict) else []
        services = _bundled_python(app)
        page_status, page = _request("GET", f"{ORIGIN}/")
        runtime_dlls = ("msvcp140.dll", "msvcp140_1.dll", "vcruntime140.dll", "vcruntime140_1.dll")
        loaded = _loaded_modules(services[0]["ProcessId"], runtime_dlls) if services else {}
        python_dir = str(app / "resources" / "python").lower()
        result.update(
            {
                "owner_health": health_status,
                "unauthenticated_health": unauth_status,
                "credential_file_created": len(owner) >= 32,
                "extractor_key_not_inherited": isinstance(health, dict) and health.get("extractor_configured") is False,
                "test_mode_not_inherited": isinstance(health, dict) and health.get("test_mode") is False,
                "kernel_under_user_data": isinstance(health, dict) and str(user_data).lower() in str((health.get("kernel") or {}).get("db_path", "")).lower(),
                "mnemosyne_env_not_used": not (raw / "mnemosyne-leak").exists(),
                "embeddings_loaded": isinstance(health, dict) and health.get("embeddings_loaded"),
                "service_python": [row.get("ExecutablePath") for row in services],
                "service_parent_is_omna": bool(services) and all(row.get("ParentProcessId") == electron.pid for row in services),
                "service_isolated": bool(services) and all(" -I " in f" {row.get('CommandLine') or ''} " for row in services),
                "bridge_command": runtime,
                "bridge_uses_installed_python": bool(runtime) and runtime[0].lower() == str(app / "resources" / "python" / "python.exe").lower() and runtime[1:4] == ["-I", "-X", "utf8"],
                "page_served": page_status == 200 and isinstance(page, str) and "/assets/" in page,
                "vc_runtime_modules": loaded,
                "vc_runtime_from_install_dir": bool(loaded) and all(
                    paths and all(path.lower().startswith(python_dir) for path in paths) for paths in loaded.values()
                ),
            }
        )

        saved, saved_body = _request("POST", f"{ORIGIN}/api/v1/memories", owner, {"content": TARGET, "kind": "fact", "category": "preference", "share_enabled": True}, idem=True)
        result["save_status"] = saved
        result["owner_search_finds"] = _search_has_target(owner)

        bridge_agent = _create_agent(owner, "合成安装版", secrets)
        bridge_env = {
            "ZHIWO_API_ORIGIN": ORIGIN,
            "ZHIWO_AGENT_CREDENTIAL": bridge_agent["credential"],
            "PYTHONPATH": str(shadow),
            "PYTHONHOME": str(raw / "no-python-home"),
        }
        stdio = asyncio.run(_mcp(runtime, _minimal_env(bridge_env), raw, [("search_memory", {"query": QUERY, "limit": 5})]))
        result["bridge_tools"] = stdio["tools"]
        result["bridge_search_finds"] = TARGET in stdio["outputs"][0]
        result["bridge_agent_status"] = _agent_status(owner, bridge_agent["id"])

        if OPENCODE.is_file():
            client_agent = _create_agent(owner, "合成 OpenCode 安装版", secrets)
            run = raw / "opencode"
            run.mkdir()
            called = _opencode(run, runtime, {"ZHIWO_API_ORIGIN": ORIGIN, "ZHIWO_AGENT_CREDENTIAL": client_agent["credential"], "PYTHONPATH": str(shadow)})
            result["opencode_exit"] = called["exit_code"]
            result["opencode_search_finds"] = TARGET in called["payload"]
            result["opencode_agent_status"] = _agent_status(owner, client_agent["id"])
        else:
            result["opencode"] = "NOT_RUN"

        result["first_quit"] = quit_app()
        result["second_ready_seconds"] = launch()
        result["restart_keeps_memory"] = _search_has_target(owner)
        result["restart_same_credential"] = credential_file.read_text(encoding="utf-8").strip() == owner
        result["second_quit"] = quit_app()
        electron = None

        uninstaller = app / "Uninstall OMNA.exe"
        subprocess.run([str(uninstaller), "/S"], check=False, timeout=300)
        began = time.time()
        uninstalled = _wait(lambda: not exe.exists() and not any(shortcuts().values()) and not _uninstall_entry(), 180, step=2)
        result["uninstall_seconds"] = round(time.time() - began, 1)
        result["uninstall_removed_app"] = not exe.exists()
        result["shortcuts_after_uninstall"] = shortcuts()
        result["uninstall_entry_after_uninstall"] = _uninstall_entry()
        result["user_data_kept_after_uninstall"] = (user_data / "data" / "zhiwo.db").is_file()

        blob = json.dumps(result, ensure_ascii=False)
        result["credential_in_result"] = any(secret in blob for secret in secrets)
        quits = [result["first_quit"], result["second_quit"]]
        result["pass"] = all(
            [
                result["install_exit"] == 0 and result["installed_files"],
                result["shortcuts_after_install"] == {"desktop": True, "start_menu": True} and result["uninstall_entry_after_install"],
                result["owner_health"] == 200 and result["unauthenticated_health"] == 401,
                result["credential_file_created"],
                result["extractor_key_not_inherited"] and result["test_mode_not_inherited"],
                result["kernel_under_user_data"] and result["mnemosyne_env_not_used"],
                result["service_parent_is_omna"] and result["service_isolated"],
                result["bridge_uses_installed_python"] and result["page_served"],
                result["embeddings_loaded"] is True and result["vc_runtime_from_install_dir"],
                result["save_status"] == 200 and result["owner_search_finds"],
                result["bridge_tools"] == ["explain_memory", "get_context", "propose_memory", "search_memory"],
                result["bridge_search_finds"] and result["bridge_agent_status"] == "verified",
                result.get("opencode") == "NOT_RUN" or (result.get("opencode_search_finds") and result.get("opencode_agent_status") == "verified"),
                all(q["electron_exited"] and q["python_left"] == 0 and q["port_free"] for q in quits),
                result["restart_keeps_memory"] and result["restart_same_credential"],
                result["uninstall_removed_app"] and result["shortcuts_after_uninstall"] == {"desktop": False, "start_menu": False},
                result["uninstall_entry_after_uninstall"] is False,
                result["user_data_kept_after_uninstall"],
                result["credential_in_result"] is False,
            ]
        )
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["pass"] = False
    finally:
        if electron is not None and electron.poll() is None:
            subprocess.run(["taskkill", "/PID", str(electron.pid), "/T", "/F"], check=False, capture_output=True)
        if app is not None and not uninstalled and (app / "Uninstall OMNA.exe").exists():
            subprocess.run([str(app / "Uninstall OMNA.exe"), "/S"], check=False, timeout=300)
            _wait(lambda: not (app / "OMNA.exe").exists() and not _uninstall_entry(), 180, step=2)
        subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", str(raw)], check=False, capture_output=True)
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if not result["pass"]:
        raise AssertionError(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    test_installed_desktop()
