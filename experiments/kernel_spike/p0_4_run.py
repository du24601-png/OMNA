"""P0.4: one real stdio client must call search_memory.

A local MCP client checks the bridge first. Acceptance is the installed
client's handshake, tool list, and tool result, not that local check.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

SPIKE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = SPIKE_ROOT.parents[1]
RUN = SPIKE_ROOT / "runs" / "p0_4"
RESULT_PATH = SPIKE_ROOT / "results" / "p0_4_windows.json"
SOURCE_DB = SPIKE_ROOT / "runs" / "p0_3" / "zh" / "before-recall" / "mnemosyne.db"
SOURCE_CONTROL = SPIKE_ROOT / "runs" / "p0_3" / "zh" / "control.json"
CACHE = SPIKE_ROOT / "runs" / "p0_3" / "fastembed-cache"
FIXTURE = SPIKE_ROOT / "fixtures" / "p0_3_queries.json"
PYTHON = SPIKE_ROOT / ".venv" / "Scripts" / "python.exe"
SERVER = SPIKE_ROOT / "p0_4_stdio.py"
WRONG = "p04-wrong-credential"
TARGET_ID = "m06"
QUERY_ID = "d06"
OPENCODE_MODEL = "opencode-go/deepseek-v4-flash"
OPENCODE = Path(r"C:\Users\example\npm-global\node_modules\opencode-ai\bin\opencode.exe")


def emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def load_question() -> tuple[str, str]:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    query = next(row for row in fixture["queries"] if row["id"] == QUERY_ID)
    memory = next(row for row in fixture["memories"] if row["id"] == query["target"])
    if memory["id"] != TARGET_ID:
        raise SystemExit("frozen d06 no longer targets m06")
    return query["text"], memory["content"]


def other_bodies(target: str) -> list[str]:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return [row["content"] for row in fixture["memories"] if row["content"] != target]


def child_env(credential: str, data_dir: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for key in (
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "HOMEDRIVE",
        "HOMEPATH",
        "USERNAME",
        "PROCESSOR_ARCHITECTURE",
    ):
        value = os.environ.get(key)
        if value:
            env[key] = value
    env.update(
        {
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "ZHIWO_DATA_DIR": str(data_dir),
            "ZHIWO_CONTROL_PATH": str(data_dir / "control.json"),
            "ZHIWO_AGENT_FILE": str(RUN / "agent.json"),
            "ZHIWO_ACCESS_LOG": str(RUN / "access.jsonl"),
            "ZHIWO_SERVER_LOG": str(RUN / "server.log"),
            "MNEMOSYNE_FASTEMBED_CACHE_DIR": str(CACHE),
            "ZHIWO_AGENT_CREDENTIAL": credential,
        }
    )
    return env


def public_env(env: dict[str, str]) -> dict[str, str]:
    shown = {}
    for key, value in env.items():
        if key == "ZHIWO_AGENT_CREDENTIAL":
            shown[key] = "<redacted>"
        elif key in {"PATH", "PATHEXT"}:
            shown[key] = "<inherited>"
        else:
            shown[key] = value
    return shown


def write_opencode_config(env: dict[str, str]) -> None:
    config = {
        "$schema": "https://opencode.ai/config.json",
        "model": OPENCODE_MODEL,
        "mcp": {
            "obsidian-brain": {
                "type": "local",
                "command": ["npx", "-y", "obsidian-brain@latest", "server"],
                "enabled": False,
            },
            "zhiwo": {
                "type": "local",
                "command": [str(PYTHON), str(SERVER)],
                "enabled": True,
                "environment": env,
            },
        },
    }
    (RUN / "opencode.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_mcp_config(path: Path, env: dict[str, str]) -> None:
    config = {
        "mcpServers": {
            "zhiwo": {
                "command": str(PYTHON),
                "args": [str(SERVER)],
                "env": env,
            }
        }
    }
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def prepare(token: str) -> Path:
    if not SOURCE_DB.exists() or not SOURCE_CONTROL.exists():
        raise SystemExit("BLOCKED: P0.3 synthetic library snapshot is missing")
    if not CACHE.exists():
        raise SystemExit("BLOCKED: local embedding cache is missing")
    data_dir = RUN / "data"
    if data_dir.exists():
        shutil.rmtree(data_dir)
    data_dir.mkdir(parents=True)
    shutil.copy2(SOURCE_DB, data_dir / "mnemosyne.db")
    shutil.copy2(SOURCE_CONTROL, data_dir / "control.json")
    for name in ("access.jsonl", "server.log"):
        path = RUN / name
        if path.exists():
            path.unlink()
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    (RUN / "agent.json").write_text(
        json.dumps(
            {"agent_id": "agent-p04-test", "credential_sha256": digest},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (RUN / "credential.txt").write_text(token, encoding="utf-8")
    return data_dir


def log_offset() -> int:
    path = RUN / "access.jsonl"
    return path.stat().st_size if path.exists() else 0


def read_events(offset: int) -> list[dict]:
    path = RUN / "access.jsonl"
    if not path.exists():
        return []
    chunk = path.read_bytes()[offset:]
    events = []
    for line in chunk.decode("utf-8").splitlines():
        if line.strip():
            events.append(json.loads(line))
    return events


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    import ctypes

    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    ctypes.windll.kernel32.CloseHandle(handle)
    return True


def wait_until_dead(pid: int) -> bool:
    import time

    for _ in range(30):
        if not pid_alive(pid):
            return True
        time.sleep(0.5)
    return not pid_alive(pid)


def stop_pid(pid: int) -> None:
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        check=False,
        capture_output=True,
        text=True,
    )


def tool_result_text(result) -> str:
    chunks: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            chunks.append(text)
    if chunks:
        return "\n".join(chunks)
    if hasattr(result, "model_dump"):
        return json.dumps(result.model_dump(mode="json"), ensure_ascii=False, default=str)
    return str(result)


async def local_call(env: dict[str, str], label: str, question: str) -> dict:
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(
        command=str(PYTHON),
        args=[str(SERVER)],
        env=env,
        cwd=str(SPIKE_ROOT),
    )
    stderr_path = RUN / f"{label}-stderr.txt"
    with stderr_path.open("w", encoding="utf-8") as errlog:
        async with stdio_client(params, errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                called = await session.call_tool(
                    "search_memory",
                    {"query": question, "limit": 5},
                    read_timeout_seconds=180,
                )
    names = [tool.name for tool in listed.tools]
    return {
        "label": label,
        "tools": names,
        "tool_text": tool_result_text(called),
        "stderr_path": str(stderr_path),
    }


def command_version(command: list[str]) -> str:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"UNAVAILABLE:{type(exc).__name__}"
    text = " ".join(part.strip() for part in (completed.stdout, completed.stderr) if part).strip()
    return text.splitlines()[0] if text else f"exit:{completed.returncode}"


def opencode_call(env: dict[str, str], question: str, label: str) -> dict:
    write_opencode_config(env)
    prompt = (
        "请调用 zhiwo 的 search_memory 工具，查询这句话："
        f"{question}\n"
        "不要编造工具没有返回的个人记忆。如果工具返回错误，直接说明错误码，不要补写记忆原文。"
    )
    command = [
        str(OPENCODE),
        "run",
        prompt,
        "--dir",
        str(RUN),
        "--model",
        OPENCODE_MODEL,
        "--format",
        "json",
        "--auto",
        "--pure",
    ]
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        cwd=str(RUN),
    )
    stdout_path = RUN / f"{label}.stream.jsonl"
    stderr_path = RUN / f"{label}.stderr.txt"
    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")
    return {
        "label": label,
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "parsed": parse_opencode(completed.stdout),
    }


def parse_opencode(text: str) -> dict:
    uses: list[dict] = []
    tool_chunks: list[str] = []
    texts: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        collect_opencode(event, uses, tool_chunks, texts, seen)
    unique_tools = list(dict.fromkeys(tool_chunks))
    answer = [item for item in texts if item not in unique_tools and "request_id" not in item]
    return {
        "tool_uses": uses,
        "tool_results": "\n".join(unique_tools),
        "final_answer": "\n".join(answer),
    }


def collect_opencode(
    node,
    uses: list[dict],
    tool_chunks: list[str],
    texts: list[str],
    seen: set[str],
) -> None:
    if isinstance(node, dict):
        tool = node.get("tool")
        state = node.get("state") if isinstance(node.get("state"), dict) else {}
        if isinstance(tool, str) and "search_memory" in tool:
            item = {"name": tool, "input": state.get("input") or node.get("input")}
            key = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
            if key not in seen:
                seen.add(key)
                uses.append(item)
            output = state.get("output")
            if isinstance(output, str) and output:
                tool_chunks.append(output)
            elif isinstance(output, (dict, list)):
                tool_chunks.append(json.dumps(output, ensure_ascii=False))
        if node.get("type") in {"text", "reasoning"} and isinstance(node.get("text"), str):
            texts.append(node["text"])
        for value in node.values():
            collect_opencode(value, uses, tool_chunks, texts, seen)
    elif isinstance(node, list):
        for item in node:
            collect_opencode(item, uses, tool_chunks, texts, seen)


def parse_stream(text: str) -> dict:
    uses: list[dict] = []
    results: list[object] = []
    final = None
    seen: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "result":
            final = event.get("result")
        collect(event, uses, results, seen)
    return {
        "tool_uses": uses,
        "tool_results": flatten_text(results),
        "final_answer": final if isinstance(final, str) else json.dumps(final, ensure_ascii=False),
    }


def collect(node, uses: list[dict], results: list[object], seen: set[str]) -> None:
    if isinstance(node, dict):
        if node.get("type") == "tool_use":
            item = {"name": node.get("name"), "input": node.get("input")}
            key = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
            if key not in seen:
                seen.add(key)
                uses.append(item)
        if node.get("type") == "tool_result":
            results.append(node.get("content"))
        for value in node.values():
            collect(value, uses, results, seen)
    elif isinstance(node, list):
        for item in node:
            collect(item, uses, results, seen)


def flatten_text(nodes: list[object]) -> str:
    chunks: list[str] = []

    def walk(node) -> None:
        if isinstance(node, str):
            chunks.append(node)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            if isinstance(node.get("text"), str):
                chunks.append(node["text"])
            else:
                for value in node.values():
                    walk(value)

    walk(nodes)
    return "\n".join(chunks)


def phase_from_server(events: list[dict], tool_text: str, target: str) -> dict:
    started = [event for event in events if event.get("event") == "started"]
    listed = [event for event in events if event.get("event") == "tools_listed"]
    calls = [event for event in events if event.get("event") == "tool_result"]
    payload = calls[-1]["payload"] if calls else tool_text
    names = listed[-1].get("names") if listed else []
    pid = started[-1].get("pid") if started else None
    return {
        "initialized": any(event.get("event") == "initialized" for event in events),
        "tools": names,
        "pid": pid,
        "authenticated_process": started[-1].get("authenticated") if started else None,
        "capabilities": started[-1].get("capabilities") if started else None,
        "server_payload": payload,
        "target_in_tool_payload": target in str(payload),
        "outcomes": [event.get("outcome") for event in calls],
    }


def redact_text(text: str, secret: str) -> tuple[str, bool]:
    if secret and secret in text:
        return text.replace(secret, "[REDACTED]"), True
    return text, False


def package_versions() -> dict[str, str]:
    found = {}
    for name in ("mcp", "mnemosyne-memory", "fastembed", "sqlite-vec"):
        try:
            found[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            found[name] = "MISSING"
    return found


def main() -> None:
    if sys.platform != "win32" or platform.system() != "Windows" or os.environ.get("WSL_DISTRO_NAME"):
        raise SystemExit("BLOCKED: P0.4 requires native Windows")
    question, target = load_question()
    token = secrets.token_urlsafe(32)
    while WRONG in token:
        token = secrets.token_urlsafe(32)
    data_dir = prepare(token)
    good_env = child_env(token, data_dir)
    bad_env = child_env(WRONG, data_dir)
    write_mcp_config(RUN / "mcp.good.json", good_env)
    write_mcp_config(RUN / "mcp.bad.json", bad_env)
    startup = {
        "command": str(PYTHON),
        "args": [str(SERVER)],
        "env": public_env(good_env),
        "cwd": str(SPIKE_ROOT),
    }

    leaked = False

    def absorb(text: str) -> str:
        nonlocal leaked
        cleaned, found = redact_text(text, token)
        leaked = leaked or found
        return re.sub(r"ark-[A-Za-z0-9-]{8,}", "[REDACTED]", cleaned)

    emit({"phase": "prepared"})
    preflight = []
    try:
        for label, env in (("preflight-good", good_env), ("preflight-bad", bad_env)):
            offset = log_offset()
            local = asyncio.run(local_call(env, label, question))
            events = read_events(offset)
            pids = [event.get("pid") for event in events if event.get("event") == "started"]
            dead = all(wait_until_dead(pid) for pid in pids if isinstance(pid, int))
            row = {
                **phase_from_server(events, local["tool_text"], target),
                "label": label,
                "client_tool_text": absorb(local["tool_text"]),
                "process_exited": dead,
            }
            preflight.append(row)
            emit(
                {
                    "phase": label,
                    "tools": row.get("tools"),
                    "target": row.get("target_in_tool_payload"),
                    "outcome": row.get("outcomes"),
                }
            )
    except Exception as exc:
        preflight.append({"label": "preflight", "error": f"{type(exc).__name__}: {absorb(str(exc))}"})

    client_runs = []
    client_error = None
    if not any(row.get("label") == "preflight-good" and row.get("target_in_tool_payload") for row in preflight):
        client_error = "local bridge did not return the target memory; real client was not started"
    else:
        try:
            calls = (
                ("client-good-1", good_env),
                ("client-good-2", good_env),
                ("client-bad", bad_env),
            )
            for label, env in calls:
                offset = log_offset()
                previous = [
                    event.get("pid")
                    for event in read_events(0)
                    if event.get("event") == "started" and isinstance(event.get("pid"), int)
                ]
                if previous and not wait_until_dead(previous[-1]):
                    stop_pid(previous[-1])
                    wait_until_dead(previous[-1])
                result = opencode_call(env, question, label)
                events = read_events(offset)
                phase = phase_from_server(events, "", target)
                parsed = result["parsed"]
                tool_text = absorb(parsed["tool_results"])
                final_answer = absorb(parsed["final_answer"] or "")
                pid = phase.get("pid")
                exited = wait_until_dead(pid) if isinstance(pid, int) else False
                if isinstance(pid, int) and not exited:
                    stop_pid(pid)
                    exited = wait_until_dead(pid)
                row = {
                        "label": label,
                        "exit_code": result["exit_code"],
                        "initialized": phase["initialized"],
                        "tools": phase["tools"],
                        "pid": pid,
                        "process_exited": exited,
                        "capabilities": phase["capabilities"],
                        "authenticated_process": phase["authenticated_process"],
                        "server_payload": absorb(str(phase["server_payload"])),
                        "client_tool_uses": parsed["tool_uses"],
                        "client_tool_result": tool_text,
                        "model_final_answer": final_answer,
                        "target_in_server_payload": target in str(phase["server_payload"]),
                        "target_in_client_tool_result": target in tool_text,
                        "outcomes": phase["outcomes"],
                        "stderr_excerpt": absorb(result["stderr"])[:2000],
                    }
                client_runs.append(row)
                emit(
                    {
                        "phase": label,
                        "exit": row["exit_code"],
                        "initialized": row["initialized"],
                        "tools": row["tools"],
                        "target": row["target_in_client_tool_result"],
                        "pid": row["pid"],
                    }
                )
        except Exception as exc:
            client_error = f"{type(exc).__name__}: {absorb(str(exc))}"

    files_to_scan = [RUN / "access.jsonl", RUN / "server.log"]
    files_to_scan.extend(RUN.glob("*-stderr.txt"))
    files_to_scan.extend(RUN.glob("*.stream.jsonl"))
    credential_hits = []
    for path in files_to_scan:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if token in text:
            credential_hits.append(path.name)
            leaked = True
            path.write_text(text.replace(token, "[REDACTED]"), encoding="utf-8")

    bodies = other_bodies(target)
    bad_runs = [row for row in client_runs if row.get("label") == "client-bad"]
    good_runs = [row for row in client_runs if str(row.get("label", "")).startswith("client-good")]
    bad_text = "\n".join(
        str(row.get("client_tool_result") or "") + "\n" + str(row.get("server_payload") or "")
        for row in bad_runs
    )
    bad_clean = (
        bool(bad_runs)
        and "UNAUTHENTICATED" in bad_text
        and target not in bad_text
        and all(body not in bad_text for body in bodies)
    )
    good_ok = (
        len(good_runs) == 2
        and all(row.get("initialized") for row in good_runs)
        and all(row.get("tools") == ["search_memory"] for row in good_runs)
        and all(row.get("target_in_client_tool_result") for row in good_runs)
        and all(row.get("target_in_server_payload") for row in good_runs)
        and all(row.get("process_exited") for row in good_runs)
        and good_runs[0].get("pid") != good_runs[1].get("pid")
    )
    bad_ok = (
        len(bad_runs) == 1
        and bad_runs[0].get("initialized")
        and bad_runs[0].get("tools") == ["search_memory"]
        and bad_clean
        and bad_runs[0].get("process_exited")
    )
    no_resources = all(
        (row.get("capabilities") or {}).get("resources") is False
        and (row.get("capabilities") or {}).get("prompts") is False
        for row in good_runs + bad_runs
    )
    passed = good_ok and bad_ok and no_resources and not leaked and client_error is None

    cursor_version = command_version(["cursor", "--version"])
    opencode_version = command_version([str(OPENCODE), "--version"])
    result = {
        "task": "P0.4",
        "pass": passed,
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
        },
        "client": {
            "name": "OpenCode",
            "version": opencode_version,
            "model": OPENCODE_MODEL,
            "invocation": [
                str(OPENCODE),
                "run",
                "<普通中文问题>",
                "--dir",
                "<runs/p0_4>",
                "--model",
                OPENCODE_MODEL,
                "--format",
                "json",
                "--auto",
                "--pure",
            ],
            "config": "experiments/kernel_spike/runs/p0_4/opencode.json",
            "cursor_version": cursor_version,
            "cursor_not_used_because": (
                "Cursor 3.21.18 的 CLI 能登记 MCP，但没有可脚本化的无头工具调用。"
                "cursor agent --print 会被交给 Electron。"
            ),
            "claude_code_not_used_because": (
                "Claude Code 2.1.158 能连上 stdio 并在初始化消息里发现 search_memory，"
                "但模型请求返回 HTTP 400，Coding Plan 订阅不可用，工具没有执行。"
            ),
        },
        "server": {
            "entry": "experiments/kernel_spike/p0_4_stdio.py",
            "sdk": package_versions(),
            "startup": startup,
            "exposed_tools": ["search_memory"],
            "database": "copy of experiments/kernel_spike/runs/p0_3/zh/before-recall/mnemosyne.db",
            "embedding_model": "BAAI/bge-small-zh-v1.5",
            "embedding_cache": "experiments/kernel_spike/runs/p0_3/fastembed-cache",
            "offline": True,
        },
        "question": {
            "id": QUERY_ID,
            "text": question,
            "target_id": TARGET_ID,
            "target_content": target,
        },
        "preflight": preflight,
        "client_runs": client_runs,
        "client_error": client_error,
        "credential": {
            "transport": "stdio child env ZHIWO_AGENT_CREDENTIAL",
            "stored_as": "sha256 in runs/p0_4/agent.json",
            "agent_id": "agent-p04-test",
            "wrong_credential_rejected": bad_ok,
            "present_in_logs": credential_hits,
        },
        "limits": [
            "只暴露 search_memory，没有 get_context、propose_memory、explain_memory，也没有完整 Gateway 或 zhiwo.db。",
            "这一轮没有类别授权。测试凭证通过后能读到该合成库里已登记的记忆；错误凭证拿不到正文。",
            "MemoryItem.kind 固定为 unspecified。场景来自控制记录，不来自 Kernel memory_type。",
            "客户端最终回答由 OpenCode 调用的远程模型生成。记忆检索在本机、离线缓存上完成。",
            "Kernel recall 会更新实验副本里的 recall_count。P0.3 的原始库没有被这轮写入。",
            "P0.3 首次从 Hugging Face 下载 BAAI/bge-small-zh-v1.5 因 TLS 中断失败。模型文件后来从 Qdrant 的 GCS 压缩包手工放进缓存。安装体验留到产品化阶段。",
            "改写查询 r07 仍会在字面门槛被丢掉。本轮问题用的是已冻结的直接查询 d06。",
        ],
    }
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    if token in encoded:
        encoded = encoded.replace(token, "[REDACTED]")
        result["pass"] = False
        result["credential"]["present_in_logs"].append("result-json")
        encoded = json.dumps(result, ensure_ascii=False, indent=2).replace(token, "[REDACTED]")
    RESULT_PATH.write_text(encoded, encoding="utf-8")
    emit(
        {
            "pass": result["pass"],
            "client_error": client_error,
            "good": [
                {
                    "label": row.get("label"),
                    "initialized": row.get("initialized"),
                    "tools": row.get("tools"),
                    "target_in_client_tool_result": row.get("target_in_client_tool_result"),
                    "pid": row.get("pid"),
                }
                for row in good_runs
            ],
            "bad_ok": bad_ok,
            "credential_hits": credential_hits,
            "result": str(RESULT_PATH),
        }
    )
    if not result["pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
