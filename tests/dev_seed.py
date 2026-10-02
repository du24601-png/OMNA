"""Synthetic data for checking the desktop shell by hand. Development only.

Talks to a development OMNA started with its own port and user-data folder,
never the installed one. The owner credential is read from that folder.

    python tests/dev_seed.py seed  --port 8785 --user-data <folder>   # memories, 3 agents, reads, 3 suggestions
    python tests/dev_seed.py read  --port 8785 --user-data <folder>   # one agent read  -> tray turns green
    python tests/dev_seed.py propose --port 8785 --user-data <folder> # one new suggestion -> badge + notification
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

MEMORIES = [
    ("project", "在做本地优先的笔记应用「纸鸢」，计划下个月开放内测"),
    ("goal", "在学 Rust，打算用它重写「纸鸢」的同步模块"),
    ("preference", "先给结论，再讲原因，不需要客套"),
    ("preference", "中文回答，技术名词保留英文"),
    ("preference", "包管理用 pnpm，不用 npm"),
    ("identity", "独立开发者，一个人运营一家小公司"),
]
AGENTS = ["Claude Code", "OpenCode", "WorkBuddy"]
STATE = "dev-seed.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["seed", "read", "propose"])
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--user-data", type=Path, required=True)
    args = parser.parse_args()
    if args.port == 8765:
        print("refusing to touch port 8765: that is the installed OMNA", file=sys.stderr)
        return 2
    owner = (args.user_data / "owner.credential").read_text(encoding="utf-8").strip()
    origin = f"http://127.0.0.1:{args.port}"
    state_file = args.user_data / STATE

    def call(method, path, body=None, token=owner, key=True):
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        if key:
            headers["Idempotency-Key"] = str(uuid.uuid4())
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(origin + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.loads(response.read() or b"null")
        except urllib.error.HTTPError as exc:
            print(method, path, exc.code, exc.read().decode("utf-8", "replace"), file=sys.stderr)
            raise

    if args.action == "seed":
        for category, content in MEMORIES:
            call("POST", "/api/v1/memories", {"content": content, "kind": "fact", "category": category, "scope": None, "share_enabled": True})
        secrets = {}
        for name in AGENTS:
            created = call("POST", "/api/v1/agents", {"name": name})
            call("PATCH", f"/api/v1/agents/{created['id']}", {
                "allowed_tools": ["get_context", "search_memory", "propose_memory", "explain_memory"],
                "allowed_categories": ["preference", "project", "goal"],
                "propose_categories": ["identity", "goal", "preference", "project", "event", "other"],
            })
            secrets[name] = created["credential"]
        state_file.write_text(json.dumps(secrets, ensure_ascii=False), encoding="utf-8")
        for name, query in [("Claude Code", "代码风格"), ("OpenCode", "纸鸢")]:
            call("POST", "/api/v1/agent/tools/search_memory", {"query": query}, token=secrets[name], key=False)
        for name, content in [("Claude Code", "测试框架用 Vitest，不用 Jest"), ("WorkBuddy", "周报习惯在周五下午写")]:
            call("POST", "/api/v1/agent/tools/propose_memory", {"change": {"type": "add", "content": content, "kind": "fact", "category": "preference"}, "evidence": {"text": content}}, token=secrets[name], key=False)
        print("seeded")
        return 0

    secrets = json.loads(state_file.read_text(encoding="utf-8"))
    if args.action == "read":
        call("POST", "/api/v1/agent/tools/search_memory", {"query": "回答风格"}, token=secrets["Claude Code"], key=False)
        print("one read by Claude Code")
    else:
        content = f"开会前先发议程（{uuid.uuid4().hex[:4]}）"
        call("POST", "/api/v1/agent/tools/propose_memory", {"change": {"type": "add", "content": content, "kind": "fact", "category": "preference"}, "evidence": {"text": content}}, token=secrets["OpenCode"], key=False)
        print("one suggestion by OpenCode:", content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
