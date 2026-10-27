"""Smoke test: list the tools and run the core flow against a running server.

    SHIPCUBE_MCP_URL=http://localhost:8080/mcp SHIPCUBE_TOKEN=sc_pat_... uv run python scripts/smoke.py
"""

import asyncio
import json
import os

import httpx2
from mcp.client.client import Client
from mcp.client.streamable_http import streamable_http_client

URL = os.environ.get("SHIPCUBE_MCP_URL", "http://localhost:8080/mcp")
TOKEN = os.environ.get("SHIPCUBE_TOKEN", "")
GIT = {"kind": "git", "url": "https://github.com/example/olist-tracker"}


async def main() -> None:
    http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {TOKEN}"}, timeout=httpx2.Timeout(30, read=90))
    async with Client(streamable_http_client(URL, http_client=http)) as client:
        tools = (await client.list_tools()).tools
        print(f"tools ({len(tools)}): {', '.join(t.name for t in tools)}\n")

        async def progress(p: float, total: float | None, message: str | None) -> None:
            print(f"  progress {p:g}/{total:g}: {message}")

        async def call(name: str, args: dict | None = None) -> dict:
            res = await client.call_tool(name, args or {}, progress_callback=progress)
            print(f"> {name} {json.dumps(args or {})}")
            out = res.structured_content if not res.is_error else {"ERROR": [c.text for c in res.content]}
            print(f"  {json.dumps(out)[:400]}")
            return out or {}

        await call("whoami")
        est = await call("estimate_load", {"source": GIT, "wait_s": 15})
        dep = await call("deploy_app", {
            "app": "olist-tracker", "source": GIT, "idempotency_key": "smoke-1", "wait_s": 3,
            "estimate_id": est["operation"]["result"]["estimate_id"],
        })
        if not dep["operation"]["done"]:
            await call("get_operation", {"id": dep["operation"]["id"], "wait_s": 20})
        await call("grant_access", {"app": "olist-tracker", "principal": "user:teammate@example.com"})
        await call("set_caps", {"app": "olist-tracker", "caps": {"max_instances": 1}})
        await call("get_status", {"app": "olist-tracker"})
        await call("query", {"query": {"measures": ["orders.revenue"], "dimensions": ["orders.region"]}})


asyncio.run(main())
