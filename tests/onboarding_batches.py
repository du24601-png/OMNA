"""v2 front end: the onboarding step 3 / connect-import card batch logic, end to end.

Starts a throwaway OMNA service (temporary data directory, test mode, a fake
home holding a synthetic ~/.claude/CLAUDE.md, a free port that is never 8765),
then runs apps/web/src/batch.ts and api.ts under Node against it, through
tests/onboarding_batches.harness.ts. Needs `server/.venv` (or ZHIWO_PYTHON)
and the web app's node_modules (for esbuild). Synthetic data only. Writes
tests/results/onboarding_batches.json.

    python tests/onboarding_batches.py
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
import urllib.request
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RESULT_PATH = REPO / "tests" / "results" / "onboarding_batches.json"
CACHE = Path(os.environ.get("ZHIWO_FASTEMBED_CACHE_DIR") or REPO / "experiments" / "kernel_spike" / "runs" / "p0_3" / "fastembed-cache")
OWNER = "onboarding-owner"
MEMORIES = [("preference", "先给结论，再讲原因，不需要客套"), ("preference", "包管理用 pnpm，不用 npm")]
CLAUDE_MD = """# 关于我

- 独立开发者，主要写 TypeScript 和 Python

## 偏好

- 包管理用 pnpm，不用 npm
- git 提交信息用中文，动词开头
- git 提交信息用中文，动词开头写
- 测试框架用 Vitest，不用 Jest

## 项目

- 在做本地优先的笔记应用「纸鸢」
"""
BUNDLE = """
const { createRequire } = require("module");
const path = require("path");
const web = path.resolve(process.argv[1], "apps/web");
const vite = require("fs").realpathSync(path.join(web, "node_modules/vite/package.json"));
const esbuild = createRequire(vite)("esbuild");
esbuild.build({ entryPoints: [process.argv[2]], bundle: true, platform: "node", format: "esm", target: "node20", outfile: process.argv[3], logLevel: "error" })
  .catch(() => process.exit(1));
"""


def _free_port() -> int:
    while True:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        if port != 8765:
            return port


def _call(base: str, method: str, path: str, body=None, token: str = OWNER):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Idempotency-Key": str(uuid.uuid4())}
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read() or b"null")


def run() -> dict:
    result: dict = {"platform": platform.platform(), "python": platform.python_version(), "pass": False}
    python = os.environ.get("ZHIWO_PYTHON") or str(REPO / "server" / ".venv" / "Scripts" / "python.exe")
    if not Path(python).exists() or not (REPO / "apps" / "web" / "node_modules" / "vite").exists():
        result["status"] = "NOT_RUN"
        result["reason"] = "needs server/.venv (or ZHIWO_PYTHON) and apps/web/node_modules"
        return result
    started = time.time()
    with tempfile.TemporaryDirectory(prefix="zhiwo-onboarding-", ignore_cleanup_errors=True) as raw:
        root = Path(raw)
        data, home, build = root / "data", root / "home", root / "build"
        (home / ".claude").mkdir(parents=True)
        build.mkdir()
        instructions = home / ".claude" / "CLAUDE.md"
        instructions.write_text(CLAUDE_MD, encoding="utf-8")
        mtime = instructions.stat().st_mtime_ns
        port = _free_port()
        base = f"http://127.0.0.1:{port}"
        env = {
            **{k: v for k, v in os.environ.items() if not k.startswith("ZHIWO_EXTRACTOR_")},
            "ZHIWO_DATA_DIR": str(data),
            "ZHIWO_OWNER_CREDENTIAL": OWNER,
            "ZHIWO_FASTEMBED_CACHE_DIR": str(CACHE),
            "ZHIWO_TEST_MODE": "1",
            "ZHIWO_CLIENT_HOME": str(home),
            "HF_HUB_OFFLINE": "1",
            "PYTHONPATH": str(REPO / "server"),
        }
        env.pop("ZHIWO_KERNEL_CONNECT_ONLY", None)
        log = (root / "service.log").open("w", encoding="utf-8")
        service = subprocess.Popen(
            [python, "-X", "utf8", "-m", "uvicorn", "zhiwo.api.app:app", "--host", "127.0.0.1", "--port", str(port), "--no-access-log"],
            cwd=REPO / "server", env=env, stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            for _ in range(240):
                try:
                    if _call(base, "GET", "/api/v1/health").get("embeddings_loaded"):
                        break
                except (urllib.error.URLError, ConnectionError):
                    pass
                if service.poll() is not None:
                    raise RuntimeError("service exited early")
                time.sleep(0.5)
            else:
                raise RuntimeError("service did not become ready")
            for category, content in MEMORIES:
                _call(base, "POST", "/api/v1/memories", {"content": content, "kind": "fact", "category": category, "scope": None, "share_enabled": True})
            agent = _call(base, "POST", "/api/v1/agents", {"name": "Codex"})
            _call(base, "PATCH", f"/api/v1/agents/{agent['id']}", {"allowed_tools": ["get_context", "search_memory"], "allowed_categories": ["preference", "goal"]})
            bundle = build / "harness.mjs"
            subprocess.run(["node", "-e", BUNDLE, str(REPO), str(REPO / "tests" / "onboarding_batches.harness.ts"), str(bundle)], check=True)
            harness = subprocess.run(
                ["node", str(bundle)],
                env={**os.environ, "OMNA_BASE": base, "OMNA_OWNER": OWNER, "OMNA_AGENT_ID": agent["id"], "OMNA_AGENT_TOKEN": agent["credential"]},
                capture_output=True, text=True, encoding="utf-8", timeout=600,
            )
            if harness.returncode != 0:
                raise RuntimeError(harness.stderr.strip()[-2000:])
            outcome = json.loads(harness.stdout.strip().splitlines()[-1])
            outcome["checks"].append({"name": "instruction file not modified", "ok": instructions.stat().st_mtime_ns == mtime})
            result["checks"] = outcome["checks"]
            result["pass"] = all(item["ok"] for item in outcome["checks"])
        except Exception as exc:  # noqa: BLE001 - recorded as the failure
            result["error"] = str(exc)
        finally:
            service.terminate()
            try:
                service.wait(timeout=20)
            except subprocess.TimeoutExpired:
                service.kill()
            log.close()
            if not result["pass"]:
                result["service_log_tail"] = (root / "service.log").read_text(encoding="utf-8", errors="replace")[-3000:]
    result["seconds"] = round(time.time() - started, 1)
    return result


if __name__ == "__main__":
    outcome = run()
    if outcome.get("status") != "NOT_RUN":
        RESULT_PATH.write_text(json.dumps(outcome, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for item in outcome.get("checks", []):
        print("PASS" if item["ok"] else "FAIL", item["name"])
    print(json.dumps({k: v for k, v in outcome.items() if k not in ("checks", "service_log_tail")}, ensure_ascii=False))
    raise SystemExit(0 if outcome["pass"] else 1)
