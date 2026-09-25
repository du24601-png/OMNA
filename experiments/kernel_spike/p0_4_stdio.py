"""P0.4 minimal stdio bridge. Exposes search_memory and nothing else.

This is an experiment entry, not the product Gateway. It reuses the
memory-search adapter and a copy of the P0.3 synthetic library.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

SPIKE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = SPIKE_ROOT.parents[1]
sys.path.insert(0, str(REPO_ROOT / "server"))

from zhiwo.adapters.memory_search import (
    RECALL_CANDIDATES,
    SearchRejected,
    check_search_arguments,
    load_catalog,
    search_published,
)

READER = "zhiwo:p04-reader"
MODEL = "BAAI/bge-small-zh-v1.5"
DROPPED_ENV = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "OPENROUTER_BASE_URL",
    "HERMES_HOME",
    "MNEMOSYNE_HOME",
    "MNEMOSYNE_DB_PATH",
    "MNEMOSYNE_EMBEDDING_API_KEY",
    "MNEMOSYNE_EMBEDDING_API_URL",
    "MNEMOSYNE_LLM_API_KEY",
    "MNEMOSYNE_LLM_BASE_URL",
    "MNEMOSYNE_SYNC_REMOTE",
    "MNEMOSYNE_SYNC_KEY",
    "MNEMOSYNE_NO_EMBEDDINGS",
    "MNEMOSYNE_EMBEDDINGS_OFF",
    "MNEMOSYNE_SKIP_EMBEDDINGS",
)
HIDDEN_METHODS = (
    "prompts/list",
    "prompts/get",
    "resources/list",
    "resources/read",
    "resources/templates/list",
    "resources/subscribe",
    "resources/unsubscribe",
    "subscriptions/listen",
    "completion/complete",
    "logging/setLevel",
)


class _Redact(logging.Filter):
    def __init__(self, secret: str) -> None:
        super().__init__()
        self._secret = secret

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._secret:
            return True
        message = record.getMessage()
        if self._secret in message:
            record.msg = message.replace(self._secret, "[REDACTED]")
            record.args = ()
        return True


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _prepare_local_env(data_dir: Path, cache_dir: Path) -> None:
    for key in DROPPED_ENV:
        os.environ.pop(key, None)
    os.environ.update(
        {
            "MNEMOSYNE_DATA_DIR": str(data_dir),
            "MNEMOSYNE_PERSONA_FILE": str(data_dir / "persona.md"),
            "MNEMOSYNE_FASTEMBED_CACHE_DIR": str(cache_dir),
            "MNEMOSYNE_EMBEDDING_MODEL": MODEL,
            "MNEMOSYNE_EMBEDDINGS_VIA_API": "0",
            "MNEMOSYNE_LLM_ENABLED": "0",
            "MNEMOSYNE_HOST_LLM_ENABLED": "0",
            "MNEMOSYNE_FORCE_LOCAL": "1",
            "MNEMOSYNE_SLEEP_MODEL_REFRESH_ENABLED": "false",
            "MNEMOSYNE_WM_TTL_HOURS": "1000000",
            "MNEMOSYNE_WM_MAX_ITEMS": "1000000",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_HUB_DISABLE_PROGRESS_BARS": "1",
            "TQDM_DISABLE": "1",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        }
    )


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("BLOCKED: P0.4 requires native Windows")
    data_dir = Path(os.environ["ZHIWO_DATA_DIR"])
    control_path = Path(os.environ["ZHIWO_CONTROL_PATH"])
    agent_path = Path(os.environ["ZHIWO_AGENT_FILE"])
    access_path = Path(os.environ["ZHIWO_ACCESS_LOG"])
    server_log_path = Path(os.environ["ZHIWO_SERVER_LOG"])
    cache_dir = Path(os.environ["MNEMOSYNE_FASTEMBED_CACHE_DIR"])
    secret = os.environ.pop("ZHIWO_AGENT_CREDENTIAL", "")
    presented = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    agent = json.loads(agent_path.read_text(encoding="utf-8"))
    expected = str(agent.get("credential_sha256") or "")
    try:
        authenticated = bool(expected) and hmac.compare_digest(presented, expected)
    except ValueError:
        authenticated = False
    agent_id = str(agent.get("agent_id") or "") if authenticated else None
    access_path.parent.mkdir(parents=True, exist_ok=True)
    server_log_path.parent.mkdir(parents=True, exist_ok=True)
    redact = _Redact(secret)
    write_lock = threading.Lock()

    def append_event(event: dict) -> None:
        event["time"] = _utc()
        line = json.dumps(event, ensure_ascii=False)
        if secret and secret in line:
            line = line.replace(secret, "[REDACTED]")
        with write_lock:
            with access_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()

    _prepare_local_env(data_dir, cache_dir)
    state: dict = {"mem": None, "catalog": None, "error": None}
    state_lock = threading.Lock()

    def open_reader():
        with state_lock:
            if state["mem"] is not None or state["error"] is not None:
                return
            try:
                if not cache_dir.exists():
                    raise RuntimeError("local embedding cache is missing")
                held = sys.stdout
                sys.stdout = sys.stderr
                try:
                    from mnemosyne import Mnemosyne

                    state["catalog"] = load_catalog(data_dir / "mnemosyne.db", control_path)
                    state["mem"] = Mnemosyne(
                        session_id=READER,
                        db_path=data_dir / "mnemosyne.db",
                    )
                finally:
                    sys.stdout = held
            except Exception as exc:
                state["error"] = f"{type(exc).__name__}"
                append_event(
                    {
                        "event": "reader_failed",
                        "error_type": type(exc).__name__,
                        "delivery": "failed",
                    }
                )

    import mcp_types
    from mcp.server.mcpserver import MCPServer

    server = MCPServer(
        name="zhiwo",
        version="0.0.0-p0.4",
        instructions="实验入口。只有只读工具 search_memory。",
        log_level="WARNING",
    )

    @server.tool(
        name="search_memory",
        description="搜索已确认的个人记忆。只读。query 为中文问题，limit 为 1 到 20，默认 10。",
        annotations=mcp_types.ToolAnnotations(
            read_only_hint=True,
            destructive_hint=False,
            open_world_hint=False,
        ),
        structured_output=False,
    )
    def search_memory(query: str, limit: int = 10, categories: list[str] | None = None) -> str:
        request_id = str(uuid.uuid4())
        if not authenticated:
            payload = {
                "request_id": request_id,
                "error": {
                    "code": "UNAUTHENTICATED",
                    "message": "credential rejected",
                    "retryable": False,
                },
            }
            return _finish(payload, "unauthenticated", None)
        try:
            query, limit, _allowed = check_search_arguments(query, limit, categories)
            open_reader()
            if state["error"] is not None or state["mem"] is None:
                payload = {
                    "request_id": request_id,
                    "error": {
                        "code": "MODEL_UNAVAILABLE",
                        "message": "local reader is unavailable",
                        "retryable": True,
                    },
                }
                return _finish(payload, "model_unavailable", None)
            held = sys.stdout
            sys.stdout = sys.stderr
            try:
                raw = state["mem"].recall(query, top_k=RECALL_CANDIDATES)
            finally:
                sys.stdout = held
            if isinstance(raw, dict):
                raw = raw.get("results") or []
            found = search_published(
                list(raw),
                state["catalog"],
                query,
                limit,
                categories,
            )
            payload = {"request_id": request_id, **found}
            return _finish(payload, "ok" if found["items"] else "empty", agent_id)
        except SearchRejected as exc:
            payload = {
                "request_id": request_id,
                "error": {"code": exc.code, "message": exc.message, "retryable": False},
            }
            return _finish(payload, exc.code.lower(), agent_id)
        except Exception:
            payload = {
                "request_id": request_id,
                "error": {
                    "code": "KERNEL_UNAVAILABLE",
                    "message": "recall failed",
                    "retryable": True,
                },
            }
            return _finish(payload, "kernel_unavailable", agent_id)

    def _finish(payload: dict, outcome: str, resolved_agent: str | None) -> str:
        encoded = json.dumps(payload, ensure_ascii=False)
        event = {
            "event": "tool_result",
            "tool": "search_memory",
            "agent_id": resolved_agent,
            "outcome": outcome,
            "delivery": "sent",
            "request_id": payload.get("request_id"),
            "item_ids": [item["id"] for item in payload.get("items") or []],
            "payload": encoded,
        }
        try:
            append_event(event)
        except Exception:
            if "items" in payload:
                denied = {
                    "request_id": payload.get("request_id"),
                    "error": {
                        "code": "AUDIT_UNAVAILABLE",
                        "message": "access log failed",
                        "retryable": True,
                    },
                }
                return json.dumps(denied, ensure_ascii=False)
        return encoded

    low = server._lowlevel_server
    for method in HIDDEN_METHODS:
        low._request_handlers.pop(method, None)

    class _Observe:
        async def __call__(self, ctx, call_next):
            method = getattr(ctx, "method", None)
            try:
                result = await call_next(ctx)
            except Exception:
                append_event({"event": "mcp_error", "method": method})
                raise
            if method == "initialize":
                append_event({"event": "initialized", "method": method})
            elif method == "tools/list":
                listed = result[0] if isinstance(result, tuple) else result
                if isinstance(listed, dict):
                    dumped = listed
                elif hasattr(listed, "model_dump"):
                    dumped = listed.model_dump(mode="json")
                else:
                    dumped = {}
                raw_tools = dumped.get("tools") if isinstance(dumped, dict) else None
                names = []
                for item in raw_tools or []:
                    if isinstance(item, dict) and item.get("name"):
                        names.append(item["name"])
                    elif hasattr(item, "name"):
                        names.append(item.name)
                append_event(
                    {
                        "event": "tools_listed",
                        "method": method,
                        "result_type": type(listed).__name__,
                        "result_keys": list(dumped)[:12] if isinstance(dumped, dict) else [],
                        "names": names,
                    }
                )
            elif method == "tools/call":
                append_event({"event": "tools_call", "method": method})
            return result

    low.middleware.append(_Observe())
    root = logging.getLogger()
    root.setLevel(logging.WARNING)
    file_handler = logging.FileHandler(server_log_path, encoding="utf-8")
    file_handler.addFilter(redact)
    root.addHandler(file_handler)
    for handler in root.handlers:
        handler.addFilter(redact)

    capabilities = low.get_capabilities()
    append_event(
        {
            "event": "started",
            "pid": os.getpid(),
            "registered_tools": sorted(server._tool_manager._tools),
            "authenticated": authenticated,
            "agent_id": agent_id,
            "capabilities": {
                "tools": capabilities.tools is not None,
                "resources": capabilities.resources is not None,
                "prompts": capabilities.prompts is not None,
            },
        }
    )
    server.run("stdio")


if __name__ == "__main__":
    main()
