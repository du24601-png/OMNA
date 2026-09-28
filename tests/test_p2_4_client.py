"""P2.4: one real OpenCode client through the stdio bridge.

The bridge calls the running service. It does not open the database or the
Kernel. A12 full disconnect is not attempted. Electron is not packaged.
"""

from __future__ import annotations

import asyncio
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
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SERVER = REPO / "server"
PYTHON = SERVER / ".venv" / "Scripts" / "python.exe"
CACHE = REPO / "experiments" / "kernel_spike" / "runs" / "p0_3" / "fastembed-cache"
FIXTURE = REPO / "experiments" / "kernel_spike" / "fixtures" / "p0_3_queries.json"
DELIVERY = REPO / "tests" / "results" / "p2_3_delivery_windows.json"
RESULT_PATH = REPO / "tests" / "results" / "p2_4_windows.json"
BRIDGE = SERVER / "zhiwo" / "gateway" / "stdio_bridge.py"
def _find_opencode() -> Path:
    configured = os.environ.get("OPENCODE_BIN")
    if configured:
        return Path(configured)
    found = shutil.which("opencode")
    if found is None:
        return Path("opencode")
    # npm's .cmd shim cannot pass a multi-line prompt through cmd.exe.
    real = Path(found).parent / "node_modules" / "opencode-ai" / "bin" / "opencode.exe"
    return real if real.is_file() else Path(found)


OPENCODE = _find_opencode()
OPENCODE_MODEL = "opencode-go/deepseek-v4-flash"
IRRELEVANT = "如何校准实验室里的激光干涉仪"
PENDING = "待审探针：紫水晶计划还没有批准。"
REJECTED = "拒绝探针：青金石口令已经被拒绝。"
PREFERENCE = "权限探针：只向偏好连接返回这句。"
PROJECT = "权限探针：项目类别的句子不能给偏好连接。"
EXPIRED = "过期探针：这条有效期已过的句子不能当当前返回。"
FRAGMENT = "已审核片段：只解释这一句。"
EXTRA = "来源同页项目句：青瓷项目编号不该被解释出来。"
OLD = "旧版本句子：这条历史正文不该出现在解释里。"
PROPOSAL = "所有回答都越短越好。"
TARGET = "周末喜欢骑公路自行车，单次大约四十公里。"
QUERY = "周末是不是骑公路自行车并且单次大约四十公里"


def _request(method: str, url: str, token: str | None = None, body: dict | None = None, idem: bool = False) -> tuple[int, dict | list | str]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if idem:
        headers["Idempotency-Key"] = str(uuid.uuid4())
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            raw = response.read().decode("utf-8")
            status = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        status = exc.code
    if not raw:
        return status, ""
    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, raw


def _wait_health(origin: str) -> bool:
    deadline = time.time() + 180
    while time.time() < deadline:
        try:
            status, body = _request("GET", f"{origin}/health")
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(0.5)
            continue
        if status == 200 and isinstance(body, dict) and body.get("status") == "ok":
            return True
        time.sleep(0.5)
    return False


def _bridge_env(origin: str, credential: str) -> dict[str, str]:
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
            "PYTHONPATH": str(SERVER),
            "ZHIWO_API_ORIGIN": origin,
            "ZHIWO_AGENT_CREDENTIAL": credential,
        }
    )
    return env


def _tool_text(result) -> str:
    chunks: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            chunks.append(text)
    if chunks:
        return "\n".join(chunks)
    return str(result)


def _parse_payload(text: str) -> dict | None:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        loaded = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return loaded if isinstance(loaded, dict) else None


async def _stdio_session(env: dict[str, str], calls: list[tuple[str, dict]]) -> dict:
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(
        command=str(PYTHON),
        args=["-m", "zhiwo.gateway.stdio_bridge"],
        env=env,
        cwd=str(SERVER),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            outputs = []
            for name, arguments in calls:
                called = await session.call_tool(name, arguments, read_timeout_seconds=180)
                outputs.append(_tool_text(called))
    return {"tools": [tool.name for tool in listed.tools], "outputs": outputs}


def _events(db: Path) -> list[sqlite3.Row]:
    connection = sqlite3.connect(db, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        return list(connection.execute("SELECT * FROM access_events ORDER BY rowid"))
    finally:
        connection.close()


def _agent_row(db: Path, agent_id: str) -> sqlite3.Row:
    connection = sqlite3.connect(db, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
    finally:
        connection.close()


def _insert_source(db: Path, content: str) -> str:
    source_id = str(uuid.uuid4())
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    connection = sqlite3.connect(db, timeout=30)
    try:
        connection.execute(
            """
            INSERT INTO sources (id, kind, name, content, content_hash, imported_at)
            VALUES (?, 'paste', NULL, ?, ?, ?)
            """,
            (source_id, content, digest, "2026-09-25T00:00:00+00:00"),
        )
        connection.commit()
    finally:
        connection.close()
    return source_id


def _insert_proposal(db: Path, content: str, source_id: str) -> str:
    proposal_id = str(uuid.uuid4())
    payload = {"content": content, "kind": "fact", "category": "other", "scope": None, "share_enabled": True}
    connection = sqlite3.connect(db, timeout=30)
    try:
        connection.execute(
            """
            INSERT INTO proposals (
                id, origin, change_type, payload_json, evidence_json, status,
                source_id, job_id, created_at
            ) VALUES (?, 'import', 'add', ?, ?, 'pending', ?, ?, ?)
            """,
            (
                proposal_id,
                json.dumps(payload, ensure_ascii=False),
                json.dumps({"text": content}, ensure_ascii=False),
                source_id,
                str(uuid.uuid4()),
                "2026-09-25T00:00:00+00:00",
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return proposal_id


def _display(enabled: bool, status: str) -> str:
    if not enabled:
        return "已停用"
    return "已连接" if status == "verified" else "待验证"


def _stop(pid: int) -> None:
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False, capture_output=True, text=True)


def _redact(text: str, secrets: list[str]) -> str:
    cleaned = text
    for secret in secrets:
        if secret:
            cleaned = cleaned.replace(secret, "[redacted]")
    return cleaned


def _opencode(run: Path, env: dict[str, str], prompt: str, label: str) -> dict:
    config = {
        "$schema": "https://opencode.ai/config.json",
        "model": OPENCODE_MODEL,
        "mcp": {
            "zhiwo": {
                "type": "local",
                "command": [str(PYTHON), "-m", "zhiwo.gateway.stdio_bridge"],
                "enabled": True,
                "environment": env,
            }
        },
    }
    (run / "opencode.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    completed = subprocess.run(
        [
            str(OPENCODE),
            "run",
            prompt,
            "--dir",
            str(run),
            "--model",
            OPENCODE_MODEL,
            "--format",
            "json",
            "--auto",
            "--pure",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=240,
        cwd=str(run),
    )
    return {"exit_code": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr, "label": label}


def _collect_tools(text: str) -> list[dict]:
    found: list[dict] = []
    seen: set[str] = set()

    def walk(node) -> None:
        if isinstance(node, dict):
            tool = node.get("tool")
            state = node.get("state") if isinstance(node.get("state"), dict) else {}
            if isinstance(tool, str) and any(name in tool for name in ("search_memory", "propose_memory", "explain_memory", "get_context")):
                output = state.get("output")
                if isinstance(output, (dict, list)):
                    output = json.dumps(output, ensure_ascii=False)
                item = {"name": tool, "output": output if isinstance(output, str) else ""}
                key = json.dumps(item, ensure_ascii=False, sort_keys=True)
                if key not in seen:
                    seen.add(key)
                    found.append(item)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            walk(json.loads(line))
        except json.JSONDecodeError:
            continue
    return found


def _snapshot_match(db: Path, agent_id: str, text: str) -> dict:
    payload = _parse_payload(text or "")
    if payload is None:
        return {"matched": False, "delivery": None}
    request_id = payload.get("request_id")
    rows = [
        row
        for row in _events(db)
        if row["agent_id"] == agent_id and (request_id is None or row["request_id"] == request_id)
    ]
    if request_id is None:
        rows = [row for row in _events(db) if row["agent_id"] == agent_id]
    if not rows:
        return {"matched": False, "delivery": None}
    row = rows[-1]
    return {
        "matched": json.loads(row["response_snapshot"]) == payload,
        "delivery": row["delivery_state"],
        "outcome": row["outcome"],
    }


def test_real_client() -> None:
    if sys.platform != "win32" or platform.system() != "Windows" or os.environ.get("WSL_DISTRO_NAME"):
        raise AssertionError("P2.4 requires native Windows")
    if not CACHE.exists():
        raise AssertionError(f"local embedding cache is missing: {CACHE}")
    if not FIXTURE.exists():
        raise AssertionError("P0.3 query fixture is missing")
    if not PYTHON.exists() or not OPENCODE.exists():
        raise AssertionError("OpenCode or the server interpreter is missing")
    source = BRIDGE.read_text(encoding="utf-8")
    bridge_clean = (
        "import sqlite3" not in source
        and "kernel_client" not in source
        and "mnemosyne" not in source.lower()
        and "zhiwo.db" not in source
    )
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    delivery = json.loads(DELIVERY.read_text(encoding="utf-8")) if DELIVERY.exists() else {}
    owner = "p24-owner-" + uuid.uuid4().hex
    secrets = [owner]
    result: dict = {
        "task": "P2.4",
        "platform": platform.platform(),
        "client": "OpenCode 1.18.16",
        "model": OPENCODE_MODEL,
        "bridge": "zhiwo.gateway.stdio_bridge",
        "a12": "NOT_RUN",
        "electron": "NOT_RUN",
        "bridge_does_not_open_database": bridge_clean,
        "stage_complete": False,
    }
    server_proc = None
    try:
        with tempfile.TemporaryDirectory(prefix="zhiwo-p24-", ignore_cleanup_errors=True) as raw:
            data = Path(raw) / "data"
            run = Path(raw) / "opencode"
            data.mkdir()
            run.mkdir()
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            origin = f"http://127.0.0.1:{port}"
            env = os.environ.copy()
            env.update(
                {
                    "ZHIWO_DATA_DIR": str(data),
                    "ZHIWO_OWNER_CREDENTIAL": owner,
                    "ZHIWO_FASTEMBED_CACHE_DIR": str(CACHE),
                    "ZHIWO_HOST": "127.0.0.1",
                    "HF_HUB_OFFLINE": "1",
                    "HF_HUB_DISABLE_TELEMETRY": "1",
                    "PYTHONUTF8": "1",
                }
            )
            env.pop("ZHIWO_KERNEL_CONNECT_ONLY", None)
            env.pop("ZHIWO_EXTRACTOR_API_KEY", None)
            env.pop("ZHIWO_EXTRACTOR_BASE_URL", None)
            env.pop("ZHIWO_EXTRACTOR_MODEL", None)
            server_proc = subprocess.Popen(
                [str(PYTHON), "-m", "uvicorn", "zhiwo.api.app:app", "--host", "127.0.0.1", "--port", str(port)],
                cwd=str(SERVER),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if not _wait_health(origin):
                raise RuntimeError("local service did not become ready")
            preflight_secret = "p24-preflight-" + uuid.uuid4().hex
            secrets.append(preflight_secret)
            preflight = asyncio.run(_stdio_session(_bridge_env(origin, preflight_secret), []))
            if sorted(preflight["tools"]) != ["explain_memory", "get_context", "propose_memory", "search_memory"]:
                raise RuntimeError(f"bridge tools: {preflight['tools']}")
            db = data / "zhiwo.db"
            published = {}
            for memory in fixture["memories"]:
                status, body = _request(
                    "POST",
                    f"{origin}/api/v1/memories",
                    owner,
                    {
                        "content": memory["content"],
                        "kind": "fact",
                        "category": "other",
                        "scope": memory["scenario"],
                        "share_enabled": True,
                    },
                    idem=True,
                )
                if status != 200 or not isinstance(body, dict):
                    raise RuntimeError(f"publish {memory['id']} failed: {status}")
                published[memory["id"]] = body["memory_id"]
            for content, category in ((PREFERENCE, "preference"), (PROJECT, "project")):
                status, body = _request(
                    "POST",
                    f"{origin}/api/v1/memories",
                    owner,
                    {"content": content, "kind": "fact", "category": category, "share_enabled": True},
                    idem=True,
                )
                if status != 200:
                    raise RuntimeError(f"publish probe failed: {status}")
            status, _expired = _request(
                "POST",
                f"{origin}/api/v1/memories",
                owner,
                {
                    "content": EXPIRED,
                    "kind": "fact",
                    "category": "other",
                    "share_enabled": True,
                    "valid_until": "2001-01-01T00:00:00+00:00",
                },
                idem=True,
            )
            if status != 200:
                raise RuntimeError(f"publish expired failed: {status}")
            private_status, private_body = _request(
                "POST",
                f"{origin}/api/v1/memories",
                owner,
                {"content": "私有探针：解释接口不能暴露这条。", "kind": "fact", "category": "other", "share_enabled": False},
                idem=True,
            )
            if private_status != 200 or not isinstance(private_body, dict):
                raise RuntimeError("private memory was not saved")

            def create(name: str, tools: list[str], categories: list[str]) -> dict:
                status, body = _request("POST", f"{origin}/api/v1/agents", owner, {"name": name}, idem=True)
                if status != 200 or not isinstance(body, dict) or not body.get("credential"):
                    raise RuntimeError(f"create {name} failed: {status}")
                secrets.append(body["credential"])
                patched, _ = _request(
                    "PATCH",
                    f"{origin}/api/v1/agents/{body['id']}",
                    owner,
                    {"allowed_tools": tools, "allowed_categories": categories},
                    idem=True,
                )
                if patched != 200:
                    raise RuntimeError(f"grant {name} failed: {patched}")
                return body

            reader = create("合成阅读", ["get_context", "search_memory", "propose_memory", "explain_memory"], ["other", "preference", "project"])
            limited = create("合成偏好", ["search_memory"], ["preference"])
            blocked = create("合成无权限", [], [])
            opencode_agent = create("合成客户端", ["get_context", "search_memory", "propose_memory", "explain_memory"], ["other", "preference"])
            before_client = _agent_row(db, opencode_agent["id"])
            public_agent = {key: value for key, value in opencode_agent.items() if key != "credential"}

            source_id = _insert_source(db, f"{FRAGMENT}\n{EXTRA}\n{OLD}")
            pending_id = _insert_proposal(db, PENDING, source_id)
            rejected_id = _insert_proposal(db, REJECTED, source_id)
            reject_status, _reject_body = _request(
                "POST",
                f"{origin}/api/v1/proposals/{rejected_id}/decision",
                owner,
                {"decision": "reject"},
                idem=True,
            )

            print("opencode search", flush=True)
            first = _opencode(
                run,
                _bridge_env(origin, opencode_agent["credential"]),
                "请调用 zhiwo 的 search_memory 工具，query 使用这句话："
                f"{QUERY}\nlimit 为 5。不要编造工具没有返回的个人记忆。",
                "search",
            )
            first_tools = _collect_tools(first["stdout"])
            first_payload = next((item["output"] for item in first_tools if "search_memory" in item["name"] and item["output"]), "")
            first_match = _snapshot_match(db, opencode_agent["id"], first_payload)
            after_search = _agent_row(db, opencode_agent["id"])

            print("opencode propose", flush=True)
            propose_key = str(uuid.uuid4())
            proposed = _opencode(
                run,
                _bridge_env(origin, opencode_agent["credential"]),
                "请只调用 zhiwo 的 propose_memory，不要调用其他工具。\n"
                f"request_id 使用 {propose_key}。\n"
                'change 使用 {"type":"add","content":"所有回答都越短越好。","kind":"fact","category":"preference"}。\n'
                'evidence 使用 {"text":"用户希望更短。"}。\n'
                "不要把其他个人记忆写进回答。",
                "propose",
            )
            propose_tools = _collect_tools(proposed["stdout"])
            propose_payload = next((item["output"] for item in propose_tools if "propose_memory" in item["name"] and item["output"]), "")
            propose_body = _parse_payload(propose_payload) or {}
            proposal_id = propose_body.get("proposal_id")
            review_status = None
            if isinstance(proposal_id, str):
                review_status, _review_body = _request(
                    "POST",
                    f"{origin}/api/v1/proposals/{proposal_id}/decision",
                    owner,
                    {"decision": "reject"},
                    idem=True,
                )

            print("opencode search again", flush=True)
            again = _opencode(
                run,
                _bridge_env(origin, opencode_agent["credential"]),
                "请调用 zhiwo 的 search_memory，query 使用这句话："
                f"{QUERY}\n不要补充未被工具返回的建议。",
                "search-again",
            )
            again_tools = _collect_tools(again["stdout"])
            again_payload = next((item["output"] for item in again_tools if "search_memory" in item["name"] and item["output"]), "")
            again_match = _snapshot_match(db, opencode_agent["id"], again_payload)

            disable_status, disabled_body = _request(
                "PATCH",
                f"{origin}/api/v1/agents/{opencode_agent['id']}",
                owner,
                {"enabled": False},
                idem=True,
            )
            print("opencode disabled", flush=True)
            denied = _opencode(
                run,
                _bridge_env(origin, opencode_agent["credential"]),
                "请调用 zhiwo 的 search_memory，query 使用这句话："
                f"{QUERY}\n如果工具返回错误，只报告错误码，不要复述任何记忆原文。",
                "disabled",
            )
            denied_tools = _collect_tools(denied["stdout"])
            denied_payload = next((item["output"] for item in denied_tools if "search_memory" in item["name"] and item["output"]), "")
            denied_match = _snapshot_match(db, opencode_agent["id"], denied_payload)
            after_disable = _agent_row(db, opencode_agent["id"])

            print("stdio harness", flush=True)
            reader_env = _bridge_env(origin, reader["credential"])
            queries = [(row["id"], row["text"], row["target"]) for row in fixture["queries"]]
            harness_calls = [("search_memory", {"query": text, "limit": 5}) for _qid, text, _target in queries]
            harness_calls.append(("search_memory", {"query": IRRELEVANT, "limit": 5}))
            harness_calls.append(("search_memory", {"query": PENDING, "limit": 5}))
            harness_calls.append(("search_memory", {"query": REJECTED, "limit": 5}))
            harness_calls.append(("get_context", {"task": QUERY, "max_items": 5}))
            harness = asyncio.run(_stdio_session(reader_env, harness_calls))
            hits = []
            misses = []
            expired_leaked = False
            for index, (query_id, _text, target) in enumerate(queries):
                payload = _parse_payload(harness["outputs"][index]) or {}
                contents = [item.get("content") for item in payload.get("items") or [] if isinstance(item, dict)]
                target_content = next(row["content"] for row in fixture["memories"] if row["id"] == target)
                if target_content in contents[:5]:
                    hits.append(query_id)
                else:
                    misses.append(query_id)
                expired_leaked = expired_leaked or EXPIRED in json.dumps(payload, ensure_ascii=False)
            irrelevant = _parse_payload(harness["outputs"][len(queries)]) or {}
            pending_hit = _parse_payload(harness["outputs"][len(queries) + 1]) or {}
            rejected_hit = _parse_payload(harness["outputs"][len(queries) + 2]) or {}
            context_hit = _parse_payload(harness["outputs"][len(queries) + 3]) or {}
            owner_pending, owner_pending_body = _request(
                "GET",
                f"{origin}/api/v1/memories?query={urllib.parse.quote(PENDING)}",
                owner,
            )
            owner_profile, profile_body = _request("GET", f"{origin}/api/v1/profile", owner)

            limited_run = asyncio.run(
                _stdio_session(
                    _bridge_env(origin, limited["credential"]),
                    [
                        ("search_memory", {"query": PREFERENCE, "limit": 5}),
                        ("search_memory", {"query": PROJECT, "limit": 5}),
                    ],
                )
            )
            blocked_run = asyncio.run(
                _stdio_session(
                    _bridge_env(origin, blocked["credential"]),
                    [("search_memory", {"query": PREFERENCE, "limit": 5})],
                )
            )
            wrong_run = asyncio.run(
                _stdio_session(
                    _bridge_env(origin, "p24-wrong-credential"),
                    [("search_memory", {"query": PREFERENCE, "limit": 5})],
                )
            )
            owner_status, owner_cross = _request("GET", f"{origin}/api/v1/memories", limited["credential"])

            m20 = published["m20"]
            current_status, current = _request("GET", f"{origin}/api/v1/memories/{m20}", owner)
            unshare_status = None
            if current_status == 200 and isinstance(current, dict):
                unshare_status, _unshared = _request(
                    "PATCH",
                    f"{origin}/api/v1/memories/{m20}",
                    owner,
                    {
                        "content": current["content"],
                        "kind": current["kind"],
                        "category": current["category"],
                        "scope": current.get("scope"),
                        "share_enabled": False,
                        "base_revision": current["revision"],
                        "source_refs": current.get("source_ids") or [],
                    },
                    idem=True,
                )
            hidden = asyncio.run(
                _stdio_session(
                    reader_env,
                    [
                        ("search_memory", {"query": next(row["content"] for row in fixture["memories"] if row["id"] == "m20"), "limit": 5}),
                        ("search_memory", {"query": EXPIRED, "limit": 5}),
                    ],
                )
            )

            propose_run = asyncio.run(
                _stdio_session(
                    reader_env,
                    [
                        (
                            "propose_memory",
                            {
                                "request_id": str(uuid.uuid4()),
                                "change": {
                                    "type": "add",
                                    "content": FRAGMENT,
                                    "kind": "fact",
                                    "category": "other",
                                },
                                "evidence": {"text": FRAGMENT, "source_ref": source_id},
                            },
                        )
                    ],
                )
            )
            proposed_body = _parse_payload(propose_run["outputs"][0]) or {}
            accept_status = None
            accepted_id = None
            if isinstance(proposed_body.get("proposal_id"), str):
                accept_status, accepted = _request(
                    "POST",
                    f"{origin}/api/v1/proposals/{proposed_body['proposal_id']}/decision",
                    owner,
                    {"decision": "accept", "share_enabled": True},
                    idem=True,
                )
                if isinstance(accepted, dict):
                    accepted_id = accepted.get("memory_id")
            explain_outputs = ["", "", ""]
            if isinstance(accepted_id, str):
                explain_run = asyncio.run(
                    _stdio_session(
                        reader_env,
                        [
                            ("explain_memory", {"id": accepted_id}),
                            ("explain_memory", {"id": private_body["memory_id"]}),
                            ("explain_memory", {"id": str(uuid.uuid4())}),
                        ],
                    )
                )
                explain_outputs = explain_run["outputs"]
            explained = _parse_payload(explain_outputs[0]) or {}
            hidden_explain = _parse_payload(explain_outputs[1]) or {}
            missing_explain = _parse_payload(explain_outputs[2]) or {}
            reader_bridge_env = reader_env
            result.update(
                {
                    "tools": sorted(harness.get("tools") or []),
                    "hit_count": len(hits),
                    "misses": misses,
                    "irrelevant_count": len(irrelevant.get("items") or []),
                    "expired_absent_from_hits": expired_leaked is False,
                    "pending_absent": PENDING not in json.dumps(pending_hit, ensure_ascii=False),
                    "rejected_absent": REJECTED not in json.dumps(rejected_hit, ensure_ascii=False),
                    "owner_pending_status": owner_pending,
                    "owner_pending_absent": PENDING not in json.dumps(owner_pending_body, ensure_ascii=False),
                    "profile_status": owner_profile,
                    "profile_absent": PENDING not in json.dumps(profile_body, ensure_ascii=False) and REJECTED not in json.dumps(profile_body, ensure_ascii=False),
                    "context_has_target": TARGET in json.dumps(context_hit, ensure_ascii=False),
                    "limited_has_preference": PREFERENCE in limited_run["outputs"][0] and PROJECT not in limited_run["outputs"][0],
                    "limited_hides_project": PROJECT not in limited_run["outputs"][1],
                    "blocked_code": (_parse_payload(blocked_run["outputs"][0]) or {}).get("error", {}).get("code"),
                    "blocked_hides": PREFERENCE not in blocked_run["outputs"][0] and PROJECT not in blocked_run["outputs"][0],
                    "wrong_code": (_parse_payload(wrong_run["outputs"][0]) or {}).get("error", {}).get("code"),
                    "wrong_hides": PREFERENCE not in wrong_run["outputs"][0],
                    "owner_cross_status": owner_status,
                    "owner_cross_hides": PREFERENCE not in json.dumps(owner_cross, ensure_ascii=False),
                    "unshare_status": unshare_status,
                    "unshared_absent": next(row["content"] for row in fixture["memories"] if row["id"] == "m20") not in hidden["outputs"][0],
                    "expired_absent": EXPIRED not in hidden["outputs"][1],
                    "reject_status": reject_status,
                    "pending_id_kept": isinstance(pending_id, str),
                    "explain_fragment": (explained.get("result") or {}).get("evidence") == FRAGMENT,
                    "explain_hides_source": EXTRA not in explain_outputs[0] and OLD not in explain_outputs[0],
                    "explain_same_miss": hidden_explain.get("error", {}).get("message") == missing_explain.get("error", {}).get("message") == "memory not found",
                    "explain_hides_private": "私有探针" not in explain_outputs[1] and "私有探针" not in explain_outputs[2],
                    "accept_status": accept_status,
                    "client_pending_before": before_client["client_status"] == "pending" and bool(before_client["enabled"]),
                    "client_verified_after_search": after_search["client_status"] == "verified" and bool(after_search["enabled"]),
                    "client_search_match": first_match.get("matched") is True and first_match.get("delivery") == "sent",
                    "client_search_has_target": TARGET in first_payload,
                    "client_propose_status": propose_body.get("status"),
                    "client_review_status": review_status,
                    "client_again_match": again_match.get("matched") is True and again_match.get("delivery") == "sent",
                    "client_again_keeps_target": TARGET in again_payload and PROPOSAL not in again_payload,
                    "disable_status": disable_status,
                    "client_denied_match": denied_match.get("matched") is True and denied_match.get("delivery") == "sent",
                    "client_denied_code": (_parse_payload(denied_payload) or {}).get("error", {}).get("code"),
                    "client_denied_hides": TARGET not in denied_payload and PROPOSAL not in denied_payload,
                    "disabled_display": _display(bool(after_disable["enabled"]), after_disable["client_status"]),
                    "disabled_still_verified_bit": after_disable["client_status"] == "verified",
                    "bridge_env_has_data_dir": "ZHIWO_DATA_DIR" in reader_bridge_env,
                    "connected_word_before_client": "已连接" in json.dumps(public_agent, ensure_ascii=False),
                    "a10_audit_code": delivery.get("audit_code"),
                    "a10_interrupt_state": delivery.get("interrupt_state"),
                    "a10_uncertain_state": delivery.get("uncertain_state"),
                    "a10_model_read_claimed": delivery.get("model_read_claimed"),
                    "a10_revised_hides": delivery.get("revised_hides"),
                    "opencode_exit": [first["exit_code"], proposed["exit_code"], again["exit_code"], denied["exit_code"]],
                }
            )
        blob = json.dumps(result, ensure_ascii=False)
        result["credential_in_result"] = any(secret and secret in blob for secret in secrets)
        result["pass"] = all(
            [
                result.get("bridge_does_not_open_database") is True,
                result.get("bridge_env_has_data_dir") is False,
                result.get("tools") == ["explain_memory", "get_context", "propose_memory", "search_memory"],
                result.get("hit_count", 0) >= 16,
                result.get("irrelevant_count", 20) < 20,
                result.get("expired_absent_from_hits") is True,
                result.get("pending_absent") is True and result.get("rejected_absent") is True,
                result.get("owner_pending_absent") is True and result.get("profile_absent") is True,
                result.get("limited_has_preference") is True and result.get("limited_hides_project") is True,
                result.get("blocked_code") == "FORBIDDEN" and result.get("blocked_hides") is True,
                result.get("wrong_code") == "UNAUTHENTICATED" and result.get("wrong_hides") is True,
                result.get("owner_cross_status") == 401 and result.get("owner_cross_hides") is True,
                result.get("unshare_status") == 200 and result.get("unshared_absent") is True and result.get("expired_absent") is True,
                result.get("explain_fragment") is True and result.get("explain_hides_source") is True,
                result.get("explain_same_miss") is True and result.get("explain_hides_private") is True,
                result.get("client_pending_before") is True and result.get("client_verified_after_search") is True,
                result.get("client_search_match") is True and result.get("client_search_has_target") is True,
                result.get("client_propose_status") == "pending" and result.get("client_review_status") == 200,
                result.get("client_again_match") is True and result.get("client_again_keeps_target") is True,
                result.get("client_denied_match") is True and result.get("client_denied_code") == "FORBIDDEN",
                result.get("client_denied_hides") is True and result.get("disabled_display") == "已停用",
                result.get("a10_audit_code") == "AUDIT_UNAVAILABLE" and result.get("a10_interrupt_state") == "failed",
                result.get("a10_uncertain_state") == "unknown" and result.get("a10_model_read_claimed") is False,
                result.get("a10_revised_hides") is True,
                result.get("credential_in_result") is False and result.get("a12") == "NOT_RUN" and result.get("electron") == "NOT_RUN",
                result.get("reject_status") == 200 and result.get("pending_id_kept") is True,
                result.get("connected_word_before_client") is False,
            ]
        )
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["pass"] = False
    finally:
        if server_proc is not None and server_proc.poll() is None:
            _stop(server_proc.pid)
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if not result["pass"]:
        raise AssertionError(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    test_real_client()
