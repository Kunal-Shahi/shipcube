"""Mount extra tool packages (the data layer's list_cubes, describe_cube, query, propose_migration,
apply_migration, search_values) on the ShipCube MCP server, in-process.

A plugin is a callable ``register(host: ToolHost) -> None``, found through the
``shipcube.mcp_tools`` entry-point group or the ``SHIPCUBE_MCP_PLUGINS`` env var
(comma-separated ``module:function``). The plugin registers tools with ``host.tool(...)``,
which takes the same arguments as ``MCPServer.tool`` and replaces a built-in tool of the same
name (the data layer's ``query`` replaces ours).

Inside a tool, ``await host.caller("data:read")`` returns the verified workspace, user and
scopes, or raises a ToolError if the token lacks the scope. The API stays the only place that
makes authorization decisions; the data layer uses ``workspace_id`` for schema routing and
row-level filters.
"""

from __future__ import annotations

import importlib
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Any

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .backend import Api, ApiError

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CallerInfo:
    user_id: str
    email: str
    workspace_id: str
    token_id: str | None
    scopes: frozenset[str]


class ToolHost:
    def __init__(self, server: MCPServer, api: Api) -> None:
        self.server = server
        self.api = api

    def tool(self, *args: Any, **kwargs: Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            name = kwargs.get("name") or (args[0] if args else None) or fn.__name__
            if name in {t.name for t in self.server._tool_manager.list_tools()}:
                log.info("plugin tool %s replaces the built-in tool", name)
                self.server.remove_tool(name)
            return self.server.tool(*args, **kwargs)(fn)

        return decorator

    async def caller(self, required_scope: str | None = None) -> CallerInfo:
        t = get_access_token()
        try:
            me = await self.api.call("GET", "/v1/me", token=t.token if t else None)
        except ApiError as e:
            raise ToolError(str(e)) from e
        tok = me.get("token") or {}
        info = CallerInfo(me["user_id"], me["email"], me["workspace"]["id"], tok.get("id"), frozenset(tok.get("scopes", [])))
        if required_scope and required_scope not in info.scopes:
            raise ToolError(f"permission_denied (403): token lacks scope {required_scope}")
        return info


def load_plugins(host: ToolHost) -> list[str]:
    loaded: list[str] = []
    specs = [s.strip() for s in os.environ.get("SHIPCUBE_MCP_PLUGINS", "").split(",") if s.strip()]
    targets: list[tuple[str, Callable[[ToolHost], None]]] = []
    for spec in specs:
        module, _, attr = spec.partition(":")
        targets.append((spec, getattr(importlib.import_module(module), attr or "register")))
    for ep in entry_points(group="shipcube.mcp_tools"):
        targets.append((ep.value, ep.load()))
    for name, register in targets:
        register(host)
        loaded.append(name)
    return loaded
