from __future__ import annotations

import asyncio


def test_mcp_registry_records_server_status_for_success_and_failure():
    from tools.aggre_mcp_market.models import Protocol, ToolInfo
    from tools.aggre_mcp_market.service.registry import MCPRegistry

    class GoodClient:
        url = "http://good.example.test/mcp"
        authorization = None
        tool_prefix = "good"
        protocol = Protocol.STREAMABLE_HTTP

        def list_tools(self):
            return [
                ToolInfo(
                    full_name="good-search",
                    name="search",
                    description="Search",
                    input_schema={"type": "object"},
                    output_schema={},
                    server_url=self.url,
                    protocol=self.protocol,
                    tool_prefix=self.tool_prefix,
                )
            ]

    class BadClient:
        url = "http://bad.example.test/mcp"
        authorization = "Bearer token"
        tool_prefix = "bad"
        protocol = Protocol.STREAMABLE_HTTP

        def list_tools(self):
            raise RuntimeError("server unavailable")

    registry = MCPRegistry([], start_background=False)
    registry._clients = [GoodClient(), BadClient()]

    registry.refresh(keep_last_on_failure=False)

    assert [tool.full_name for tool in registry.list_tools()] == ["good-search"]
    statuses = {item.tool_prefix: item for item in registry.list_servers()}
    assert statuses["good"].status == "ok"
    assert statuses["good"].tool_count == 1
    assert statuses["bad"].status == "error"
    assert statuses["bad"].authorization_configured is True
    assert "server unavailable" in statuses["bad"].error


def test_mcp_registry_refresh_server_updates_only_matching_snapshot():
    from tools.aggre_mcp_market.models import Protocol, ToolInfo
    from tools.aggre_mcp_market.service.registry import MCPRegistry

    class MutableClient:
        authorization = None
        protocol = Protocol.STREAMABLE_HTTP

        def __init__(self, url, tool_prefix, tool_names):
            self.url = url
            self.tool_prefix = tool_prefix
            self.tool_names = tool_names

        def list_tools(self):
            return [
                ToolInfo(
                    full_name=f"{self.tool_prefix}-{name}",
                    name=name,
                    description=name,
                    input_schema={"type": "object"},
                    output_schema={},
                    server_url=self.url,
                    protocol=self.protocol,
                    tool_prefix=self.tool_prefix,
                )
                for name in self.tool_names
            ]

    alpha = MutableClient("http://alpha.example.test/mcp", "alpha", ["old_search"])
    beta = MutableClient("http://beta.example.test/mcp", "beta", ["beta_search"])
    registry = MCPRegistry([], start_background=False)
    registry._clients = [alpha, beta]

    registry.refresh(keep_last_on_failure=False)
    assert [tool.full_name for tool in registry.list_tools()] == ["alpha-old_search", "beta-beta_search"]

    alpha.tool_names = ["new_search"]
    beta.tool_names = ["new_beta_search"]

    assert registry.refresh_server("alpha") is True
    assert [tool.full_name for tool in registry.list_tools()] == ["beta-beta_search", "alpha-new_search"]
    statuses = {item.tool_prefix: item for item in registry.list_servers()}
    assert statuses["alpha"].status == "ok"
    assert statuses["alpha"].tool_count == 1
    assert registry.refresh_server("missing") is False


def test_mcp_market_servers_and_refresh_api(monkeypatch):
    from tools.aggre_mcp_market import app as market_app
    from tools.aggre_mcp_market.models import MCPServerStatus, Protocol, ToolInfo

    class FakeRegistry:
        refreshed = False

        def refresh(self, keep_last_on_failure=True):
            self.refreshed = keep_last_on_failure

        def list_tools(self):
            return [
                ToolInfo(
                    full_name="mcp_local-web_search",
                    name="web_search",
                    description="Search",
                    input_schema={"type": "object"},
                    output_schema={},
                    server_url="http://mcp.example.test/mcp",
                    protocol=Protocol.STREAMABLE_HTTP,
                    tool_prefix="mcp_local",
                )
            ]

        def list_servers(self):
            return [
                MCPServerStatus(
                    url="http://mcp.example.test/mcp",
                    protocol=Protocol.STREAMABLE_HTTP,
                    tool_prefix="mcp_local",
                    status="ok",
                    tool_count=1,
                    last_checked_at=1780730000.0,
                    duration_ms=12,
                )
            ]

    registry = FakeRegistry()
    monkeypatch.setattr(market_app, "registry", registry)

    servers = market_app.get_servers()
    assert len(servers) == 1
    assert servers[0].tool_prefix == "mcp_local"
    assert servers[0].status == "ok"

    refreshed = asyncio.run(market_app.refresh_tools())
    assert registry.refreshed is True
    assert refreshed["toolCount"] == 1
    assert refreshed["servers"][0]["tool_prefix"] == "mcp_local"
