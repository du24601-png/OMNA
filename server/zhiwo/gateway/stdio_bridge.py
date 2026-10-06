"""Stdio MCP bridge for one agent credential.

This process speaks MCP on stdin/stdout and calls the local service over
HTTP. It does not open the control database or the Kernel. The credential
is sent only as the agent bearer token.

Tool arguments are flat and typed so a model can read the allowed values
from the schema. Request ids are not part of the tool surface; the service
creates them. A service error is returned as an MCP tool error whose text is
the service's JSON error payload.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import urllib.error
import urllib.request
from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import Field


_TOOLS = ("get_context", "search_memory", "propose_memory", "explain_memory")
Category = Literal["identity", "goal", "preference", "project", "event", "other"]
_CATEGORY_HELP = "identity 身份；goal 目标；preference 偏好与习惯；project 项目；event 发生过的事；other 其他"
_INSTRUCTIONS = (
    "知我是用户本机的个人记忆库。读取只会返回用户已确认并允许你看的记忆。"
    "用户让你把内容记进知我时：每条记忆只写一件事，逐条调用 propose_memory；"
    "只提交描述用户本人的内容：身份、长期偏好、目标、正在做的项目、发生过的事；"
    "不要提交只对某个代码库成立的规则、命令、文件路径、给 AI 的操作步骤，也不要提交你自己的推测；"
    "category 只能从 identity、goal、preference、project、event、other 里选；"
    "evidence 逐字摘自用户给你的原文，不要改写标点；不要推算日期，也不要补充原文没有的信息。"
    "提交的都是待确认建议，用户在知我里确认后才生效。"
    "全部提交后，如实告诉用户成功几条、失败几条，并说明「已提议，等你在 OMNA 里确认」；工具报错时按错误里写的字段和取值改正后再试。"
    "回答里用到了读到的记忆时，在回复末尾单独加一行来源，例如「已参考你在 OMNA 的偏好 2 条、项目 1 条」；没用到就不加。"
    "错误码是 SHARING_PAUSED 时，是用户暂停了共享：如实告诉用户，不要反复重试。"
)
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


def _checked(raw: str) -> str:
    """Return a success payload; turn a service error into an MCP tool error."""
    from mcp.server.mcpserver.exceptions import ToolError

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        raise ToolError(raw)
    return raw


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
        name="omna",
        version="0.2.0",
        instructions=_INSTRUCTIONS,
        log_level="WARNING",
    )
    read_only = mcp_types.ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)

    @server.tool(
        name="get_context",
        description="获取与当前任务相关、用户已确认并允许你读取的记忆。",
        annotations=read_only,
        structured_output=False,
    )
    def get_context(
        task: Annotated[str, Field(description="用一句话描述当前任务")],
        max_items: Annotated[int, Field(ge=1, le=10, description="最多返回几条，1 到 10")] = 5,
    ) -> str:
        return _checked(_post(origin, secret, "get_context", {"task": task, "max_items": max_items}))

    @server.tool(
        name="search_memory",
        description="搜索用户已确认并允许你读取的记忆。结果里的 id 和 revision 可用于提议修改。",
        annotations=read_only,
        structured_output=False,
    )
    def search_memory(
        query: Annotated[str, Field(description="要找的内容，一句话")],
        limit: Annotated[int, Field(ge=1, le=20, description="最多返回几条，1 到 20")] = 10,
        categories: Annotated[list[Category] | None, Field(description="只在这些类别里找；不填则不限。" + _CATEGORY_HELP)] = None,
    ) -> str:
        return _checked(_post(origin, secret, "search_memory", {"query": query, "limit": limit, "categories": categories}))

    @server.tool(
        name="propose_memory",
        description=(
            "提出一条待确认记忆，一次一条。不会直接写入正式库，用户在知我里确认后才生效。"
            "新增时不填 target_id；修改已有记忆时填 search_memory 返回的 id 和 revision。"
            "同一条重复提交会返回第一次的结果，不会多出一条。"
        ),
        annotations=mcp_types.ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False),
        structured_output=False,
    )
    def propose_memory(
        content: Annotated[str, Field(min_length=1, max_length=2000, description="记忆内容，一句话只写一件事")],
        category: Annotated[Category, Field(description=_CATEGORY_HELP)],
        evidence: Annotated[str, Field(min_length=1, max_length=2000, description="用户原话里支撑这条记忆的一段，逐字照抄")],
        kind: Annotated[Literal["fact", "event"], Field(description="fact 长期成立的事实；event 某次发生的事")] = "fact",
        scope: Annotated[str | None, Field(description="适用场景，可不填")] = None,
        target_id: Annotated[str | None, Field(description="修改已有记忆时填它的 id")] = None,
        base_revision: Annotated[int | None, Field(ge=1, description="修改已有记忆时填它的 revision")] = None,
        source_ref: Annotated[str | None, Field(description="证据所在来源的 id；没有就不填")] = None,
    ) -> str:
        change: dict = {"type": "update" if target_id else "add", "content": content, "kind": kind, "category": category}
        if scope:
            change["scope"] = scope
        if target_id:
            change["target_id"] = target_id
            change["base_revision"] = base_revision
        evidence_body: dict = {"text": evidence}
        if source_ref:
            evidence_body["source_ref"] = source_ref
        return _checked(_post(origin, secret, "propose_memory", {"change": change, "evidence": evidence_body}))

    @server.tool(
        name="explain_memory",
        description="查看一条你可读记忆的已审核证据片段。无权和不存在的返回相同。",
        annotations=read_only,
        structured_output=False,
    )
    def explain_memory(id: Annotated[str, Field(description="search_memory 或 get_context 返回的记忆 id")]) -> str:
        return _checked(_post(origin, secret, "explain_memory", {"id": id}))

    low = server._lowlevel_server
    for method in _HIDDEN:
        low._request_handlers.pop(method, None)
    server.run("stdio")


if __name__ == "__main__":
    main()
