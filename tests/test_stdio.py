"""End-to-end over a real stdio subprocess, exactly how Claude Desktop / Cursor launch the server."""

from __future__ import annotations

import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .conftest import tool_json


async def test_serve_twin_over_stdio():
    params = StdioServerParameters(command=sys.executable, args=["-m", "toolproof.cli", "serve", "--twin"])
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        init = await session.initialize()
        assert init.serverInfo.name == "toolproof-slack"
        tools = (await session.list_tools()).tools
        assert len(tools) == 7
        res = await session.call_tool("slack_find_users", {"query": "diego"})
        assert tool_json(res)["matches"][0]["mention"] == "<@U03DIEGO>"
