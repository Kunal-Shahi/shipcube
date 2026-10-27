import json

import pytest
from mcp.client.client import Client
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from shipcube_api.auth import tokens
from shipcube_api.store import USER_ID, WORKSPACE_ID

from shipcube_mcp.backend import Api
from shipcube_mcp.data import StubDataBackend
from shipcube_mcp.server import build_server

APP_TOOLS = ["whoami", "create_upload", "estimate_load", "deploy_app", "operation", "list_apps", "inspect_app",
             "set_caps", "set_access", "ai_config", "delete_app"]
DATA_TOOLS = ["data_list_cubes", "data_describe_cube", "data_query", "data_search_values", "data_propose_migration"]


@pytest.fixture
def as_agent():
    """Run tool calls as an agent holding a PAT (what the bearer middleware sets in production)."""
    pat = tokens.mint(USER_ID, WORKSPACE_ID, ["apps:read", "apps:deploy", "access:write", "caps:write", "data:read"])
    reset = auth_context_var.set(AuthenticatedUser(AccessToken(token=pat, client_id="pat:test", scopes=[])))
    yield
    auth_context_var.reset(reset)


async def _tools(server):
    async with Client(server) as client:
        return {t.name: t for t in (await client.list_tools()).tools}


async def test_tool_contract():
    tools = await _tools(build_server(Api.from_env(), StubDataBackend()))
    assert list(tools) == APP_TOOLS + DATA_TOOLS
    assert "data_apply_migration" not in tools and "apply_migration" not in tools
    for name in APP_TOOLS:
        assert tools[name].output_schema, f"{name} has no outputSchema"
    assert set(tools["deploy_app"].input_schema["required"]) == {"app", "source"}
    assert tools["delete_app"].annotations.destructive_hint is True
    assert tools["data_query"].annotations.read_only_hint is True
    assert "awaiting_approval" in tools["set_access"].description


async def test_data_tools_run_in_process_with_the_verified_caller(as_agent):
    server = build_server(Api.from_env(), StubDataBackend())
    async with Client(server) as client:
        res = await client.call_tool("data_list_cubes", {})
        assert not res.is_error and res.structured_content["cubes"][0]["name"] == "orders"
        # Proposing needs data:migrate, which this agent's token lacks; the error comes from the API's view of the token.
        denied = await client.call_tool("data_propose_migration", {"description": "add aov", "ops": [{"op": "add_measure"}]})
        assert denied.is_error and "data:migrate" in denied.content[0].text
        missing = await client.call_tool("data_describe_cube", {"cube": "nope"})
        # The SDK prefixes "Error executing tool ...: "; the library's {code, message, hint} follows.
        payload = json.loads(missing.content[0].text.split(": ", 1)[1])
        assert missing.is_error and payload["code"] == "not_found" and payload["hint"]


async def test_merged_app_tools(as_agent):
    async with Client(build_server(Api.from_env(), StubDataBackend())) as client:
        inspect = (await client.call_tool("inspect_app", {"app": "regional-revenue", "include": ["status", "metrics"]})).structured_content
        assert inspect["status"]["name"] == "regional-revenue" and inspect["metrics"]["requests"] > 0 and inspect["logs"] is None
        access = (await client.call_tool("set_access", {"app": "regional-revenue",
                                                        "add": [{"principal": "domain:acme.com"}]})).structured_content
        assert access["operation"]["status"] == "awaiting_approval" and access["operation"]["approval_url"]
        op_id = access["operation"]["id"]
        cancelled = (await client.call_tool("operation", {"id": op_id, "action": "cancel"})).structured_content
        assert cancelled["operation"]["status"] == "cancelled"
        deleted = (await client.call_tool("delete_app", {"app": "regional-revenue", "confirm": True})).structured_content
        assert deleted["operation"]["status"] == "awaiting_approval"
        assert deleted["operation"]["approval_url"] in deleted["next"]
