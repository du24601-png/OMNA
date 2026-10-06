"""L5: a real Claude Code CLI connected through the real stdio bridge.

Usage: python run_real_client.py <scenario> <model> <grant: preset|all> [import]
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import harness
from harness import ALL_CATEGORIES, SAMPLE, LiveServer

PROMPT_TEXT = (
    "帮我把下面这段自我介绍里值得长期记住的信息存到知我里（之后我会在知我里确认）。\n\n" + SAMPLE
)
PROMPT_IMPORT = "我刚在知我里导入了一段自我介绍，但我没配置提取模型。请你帮我把它提取成待确认的记忆。"
TOOLS = ["get_context", "search_memory", "propose_memory", "explain_memory"]


def main() -> None:
    scenario, model, grant = sys.argv[1], sys.argv[2], sys.argv[3]
    use_import = len(sys.argv) > 4 and sys.argv[4] == "import"
    server = LiveServer().start()
    try:
        if grant == "preset":
            entry = server.connect("propose")
        else:
            entry = server.connect("propose", categories=ALL_CATEGORIES)
        _, agents = server.owner("GET", "/api/v1/agents")
        granted = {"tools": agents["agents"][0]["allowed_tools"], "categories": agents["agents"][0]["allowed_categories"]}
        if use_import:
            status, job = server.owner("POST", "/api/v1/imports", {"kind": "paste", "text": SAMPLE})
            assert status == 200, job
        workdir = Path(tempfile.mkdtemp(prefix="ae-cli-"))
        config = workdir / "mcp.json"
        command = server.bridge_command(entry)
        config.write_text(json.dumps({"mcpServers": {"omna": {"type": "stdio", **command}}}, ensure_ascii=False), encoding="utf-8")
        allowed = ",".join(f"mcp__omna__{name}" for name in TOOLS)
        args = [
            "claude", "-p", PROMPT_IMPORT if use_import else PROMPT_TEXT,
            "--model", model,
            "--mcp-config", str(config), "--strict-mcp-config",
            "--allowedTools", allowed,
            "--disallowedTools", "Bash,Edit,Write,Read,Glob,Grep,WebFetch,WebSearch,Task,NotebookEdit",
            "--output-format", "stream-json", "--verbose",
            "--max-turns", "40",
        ]
        started = time.time()
        proc = subprocess.run(args, cwd=workdir, capture_output=True, text=True, timeout=600)
        elapsed = round(time.time() - started, 1)
        calls, results, final = [], {}, None
        for line in proc.stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "assistant":
                for block in event["message"].get("content", []):
                    if block.get("type") == "tool_use":
                        calls.append({"id": block["id"], "name": block["name"], "input": block["input"]})
            if event.get("type") == "user":
                for block in event["message"].get("content", []) if isinstance(event["message"].get("content"), list) else []:
                    if block.get("type") == "tool_result":
                        content = block.get("content")
                        if isinstance(content, list):
                            content = "".join(item.get("text", "") for item in content if isinstance(item, dict))
                        results[block["tool_use_id"]] = {"is_error": bool(block.get("is_error")), "text": str(content)[:400]}
            if event.get("type") == "result":
                final = event
        for call in calls:
            call["result"] = results.get(call["id"])
        _, proposals = server.owner("GET", "/api/v1/proposals?status=pending")
        _, events = server.owner("GET", "/api/v1/access-events")
        stored = proposals["proposals"]
        propose_calls = [c for c in calls if c["name"].endswith("propose_memory")]
        propose_ok = [c for c in propose_calls if c["result"] and '"status":"pending"' in c["result"]["text"].replace(" ", "")]
        request_ids = [c["input"].get("request_id") for c in propose_calls]
        out = {
            "scenario": scenario,
            "model": model,
            "grant": granted,
            "import_first": use_import,
            "elapsed_s": elapsed,
            "exit_code": proc.returncode,
            "num_turns": final and final.get("num_turns"),
            "cost_usd": final and final.get("total_cost_usd"),
            "final_reply": final and final.get("result"),
            "tool_calls": len(calls),
            "propose_calls": len(propose_calls),
            "propose_succeeded": len(propose_ok),
            "propose_failed": len(propose_calls) - len(propose_ok),
            "distinct_request_ids": len(set(request_ids)),
            "request_ids_sample": request_ids[:5],
            "stored_pending": len(stored),
            "stored_categories": sorted(p["payload"]["category"] for p in stored),
            "stored": [{"content": p["payload"]["content"], "category": p["payload"]["category"], "kind": p["payload"]["kind"],
                        "evidence": p["evidence"].get("text"), "evidence_in_source": (p["evidence"].get("text") or "") in SAMPLE,
                        "verification": p["evidence"].get("verification")} for p in stored],
            "audit_events": len(events["events"]),
            "audit_rejected": sum(1 for e in events["events"] if e.get("outcome") == "rejected"),
            "calls": calls,
            "stderr_tail": proc.stderr[-800:],
        }
        harness.record(f"L5_{scenario}", out)
        summary = {k: out[k] for k in ("scenario", "model", "propose_calls", "propose_succeeded", "propose_failed", "stored_pending", "stored_categories", "audit_events", "audit_rejected", "num_turns", "elapsed_s")}
        print(json.dumps(summary, ensure_ascii=False))
        print("FINAL:", (out["final_reply"] or "")[:1500])
    finally:
        server.stop()


if __name__ == "__main__":
    main()
