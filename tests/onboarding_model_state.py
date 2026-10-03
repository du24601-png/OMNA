"""Isolated regression for the import card's direct-file menu and consent copy.

Uses synthetic dev_onboarding data and a fake client home, two free ports (never
8765), and external Playwright dependencies. Health states and network failures
are browser-only fixtures; this is UI evidence, not a live extraction-model run.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RESULT = REPO / "tests/results/onboarding_model_state.json"


def free_port() -> int:
    while True:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        if port != 8765:
            return port


def wait_ready(url: str, process: subprocess.Popen, *, health: bool = False) -> None:
    for _ in range(240):
        if process.poll() is not None:
            raise RuntimeError(f"test process exited: {process.returncode}")
        try:
            request = urllib.request.Request(url, headers={"Authorization": "Bearer dev-onboarding-owner"})
            with urllib.request.urlopen(request, timeout=2) as response:
                if not health or json.loads(response.read()).get("embeddings_loaded"):
                    return
        except (urllib.error.URLError, TimeoutError):
            pass
        time.sleep(0.25)
    raise RuntimeError("isolated test service did not become ready")


def stop(process: subprocess.Popen) -> None:
    if process.poll() is None:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], check=True, capture_output=True)
        else:
            process.terminate()
        process.wait(timeout=20)


def run() -> dict:
    started = time.time()
    result = {"pass": False, "platform": platform.platform(), "scope": "rendered UI with synthetic health states; no live model calls"}
    playwright_module = os.environ.get("OMNA_PLAYWRIGHT_MODULE")
    evidence_value = os.environ.get("OMNA_UI_EVIDENCE_DIR")
    if not playwright_module or not evidence_value:
        return {**result, "status": "NOT_RUN", "reason": "set OMNA_PLAYWRIGHT_MODULE and OMNA_UI_EVIDENCE_DIR outside the repo"}
    evidence = Path(evidence_value).resolve()
    if evidence.is_relative_to(REPO):
        raise ValueError("screenshots must be outside the repository")
    evidence.mkdir(parents=True, exist_ok=True)
    node = os.environ.get("OMNA_NODE") or shutil.which("node")
    python = os.environ.get("ZHIWO_PYTHON") or sys.executable
    service_port, web_port = free_port(), free_port()
    while service_port == web_port:
        web_port = free_port()
    env = {key: value for key, value in os.environ.items() if not key.startswith("ZHIWO_EXTRACTOR_")}
    env["HF_HUB_OFFLINE"] = "1"
    processes = []
    with tempfile.TemporaryDirectory(prefix="omna-model-state-") as raw:
        folder = Path(raw)
        logs = []
        try:
            service_log = (folder / "service.log").open("w", encoding="utf-8")
            logs.append(service_log)
            service = subprocess.Popen([python, "-X", "utf8", str(REPO / "tests/dev_onboarding.py"), "serve", "--port", str(service_port), "--dir", str(folder / "runtime")], cwd=REPO, env=env, stdout=service_log, stderr=subprocess.STDOUT)
            processes.append(service)
            wait_ready(f"http://127.0.0.1:{service_port}/api/v1/health", service, health=True)
            web_log = (folder / "web.log").open("w", encoding="utf-8")
            logs.append(web_log)
            web_env = {**env, "VITE_API_ORIGIN": f"http://127.0.0.1:{service_port}"}
            web = subprocess.Popen([node, str(REPO / "apps/web/node_modules/vite/bin/vite.js"), "--host", "127.0.0.1", "--port", str(web_port), "--strictPort"], cwd=REPO / "apps/web", env=web_env, stdout=web_log, stderr=subprocess.STDOUT)
            processes.append(web)
            wait_ready(f"http://127.0.0.1:{web_port}/", web)
            test_env = {**env, "OMNA_BASE": f"http://127.0.0.1:{service_port}", "OMNA_WEB": f"http://127.0.0.1:{web_port}", "OMNA_TEST_HOME": str((folder / "runtime/home").resolve()), "OMNA_PLAYWRIGHT_MODULE": playwright_module, "OMNA_UI_EVIDENCE_DIR": str(evidence)}
            test = subprocess.run([node, str(REPO / "tests/onboarding_model_state.harness.cjs")], env=test_env, capture_output=True, text=True, encoding="utf-8", timeout=120)
            if test.returncode:
                raise RuntimeError(test.stderr[-2000:])
            outcome = json.loads(test.stdout.strip().splitlines()[-1])
            result.update(outcome)
            result["ports"] = [service_port, web_port]
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            for process in reversed(processes):
                stop(process)
            for log in logs:
                log.close()
            if "error" in result:
                result["log_tail"] = {file.name: file.read_text(encoding="utf-8", errors="replace")[-2000:] for file in folder.glob("*.log")}
    result["cleanup"] = {"processes_stopped": all(process.poll() is not None for process in processes), "temporary_data_removed": not folder.exists()}
    result["seconds"] = round(time.time() - started, 1)
    return result


if __name__ == "__main__":
    outcome = run()
    RESULT.write_text(json.dumps(outcome, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for item in outcome.get("checks", []):
        print("PASS" if item["ok"] else "FAIL", item["name"])
    print(json.dumps({key: value for key, value in outcome.items() if key not in ("checks", "log_tail")}, ensure_ascii=False))
    raise SystemExit(0 if outcome["pass"] else 1)
