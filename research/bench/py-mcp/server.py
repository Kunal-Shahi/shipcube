"""Minimal MCP server on the official Python SDK (mcp 2.x MCPServer), streamable HTTP, one tool."""
import os

import uvicorn
from mcp.server.mcpserver import MCPServer
from starlette.responses import PlainTextResponse
from starlette.routing import Route

mcp = MCPServer("shipcube-bench")


@mcp.tool()
def estimate_load(repo: str) -> dict:
    """Estimate workload class."""
    return {"class": "bursty_interactive", "tier": "T1"}


app = mcp.streamable_http_app(stateless_http=True, json_response=True)
app.router.routes.append(Route("/healthz", lambda r: PlainTextResponse("ok")))

if __name__ == "__main__":
    uvicorn.run(app, port=int(os.environ.get("PORT", "8080")), log_level="warning")
