"""Talk to the real stdio bridge with the official MCP client."""

from __future__ import annotations

import json

import anyio
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


async def _run(params: dict, steps):
    server = StdioServerParameters(command=params["command"], args=params["args"], env=params["env"])
    async with stdio_client(server) as streams:
        read, write = streams[0], streams[1]
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            return await steps(session, init)


def run(params: dict, steps):
    return anyio.run(_run, params, steps)


def dump(model) -> dict:
    return json.loads(model.model_dump_json(by_alias=True, exclude_none=True))


def text_of(result) -> str:
    return "".join(getattr(item, "text", "") for item in result.content)
