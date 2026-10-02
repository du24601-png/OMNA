"""L5 on Windows: real OpenCode -> real stdio bridge -> real OMNA service (real Kernel).

Each scenario starts its own service in a temporary data directory. Nothing
touches the user's own OMNA library or the user's OpenCode config: the
one-click connect writes into a temporary home (test mode), and OpenCode runs
with --pure and a project config in a temporary directory.

Usage (from the repo root):
    server\\.venv\\Scripts\\python.exe tests\\agent_extract\\run_opencode.py [scenario ...]
Scenarios: A1 A2 (preset as shipped), C1 C2 (all categories), E1 (import first).
Results: tests\\agent_extract\\results_opencode\\<scenario>.json
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "server"
PYTHON = SERVER / ".venv" / "Scripts" / "python.exe"
CACHE = REPO / "experiments" / "kernel_spike" / "runs" / "p0_3" / "fastembed-cache"
OUT = Path(__file__).resolve().parent / "results_opencode"
MODEL = os.environ.get("OPENCODE_MODEL", "opencode-go/deepseek-v4-flash")
ALL_CATEGORIES = ["identity", "goal", "preference", "project", "event", "other"]
SAMPLE = (
    "我叫林舟，在杭州做独立开发，一个人运营一家小公司。\n"
    "今年的目标是把记账小程序做到 1000 个付费用户。\n"
    "我写东西喜欢先给结论，不要太多客套话。\n"
    "现在主要在做一个叫「青柠账本」的项目，用 Flutter 开发。\n"
    "上周三我把服务器从阿里云迁到了腾讯云。\n"
    "我习惯早上六点起床写代码。\n"
    "我养了一只叫豆包的猫。\n"
)
PROMPT_TEXT = "帮我把下面这段自我介绍里值得长期记住的信息存到知我里（之后我会在知我里确认）。\n\n" + SAMPLE
PROMPT_IMPORT = "我刚在知我里导入了一段自我介绍，但我没配置提取模型。请你帮我把它提取成待确认的记忆。"
SCENARIOS = {
    "A1": ("preset", False), "A2": ("preset", False),
    "C1": ("all", False), "C2": ("all", False),
    "E1": ("all", True),
}


def find_opencode() -> Path:
    configured = os.environ.get("OPENCODE_BIN")
    if configured:
        return Path(configured)
    candidates = []
    found = shutil.which("opencode")
    if found:
        candidates.append(Path(found).parent / "node_modules" / "opencode-ai" / "bin" / "opencode.exe")
        candidates.append(Path(found))
    home = Path.home()
    candidates.append(home / "npm-global" / "node_modules" / "opencode-ai" / "bin" / "opencode.exe")
    candidates.append(Path(os.environ.get("APPDATA", "")) / "npm" / "node_modules" / "opencode-ai" / "bin" / "opencode.exe")
    for path in candidates:
        if path.is_file():
            return path
    raise SystemExit("opencode not found; set OPENCODE_BIN")


def call(base: str, method: str, path: str, token: str, body=None, idem: bool = False):
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    if idem:
        headers["Idempotency-Key"] = str(uuid.uuid4())
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def wait_health(base: str) -> None:
    deadline = time.time() + 180
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base + "/health", timeout=2):
                return
        except OSError:
            time.sleep(0.5)
    raise RuntimeError("service did not start")


def walk_parts(stdout: str):
    """Collect tool calls and assistant text from `opencode run --format json`."""
    tools, texts, seen = [], [], set()

    def walk(node):
        if isinstance(node, dict):
            tool = node.get("tool")
            state = node.get("state") if isinstance(node.get("state"), dict) else None
            if isinstance(tool, str) and state is not None:
                key = node.get("callID") or node.get("id") or json.dumps(node, sort_keys=True, ensure_ascii=False)
                output = state.get("output")
                if not isinstance(output, str):
                    output = json.dumps(output, ensure_ascii=False)
                item = {"tool": tool, "status": state.get("status"), "input": state.get("input"), "output": (output or "")[:600]}
                if key in seen:
                    for existing in tools:
                        if existing["_key"] == key:
                            existing.update(item)
                else:
                    seen.add(key)
                    tools.append({"_key": key, **item})
            if node.get("type") == "text" and isinstance(node.get("text"), str):
                texts.append(node["text"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                walk(json.loads(line))
            except json.JSONDecodeError:
                pass
    for item in tools:
        item.pop("_key", None)
    return tools, texts


def run(name: str, opencode: Path) -> dict:
    grant, use_import = SCENARIOS[name]
    owner = "ae-owner-" + uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix=f"ae-oc-{name}-", ignore_cleanup_errors=True) as raw:
        root = Path(raw)
        data, home, work = root / "data", root / "home", root / "run"
        for path in (data, home, work):
            path.mkdir()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        env = os.environ.copy()
        env.update({
            "ZHIWO_DATA_DIR": str(data), "ZHIWO_OWNER_CREDENTIAL": owner,
            "ZHIWO_FASTEMBED_CACHE_DIR": str(CACHE), "ZHIWO_HOST": "127.0.0.1",
            "ZHIWO_TEST_MODE": "1", "ZHIWO_CLIENT_HOME": str(home),
            "HF_HUB_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1", "PYTHONUTF8": "1",
        })
        for key in ("ZHIWO_KERNEL_CONNECT_ONLY", "ZHIWO_EXTRACTOR_API_KEY", "ZHIWO_EXTRACTOR_BASE_URL", "ZHIWO_EXTRACTOR_MODEL"):
            env.pop(key, None)
        log = (root / "service.log").open("w", encoding="utf-8")
        proc = subprocess.Popen(
            [str(PYTHON), "-X", "utf8", "-m", "uvicorn", "zhiwo.api.app:app", "--host", "127.0.0.1", "--port", str(port), "--no-access-log"],
            cwd=str(SERVER), env=env, stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            wait_health(base)
            body = {"preset": "propose", "confirm": True}
            if grant == "all":
                body.update({"allowed_tools": ["get_context", "search_memory", "propose_memory", "explain_memory"], "allowed_categories": ALL_CATEGORIES})
            status, connected = call(base, "POST", "/api/v1/agent-clients/opencode/connect", owner, body, idem=True)
            assert status == 200, connected
            written = json.loads((home / ".config" / "opencode" / "opencode.json").read_text(encoding="utf-8"))
            entry = written["mcp"]["zhiwo"]
            secret = entry["environment"]["ZHIWO_AGENT_CREDENTIAL"]
            _, agents = call(base, "GET", "/api/v1/agents", owner)
            granted = {"tools": agents["agents"][0]["allowed_tools"], "categories": agents["agents"][0]["allowed_categories"]}
            if use_import:
                status, job = call(base, "POST", "/api/v1/imports", owner, {"kind": "paste", "text": SAMPLE}, idem=True)
                assert status == 200, job
            config = {
                "$schema": "https://opencode.ai/config.json",
                "model": MODEL,
                "mcp": {"zhiwo": {**entry, "enabled": True}},
                "permission": {"edit": "deny", "bash": "deny", "webfetch": "deny"},
            }
            (work / "opencode.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
            started = time.time()
            done = subprocess.run(
                [str(opencode), "run", PROMPT_IMPORT if use_import else PROMPT_TEXT, "--dir", str(work), "--model", MODEL, "--format", "json", "--auto", "--pure"],
                cwd=str(work), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=480,
            )
            elapsed = round(time.time() - started, 1)
            stdout = done.stdout.replace(secret, "[redacted]").replace(owner, "[redacted]")
            tools, texts = walk_parts(stdout)
            _, proposals = call(base, "GET", "/api/v1/proposals?status=pending", owner)
            _, events = call(base, "GET", "/api/v1/access-events", owner)
            stored = proposals.get("proposals", [])
            proposes = [t for t in tools if "propose_memory" in t["tool"]]
            ok = [t for t in proposes if '"status":"pending"' in (t.get("output") or "").replace(" ", "")]
            result = {
                "scenario": name, "client": "OpenCode", "model": MODEL, "grant": granted, "import_first": use_import,
                "exit_code": done.returncode, "elapsed_s": elapsed,
                "tool_calls": len(tools), "propose_calls": len(proposes),
                "propose_succeeded": len(ok), "propose_failed": len(proposes) - len(ok),
                "distinct_request_ids": len({json.dumps((t.get("input") or {}).get("request_id")) for t in proposes}),
                "stored_pending": len(stored),
                "stored": [{
                    "content": p["payload"]["content"], "category": p["payload"]["category"], "kind": p["payload"]["kind"],
                    "evidence": p["evidence"].get("text"), "evidence_in_source": (p["evidence"].get("text") or "") in SAMPLE,
                    "verification": p["evidence"].get("verification"),
                } for p in stored],
                "audit_events": len(events.get("events", [])),
                "audit_rejected": sum(1 for e in events.get("events", []) if e.get("outcome") == "rejected"),
                "final_text": texts[-1] if texts else None,
                "all_text": texts,
                "tools": tools,
                "stderr_tail": done.stderr[-1500:].replace(secret, "[redacted]"),
            }
            (OUT / f"{name}.raw.jsonl").write_text(stdout, encoding="utf-8")
            return result
        finally:
            proc.terminate()
            try:
                proc.wait(15)
            except subprocess.TimeoutExpired:
                proc.kill()
            log.close()


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("run on Windows")
    if not PYTHON.is_file() or not CACHE.is_dir():
        raise SystemExit("server venv or embedding cache missing")
    OUT.mkdir(exist_ok=True)
    opencode = find_opencode()
    version = subprocess.run([str(opencode), "--version"], capture_output=True, text=True).stdout.strip()
    names = sys.argv[1:] or list(SCENARIOS)
    summary = {"opencode": str(opencode), "version": version, "model": MODEL, "runs": []}
    for name in names:
        print(f"== {name}", flush=True)
        try:
            result = run(name, opencode)
            result["opencode_version"] = version
        except Exception as exc:  # noqa: BLE001
            result = {"scenario": name, "error": f"{type(exc).__name__}: {exc}"}
        (OUT / f"{name}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        brief = {k: result.get(k) for k in ("scenario", "error", "propose_calls", "propose_succeeded", "stored_pending", "audit_events", "elapsed_s")}
        summary["runs"].append(brief)
        print(json.dumps(brief, ensure_ascii=False), flush=True)
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
