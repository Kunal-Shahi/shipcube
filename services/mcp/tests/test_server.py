import sys
import types

from mcp.client.client import Client
from pydantic import BaseModel

from shipcube_mcp.backend import Api
from shipcube_mcp.server import build_server

EXPECTED = [
    "whoami", "create_upload", "estimate_load", "deploy_app", "get_operation", "cancel_operation", "list_apps",
    "get_status", "get_logs", "get_metrics", "set_caps", "grant_access", "revoke_access", "connect_data", "query",
    "ai_config", "delete_app",
]


async def _tools(server):
    async with Client(server) as client:
        return {t.name: t for t in (await client.list_tools()).tools}


async def test_tool_contract():
    tools = await _tools(build_server(Api.from_env()))
    assert list(tools) == EXPECTED
    for t in tools.values():
        assert t.output_schema, f"{t.name} has no outputSchema"
    deploy = tools["deploy_app"].input_schema
    assert set(deploy["required"]) == {"app", "source"}
    assert tools["delete_app"].annotations.destructive_hint is True


async def test_data_layer_plugin_mounts_in_process(monkeypatch):
    """A stand-in for the shipcube-data package: adds list_cubes and replaces query."""
    plugin = types.ModuleType("fake_data_layer")

    class Cubes(BaseModel):
        cubes: list[str]

    def register(host):
        @host.tool(title="List cubes")
        async def list_cubes() -> Cubes:
            """Cubes in the workspace's semantic model."""
            return Cubes(cubes=["orders"])

        @host.tool(title="Query (data layer)")
        async def query(cube: str) -> Cubes:
            """Data-layer query."""
            return Cubes(cubes=[cube])

    plugin.register = register
    monkeypatch.setitem(sys.modules, "fake_data_layer", plugin)
    monkeypatch.setenv("SHIPCUBE_MCP_PLUGINS", "fake_data_layer:register")

    server = build_server(Api.from_env())
    tools = await _tools(server)
    assert "list_cubes" in tools
    assert tools["query"].title == "Query (data layer)"
    async with Client(server) as client:
        res = await client.call_tool("list_cubes", {})
        assert res.structured_content == {"cubes": ["orders"]}
