"""Stdio MCP bridge for one agent credential.

This process speaks MCP on stdin/stdout and calls the local service over
HTTP. It does not open the control database or the Kernel. The credential
is sent only as the agent bearer token.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import urllib.error
import urllib.request
from urllib.parse import urlparse


_TOOLS = ("get_context", "search_memory", "propose_memory", "explain_memory")
_HIDDEN = (
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
        if self._secret and self._secret in record.getMessage():
            record.msg = record.getMessage().replace(self._secret, "[redacted]")
            record.args = ()
        return True


def _origin() -> str:
    raw = os.environ.get("ZHIWO_API_ORIGIN", "").strip()
    parsed = urlparse(raw)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"} or parsed.port is None:
        raise SystemExit("ZHIWO_API_ORIGIN must be http://127.0.0.1:<port>")
    return f"http://{parsed.hostname}:{parsed.port}"


def _credential() -> str:
    secret = os.environ.get("ZHIWO_AGENT_CREDENTIAL", "")
    if not secret:
        raise SystemExit("ZHIWO_AGENT_CREDENTIAL is required")
    return secret


def _post(origin: str, secret: str, tool: str, payload: dict) -> str:
    if tool not in _TOOLS:
        return _error("VALIDATION_ERROR", "tool is not available")
    body = {key: value for key, value in payload.items() if value is not None}
    request = urllib.request.Request(
        f"{origin}/api/v1/agent/tools/{tool}",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
            "X-Zhiwo-Transport": "stdio",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        if secret and secret in raw:
            raw = raw.replace(secret, "[redacted]")
        return raw
    except (urllib.error.URLError, TimeoutError, OSError):
        return _error("KERNEL_UNAVAILABLE", "the local service did not answer", retryable=True)


def _error(code: str, message: str, retryable: bool = False) -> str:
    return json.dumps(
        {"error": {"code": code, "message": message, "retryable": retryable}},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("the stdio bridge requires native Windows")
    origin = _origin()
    secret = _credential()
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    logging.getLogger().addFilter(_Redact(secret))

    import mcp_types
    from mcp.server.mcpserver import MCPServer

    server = MCPServer(
        name="zhiwo",
        version="0.1.0",
        instructions="只使用已注册的四个记忆工具。不要请求资源或提示词。",
        log_level="WARNING",
    )

    @server.tool(
        name="get_context",
        description="获取与任务相关且已获准的记忆。task 为中文任务，max_items 为 1 到 10，默认 5。",
        annotations=mcp_types.ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False),
        structured_output=False,
    )
    def get_context(task: str, max_items: int = 5, request_id: str | None = None) -> str:
        return _post(origin, secret, "get_context", {"task": task, "max_items": max_items, "request_id": request_id})

    @server.tool(
        name="search_memory",
        description="搜索已确认且已获准的个人记忆。query 为中文问题，limit 为 1 到 20，默认 10。",
        annotations=mcp_types.ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False),
        structured_output=False,
    )
    def search_memory(
        query: str,
        limit: int = 10,
        categories: list[str] | None = None,
        request_id: str | None = None,
    ) -> str:
        return _post(
            origin,
            secret,
            "search_memory",
            {"query": query, "limit": limit, "categories": categories, "request_id": request_id},
        )

    @server.tool(
        name="propose_memory",
        description=(
            "提出一条待确认记忆，不会直接写入正式库。"
            "change.type 是 add 或 update；change 含 content、kind、category，"
            "更新时含 target_id 和 base_revision。evidence.text 是证据片段。"
        ),
        annotations=mcp_types.ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False),
        structured_output=False,
    )
    def propose_memory(request_id: str, change: dict, evidence: dict) -> str:
        return _post(
            origin,
            secret,
            "propose_memory",
            {"request_id": request_id, "change": change, "evidence": evidence},
        )

    @server.tool(
        name="explain_memory",
        description="解释一条当前获准记忆的已审核证据片段。id 是记忆 id。无权和不存在的返回相同。",
        annotations=mcp_types.ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False),
        structured_output=False,
    )
    def explain_memory(id: str, request_id: str | None = None) -> str:
        return _post(origin, secret, "explain_memory", {"id": id, "request_id": request_id})

    low = server._lowlevel_server
    for method in _HIDDEN:
        low._request_handlers.pop(method, None)
    server.run("stdio")


if __name__ == "__main__":
    main()
