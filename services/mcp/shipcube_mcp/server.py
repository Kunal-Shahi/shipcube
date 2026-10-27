"""ShipCube MCP server: Streamable HTTP on /mcp, stateless (MCP 2026-07-28; 2025-era clients are
served statelessly too).

By default the MCP server and the control-plane API run as one service (as
docs/research/language-tradeoffs.md recommends): /mcp is the MCP endpoint, /v1/* is the API,
and tools reach the API in-process. Set SHIPCUBE_API_URL to call a separate API over HTTP.
"""

from __future__ import annotations

import os

import uvicorn
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from .backend import Api
from .plugins import ToolHost, load_plugins
from .tools import register_tools

INSTRUCTIONS = (
    "ShipCube deploys and runs internal apps on GCP. Typical flow: create_upload (or a git source) -> estimate_load -> "
    "deploy_app with the estimate_id -> grant_access. Long-running tools return an operation; call get_operation to keep waiting."
)


class ApiTokenVerifier:
    """Phase 1 auth: personal access tokens (sc_pat_<id>_<secret>) minted in the web console.

    The MCP server only checks that the API recognises the token and forwards it; the API checks
    scopes on every call. Phase 2 adds OAuth 2.1 JWTs here (docs/research/networking.md §1.4)."""

    def __init__(self, api: Api) -> None:
        self.api = api

    async def verify_token(self, token: str) -> AccessToken | None:
        if not token.startswith("sc_pat_") or not await self.api.introspect(token):
            return None
        token_id = token.split("_")[2] if token.count("_") >= 3 else "unknown"
        return AccessToken(token=token, client_id=f"pat:{token_id}", scopes=[])


def build_server(api: Api) -> MCPServer:
    server = MCPServer(
        "shipcube",
        version="0.2.0",
        instructions=INSTRUCTIONS,
        token_verifier=ApiTokenVerifier(api),
        # No authorization server yet, so no Protected Resource Metadata is published
        # (resource_server_url=None); issuer_url is required by the SDK but unused in phase 1.
        auth=AuthSettings(issuer_url=os.environ.get("SHIPCUBE_CONSOLE_URL", "https://console.shipcube.dev"),
                          resource_server_url=None),
    )
    register_tools(server, api)
    load_plugins(ToolHost(server, api))
    return server


def build_app() -> ASGIApp:
    api = Api.from_env()
    server = build_server(api)
    hosts = os.environ.get("ALLOWED_HOSTS", "localhost:*,127.0.0.1:*").split(",")
    # The SDK app carries the bearer-auth middleware, so it is kept whole and only sees /mcp.
    mcp_app = server.streamable_http_app(
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            allowed_hosts=hosts,
            allowed_origins=[f"http://{h}" for h in hosts] + [f"https://{h}" for h in hosts],
        ),
    )
    other: ASGIApp = Starlette(routes=[Route("/healthz", lambda _: PlainTextResponse("ok"))])
    if not os.environ.get("SHIPCUBE_API_URL"):
        from shipcube_api.app import app as api_app

        other = api_app  # serves /healthz and /v1/*

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan" or scope.get("path", "").rstrip("/") == "/mcp":
            await mcp_app(scope, receive, send)
        else:
            await other(scope, receive, send)

    return app


def main() -> None:
    host = os.environ.get("HOST", "127.0.0.1")
    if host not in ("127.0.0.1", "localhost") and not os.environ.get("ALLOWED_HOSTS"):
        raise SystemExit("Set ALLOWED_HOSTS when listening on a non-loopback host (DNS-rebinding protection)")
    uvicorn.run(build_app(), host=host, port=int(os.environ.get("PORT", "8080")), log_level="info")


if __name__ == "__main__":
    main()
