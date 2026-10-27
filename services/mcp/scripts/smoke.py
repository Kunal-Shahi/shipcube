"""Smoke test against a running server: list the tools, run the app flow, an approval round trip
and the data tools.

    SHIPCUBE_TOKEN=sc_pat_... SHIPCUBE_CONSOLE_TOKEN=sc_ses_... uv run python scripts/smoke.py

SHIPCUBE_CONSOLE_TOKEN stands in for a human in the console: the script uses it to approve, the
way the console's approval page would. Agents (PATs) cannot approve.
"""

import asyncio
import json
import os

import httpx
import httpx2
from mcp.client.client import Client
from mcp.client.streamable_http import streamable_http_client

URL = os.environ.get("SHIPCUBE_MCP_URL", "http://localhost:8080/mcp")
API = URL.rsplit("/mcp", 1)[0]
TOKEN = os.environ.get("SHIPCUBE_TOKEN", "")
CONSOLE = os.environ.get("SHIPCUBE_CONSOLE_TOKEN", "")
# A ~20-file, ~1 MB demo app sent inline.
INLINE = {"kind": "inline", "files": [{"path": f"app/module_{i}.py", "content": "# demo\n" + "x = 1\n" * 8_000}
                                      for i in range(20)]}


def show(name: str, args: dict, out: dict) -> None:
    shown = {k: (v if k != "source" else f"<{v['kind']}, {len(v.get('files', []))} files>") for k, v in args.items()}
    print(f"> {name} {json.dumps(shown)}")
    print(f"  {json.dumps(out)[:420]}")


async def main() -> None:
    http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {TOKEN}"}, timeout=httpx2.Timeout(30, read=90))
    console = httpx.Client(base_url=API, headers={"Authorization": f"Bearer {CONSOLE}"})
    async with Client(streamable_http_client(URL, http_client=http)) as client:
        tools = (await client.list_tools()).tools
        print(f"tools ({len(tools)}): {', '.join(t.name for t in tools)}\n")

        async def progress(p: float, total: float | None, message: str | None) -> None:
            print(f"  progress {p:g}/{total:g}: {message}")

        async def call(name: str, args: dict | None = None) -> dict:
            res = await client.call_tool(name, args or {}, progress_callback=progress)
            out = res.structured_content if not res.is_error else {"ERROR": [c.text for c in res.content]}
            show(name, args or {}, out or {})
            return out or {}

        def approve(op: dict) -> None:
            if op["status"] != "awaiting_approval":  # e.g. a retry returned the already-approved operation
                print(f"  [console] {op['id']} is {op['status']}; nothing to approve")
                return
            r = console.post(f"/v1/operations/{op['id']}/approve", json={"comment": "smoke test"})
            print(f"  [console] approve {op['id']} -> {r.json()['status']}")

        await call("whoami")
        est = await call("estimate_load", {"source": INLINE, "wait_s": 15})
        print(f"  explained_by={est['operation']['result']['explained_by']}")
        dep = await call("deploy_app", {"app": "olist-tracker", "source": INLINE, "idempotency_key": "smoke-1",
                                        "estimate_id": est["operation"]["result"]["estimate_id"], "wait_s": 3})
        if not dep["operation"]["done"]:
            await call("operation", {"id": dep["operation"]["id"], "wait_s": 20})

        acc = await call("set_access", {"app": "olist-tracker",
                                        "add": [{"principal": "user:teammate@example.com"}, {"principal": "domain:example.com"}]})
        approve(acc["operation"])
        await call("operation", {"id": acc["operation"]["id"], "wait_s": 5})

        caps = await call("set_caps", {"app": "olist-tracker", "caps": {"max_instances": 4}})
        await call("set_caps", {"app": "olist-tracker", "caps": {"max_instances": 1}})
        await call("operation", {"id": caps["operation"]["id"], "action": "cancel"})

        linked = await call("deploy_app", {"app": "olist-tracker", "source": INLINE, "target": "linked-1", "wait_s": 0})
        approve(linked["operation"])
        await call("operation", {"id": linked["operation"]["id"], "wait_s": 20})

        await call("inspect_app", {"app": "olist-tracker", "include": ["status", "metrics"]})
        await call("data_list_cubes")
        await call("data_query", {"query": {"measures": ["orders.count"], "dimensions": ["states.region"], "limit": 3}})
        await call("data_propose_migration", {"description": "Average order value for the tracker",
                                              "ops": [{"op": "add_measure", "name": "order_items.aov",
                                                       "sql": "1.0 * {revenue} / nullif({count}, 0)"}]})
        await call("delete_app", {"app": "olist-tracker", "confirm": True})


asyncio.run(main())
