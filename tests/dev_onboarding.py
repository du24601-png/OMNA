"""A throwaway OMNA for checking onboarding and the import card by hand. Development only.

`serve` runs the service in test mode on its own port with an empty library in
a temporary folder, and a fake home folder holding synthetic instruction files.
"Connect" then writes client configs into that fake home, never into your real
Claude Code / OpenCode / ChatGPT settings, and never touches port 8765.
`propose` plays a connected client: it reads that client's credential from the
fake home and submits one suggestion, as the client would after you send it the
organize sentence.

    python tests/dev_onboarding.py serve   --port 8786 --dir <empty folder>
    python tests/dev_onboarding.py propose --port 8786 --dir <same folder> --client claude-code "包管理用 pnpm，不用 npm"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OWNER = "dev-onboarding-owner"
FILES = {
    (".claude", "CLAUDE.md"): """# 关于我

- 独立开发者，主要写 TypeScript 和 Python

## 偏好

- 先给结论，再讲原因，不需要客套
- 包管理用 pnpm，不用 npm
- git 提交信息用中文，动词开头
- git 提交信息用中文，动词开头写
- 测试框架用 Vitest，不用 Jest

## 项目

- 在做本地优先的笔记应用「纸鸢」
- 学 Rust，准备重写同步模块

## 这个仓库

- 运行 `pnpm test` 前先 `pnpm build`
""",
    (".codex", "AGENTS.md"): """# 写给 Codex

## 偏好

- Python 项目用 uv 管理依赖
- SQL 关键字用大写

## 项目

- 在做数据管道重构，月底上线
""",
}
CLIENT_CONFIG = {
    "claude-code": (".claude.json",),
    "opencode": (".config", "opencode", "opencode.json"),
    "codex": (".codex", "config.toml"),
    "zcode": (".zcode", "cli", "config.json"),
    "workbuddy": (".workbuddy", "mcp.json"),
    "claude": ("AppData", "Roaming", "Claude", "claude_desktop_config.json"),
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["serve", "propose"])
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--dir", type=Path, required=True)
    parser.add_argument("--client", default="claude-code", choices=sorted(CLIENT_CONFIG))
    parser.add_argument("--category", default="preference")
    parser.add_argument("content", nargs="?")
    args = parser.parse_args()
    if args.port == 8765:
        print("refusing to use port 8765: that is the installed OMNA", file=sys.stderr)
        return 2
    root = args.dir.resolve()
    home = root / "home"
    if args.action == "serve":
        return serve(args.port, root, home)
    if not args.content:
        print("give the suggestion text", file=sys.stderr)
        return 2
    return propose(args.port, home, args.client, args.content, args.category)


def serve(port: int, root: Path, home: Path) -> int:
    if (root / "data").exists() and any((root / "data").iterdir()):
        print(f"reusing the library in {root / 'data'}")
    for parts, text in FILES.items():
        path = home.joinpath(*parts)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    (home / ".config" / "opencode").mkdir(parents=True, exist_ok=True)
    os.environ.update({
        "ZHIWO_DATA_DIR": str(root / "data"),
        "ZHIWO_OWNER_CREDENTIAL": OWNER,
        "ZHIWO_TEST_MODE": "1",
        "ZHIWO_CLIENT_HOME": str(home),
        "HF_HUB_OFFLINE": "1",
    })
    for key in ("ZHIWO_EXTRACTOR_API_KEY", "ZHIWO_EXTRACTOR_BASE_URL", "ZHIWO_EXTRACTOR_MODEL", "ZHIWO_KERNEL_CONNECT_ONLY"):
        os.environ.pop(key, None)
    os.environ.setdefault("ZHIWO_FASTEMBED_CACHE_DIR", str(REPO / "experiments" / "kernel_spike" / "runs" / "p0_3" / "fastembed-cache"))
    sys.path.insert(0, str(REPO / "server"))
    print(f"owner credential: {OWNER}")
    print(f"fake home (client configs land here): {home}")
    import uvicorn

    uvicorn.run("zhiwo.api.app:app", host="127.0.0.1", port=port, access_log=False)
    return 0


def propose(port: int, home: Path, client: str, content: str, category: str) -> int:
    config = home.joinpath(*CLIENT_CONFIG[client])
    if not config.exists():
        print(f"{client} is not connected yet (no {config})", file=sys.stderr)
        return 2
    found = re.search(r'ZHIWO_AGENT_CREDENTIAL"?\s*[:=]\s*"([^"]+)"', config.read_text(encoding="utf-8"))
    if not found:
        print(f"no OMNA credential in {config}", file=sys.stderr)
        return 2
    body = {"change": {"type": "add", "content": content, "kind": "fact", "category": category}, "evidence": {"text": content}}
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/v1/agent/tools/propose_memory",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {found.group(1)}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            print(json.loads(response.read()))
    except urllib.error.HTTPError as exc:
        print(exc.code, exc.read().decode("utf-8", "replace"), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
