"""Shared setup for the agent-as-extractor test round."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SERVER = REPO / "server"
EXTLIB = Path(os.environ.get("ZHIWO_TEST_EXTLIB", str(Path.home() / "extlib")))
RESULTS = HERE / "results"
RESULTS.mkdir(exist_ok=True)

# A short self-introduction with one or more facts per category.
SAMPLE = (
    "我叫林舟，在杭州做独立开发，一个人运营一家小公司。\n"
    "今年的目标是把记账小程序做到 1000 个付费用户。\n"
    "我写东西喜欢先给结论，不要太多客套话。\n"
    "现在主要在做一个叫「青柠账本」的项目，用 Flutter 开发。\n"
    "上周三我把服务器从阿里云迁到了腾讯云。\n"
    "我习惯早上六点起床写代码。\n"
    "我养了一只叫豆包的猫。\n"
)

GOLD = [
    {"content": "用户叫林舟，在杭州做独立开发", "kind": "fact", "category": "identity", "evidence": "我叫林舟，在杭州做独立开发"},
    {"content": "用户一个人运营一家小公司", "kind": "fact", "category": "identity", "evidence": "一个人运营一家小公司"},
    {"content": "今年目标：记账小程序做到 1000 个付费用户", "kind": "fact", "category": "goal", "evidence": "今年的目标是把记账小程序做到 1000 个付费用户"},
    {"content": "写作偏好先给结论，少客套", "kind": "fact", "category": "preference", "evidence": "我写东西喜欢先给结论，不要太多客套话"},
    {"content": "在做「青柠账本」项目，用 Flutter 开发", "kind": "fact", "category": "project", "evidence": "现在主要在做一个叫「青柠账本」的项目，用 Flutter 开发"},
    {"content": "上周三把服务器从阿里云迁到腾讯云", "kind": "event", "category": "event", "evidence": "上周三我把服务器从阿里云迁到了腾讯云"},
    {"content": "习惯早上六点起床写代码", "kind": "fact", "category": "preference", "evidence": "我习惯早上六点起床写代码"},
    {"content": "养了一只叫豆包的猫", "kind": "fact", "category": "other", "evidence": "我养了一只叫豆包的猫"},
]

ALL_CATEGORIES = ["identity", "goal", "preference", "project", "event", "other"]
OWNER = "ae-owner-" + uuid.uuid4().hex


def record(name: str, data) -> None:
    path = RESULTS / f"{name}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def set_env(data_dir: Path, home: Path) -> None:
    os.environ.update(
        {
            "ZHIWO_DATA_DIR": str(data_dir),
            "ZHIWO_OWNER_CREDENTIAL": OWNER,
            "ZHIWO_TEST_MODE": "1",
            "ZHIWO_CLIENT_HOME": str(home),
            "ZHIWO_HOST": "127.0.0.1",
        }
    )
    for key in ("ZHIWO_EXTRACTOR_API_KEY", "ZHIWO_EXTRACTOR_BASE_URL", "ZHIWO_EXTRACTOR_MODEL", "ZHIWO_KERNEL_CONNECT_ONLY"):
        os.environ.pop(key, None)


def owner_headers(key: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {OWNER}"}
    headers["Idempotency-Key"] = key or str(uuid.uuid4())
    return headers


def agent_headers(secret: str) -> dict:
    return {"Authorization": f"Bearer {secret}", "X-Zhiwo-Transport": "stdio"}


def secret_from_config(home: Path, client_id: str = "claude-code") -> dict:
    """Read the stdio entry the one-click connect wrote for this client."""
    assert client_id == "claude-code"
    data = json.loads((home / ".claude.json").read_text(encoding="utf-8"))
    return data["mcpServers"]["zhiwo"]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class LiveServer:
    """Real uvicorn process (with the Kernel stand-in) for MCP and CLI tests."""

    def __init__(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="ae-live-"))
        self.data = self.dir / "data"
        self.home = self.dir / "home"
        self.home.mkdir(parents=True)
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.log = self.dir / "service.log"
        self.proc = None

    def env(self) -> dict:
        env = dict(os.environ)
        env.update(
            {
                "ZHIWO_DATA_DIR": str(self.data),
                "ZHIWO_OWNER_CREDENTIAL": OWNER,
                "ZHIWO_TEST_MODE": "1",
                "ZHIWO_CLIENT_HOME": str(self.home),
                "PYTHONPATH": os.pathsep.join([str(EXTLIB), str(SERVER), str(HERE)]),
            }
        )
        for key in ("ZHIWO_EXTRACTOR_API_KEY", "ZHIWO_EXTRACTOR_BASE_URL", "ZHIWO_EXTRACTOR_MODEL"):
            env.pop(key, None)
        return env

    def start(self) -> "LiveServer":
        launcher = HERE / "serve.py"
        self.proc = subprocess.Popen(
            [sys.executable, str(launcher), str(self.port)],
            env=self.env(),
            stdout=self.log.open("w"),
            stderr=subprocess.STDOUT,
        )
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(self.base + "/health", timeout=1):
                    return self
            except OSError:
                time.sleep(0.2)
        raise RuntimeError("server did not start: " + self.log.read_text())

    def stop(self) -> None:
        if self.proc:
            self.proc.terminate()
            self.proc.wait(10)

    def call(self, method: str, path: str, body=None, headers=None) -> tuple[int, dict]:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(self.base + path, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def owner(self, method: str, path: str, body=None) -> tuple[int, dict]:
        return self.call(method, path, body, owner_headers())

    def connect(self, preset: str = "propose", categories: list[str] | None = None) -> dict:
        body = {"preset": preset, "confirm": True}
        if categories is not None:
            tools = ["get_context", "search_memory", "propose_memory", "explain_memory"] if preset == "propose" else ["get_context", "search_memory"]
            body.update({"allowed_tools": tools, "allowed_categories": categories})
        status, result = self.owner("POST", "/api/v1/agent-clients/claude-code/connect", body)
        assert status == 200, result
        return secret_from_config(self.home)

    def bridge_command(self, entry: dict) -> dict:
        """The connect entry, run through a shim that lets the Windows-only bridge start on Linux."""
        env = dict(entry["env"])
        env["PYTHONPATH"] = os.pathsep.join([str(EXTLIB), str(SERVER)])
        return {"command": sys.executable, "args": [str(HERE / "bridge_shim.py")], "env": env}
