"""In-process dispatch for the four tool names.

`forward()` calls the service functions in this process. The standalone
stdio process is `zhiwo.gateway.stdio_bridge`. It carries the agent
credential to the existing HTTP service and does not open the database or
the Kernel.
"""

from __future__ import annotations

from zhiwo.api.errors import ApiError
from zhiwo.services.agent_tools import explain_memory, get_context, propose_memory, search_memory

_ARGUMENTS = {
    "get_context": {"task", "max_items", "request_id"},
    "search_memory": {"query", "categories", "limit", "request_id"},
    "propose_memory": {"request_id", "change", "evidence"},
    "explain_memory": {"id", "request_id"},
}


def forward(tool: str, arguments: dict, *, db_path, kernel, credential: str) -> dict:
    if not isinstance(arguments, dict):
        raise ApiError(400, "VALIDATION_ERROR", "tool arguments are invalid")
    allowed = _ARGUMENTS.get(tool)
    if allowed is None:
        raise ApiError(400, "VALIDATION_ERROR", "tool is not available")
    if set(arguments) - allowed:
        raise ApiError(400, "VALIDATION_ERROR", "tool arguments are not supported")
    if tool == "get_context":
        return get_context(
            db_path,
            kernel,
            credential,
            arguments.get("task"),
            max_items=arguments.get("max_items", 5),
            request_id=arguments.get("request_id"),
        )
    if tool == "search_memory":
        return search_memory(
            db_path,
            kernel,
            credential,
            arguments.get("query"),
            categories=arguments.get("categories"),
            limit=arguments.get("limit", 10),
            request_id=arguments.get("request_id"),
        )
    if tool == "propose_memory":
        return propose_memory(
            db_path,
            kernel,
            credential,
            arguments.get("request_id"),
            arguments.get("change"),
            arguments.get("evidence"),
        )
    return explain_memory(
        db_path,
        kernel,
        credential,
        arguments.get("id"),
        request_id=arguments.get("request_id"),
    )
