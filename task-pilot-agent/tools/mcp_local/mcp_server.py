# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio

from mcp.server.fastmcp import FastMCP
from mcp.server.session import ServerSession

from config.config import agentSettings
from tools.mcp_local.tool_registrars.all_tools import register_all_tools


old__received_request = ServerSession._received_request


# mcp python-sdk bug
async def _received_request(self, *args, **kwargs):
    try:
        return await old__received_request(self, *args, **kwargs)
    except RuntimeError:
        pass


ServerSession._received_request = _received_request

mcp = FastMCP(
    name="mcp-local-tools",
    host=agentSettings.mcp.mcp_local.host,
    port=agentSettings.mcp.mcp_local.port,
    # stateless_http=True,
    json_response=False,
)
register_all_tools(mcp)


async def mcp_run_async(transport: str = "streamable-http"):
    await asyncio.to_thread(mcp.run, transport=transport)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
