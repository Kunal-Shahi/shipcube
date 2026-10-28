"""End-to-end smoke test of the local stack, through the real MCP server over streamable HTTP.

    make smoke            (deploy/local/dev smoke)

Uses the seeded PAT (deploy/local/.state/seed.env) like a coding agent would, and the seeded console
token where a person approves in the console. Calls all 16 tools:
whoami → create_upload (PUT examples/hello-ai) → estimate_load → deploy_app → operation (until done)
→ the app answers on its URL → inspect_app → list_apps → set_caps → set_access → ai_config →
data_list_cubes / describe / query / search_values / propose_migration → delete_app (approved).
Then calls the AI gateway once with the seeded app token: Muse when META_API_KEY is set, the local
Ollama model when LOCAL_LLM=ollama, otherwise that call is skipped.

Exit code 0 only if every step passed or was legitimately skipped.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import sys
import tarfile
import time
import traceback
from pathlib import Path

import httpx
import httpx2
from mcp.client.client import Client
from mcp.client.streamable_http import streamable_http_client

ROOT = Path(__file__).resolve().parents[3]
STATE = ROOT / "deploy/local/.state/seed.env"
EXPECTED_TOOLS = {
    "whoami", "create_upload", "estimate_load", "deploy_app", "operation", "list_apps", "inspect_app",
    "set_caps", "set_access", "ai_config", "delete_app", "data_list_cubes", "data_describe_cube",
    "data_query", "data_search_values", "data_propose_migration",
}
APP = os.environ.get("SMOKE_APP", "hello-local")


def seed() -> dict[str, str]:
    out = {}
    for line in STATE.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k] = v
    return out


def tarball(src: Path) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for p in sorted(src.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                tar.add(p, arcname=str(p.relative_to(src)))
    return buf.getvalue()


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def add(self, step: str, status: str, detail: str = "") -> None:
        self.rows.append((step, status, detail))
        mark = {"PASS": "ok  ", "FAIL": "FAIL", "SKIP": "skip"}[status]
        print(f"[{mark}] {step}{': ' + detail if detail else ''}", flush=True)

    @property
    def failed(self) -> bool:
        return any(s == "FAIL" for _, s, _ in self.rows)


async def run(r: Report) -> None:
    s = seed()
    mcp_url = os.environ.get("SHIPCUBE_MCP_URL", "http://localhost:8080/mcp")
    api_url = mcp_url.rsplit("/mcp", 1)[0]
    gateway = os.environ.get("SHIPCUBE_GATEWAY_URL", "http://localhost:8090")
    console = httpx.Client(base_url=api_url, headers={"Authorization": f"Bearer {s['SHIPCUBE_DEV_CONSOLE_TOKEN']}"},
                           timeout=30)
    http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {s['SHIPCUBE_PAT']}"},
                              timeout=httpx2.Timeout(30, read=180))

    async with Client(streamable_http_client(mcp_url, http_client=http)) as client:
        async def call(name: str, args: dict | None = None) -> dict:
            res = await client.call_tool(name, args or {})
            if res.is_error:
                raise RuntimeError(f"{name}: " + " ".join(getattr(c, "text", "") for c in res.content)[:600])
            return res.structured_content or {}

        async def step(name: str, coro):
            try:
                out = await coro
                return out
            except Exception as e:  # noqa: BLE001 — every failure is reported, then the run continues
                r.add(name, "FAIL", str(e)[:600])
                if os.environ.get("SMOKE_DEBUG"):
                    traceback.print_exc()
                return None

        async def finish(op: dict, label: str, timeout_s: int = 900) -> dict:
            """Follow an operation with the `operation` tool until it's terminal."""
            deadline = time.monotonic() + timeout_s
            while not op["done"] and op["status"] != "awaiting_approval":
                if time.monotonic() > deadline:
                    raise TimeoutError(f"{label} still {op['status']} after {timeout_s}s")
                op = (await call("operation", {"id": op["id"], "wait_s": 20}))["operation"]
            return op

        # 1. tools
        tools = {t.name for t in (await client.list_tools()).tools}
        missing, extra = EXPECTED_TOOLS - tools, tools - EXPECTED_TOOLS
        r.add(f"list tools ({len(tools)})", "PASS" if not missing else "FAIL",
              f"missing {sorted(missing)}" if missing else (f"extra {sorted(extra)}" if extra else ""))

        me = await step("whoami", call("whoami"))
        if me:
            r.add("whoami", "PASS", f"{me.get('email')} in {me['workspace']['name']}")

        # 2a. load estimator on an inline app: the profiler must measure it in the Docker sandbox
        inline = {"kind": "inline", "files": [
            {"path": "app.py", "content": "from flask import Flask\napp = Flask(__name__)\n\n"
             "@app.get('/')\ndef index():\n    return 'hello'\n"},
            {"path": "requirements.txt", "content": "flask>=3.0\n"}]}
        est = await step("estimate_load (inline, sandbox)",
                         call("estimate_load", {"source": inline, "expected_users": 25, "wait_s": 30}))
        if est:
            op = await step("estimate_load (operation)", finish(est["operation"], "inline estimate", 600))
            if op and op["status"] == "succeeded":
                res = op["result"]
                meas = (res.get("profile") or {}).get("measurements") or {}
                ok = meas.get("runner") == "docker" and meas.get("mode") == "measured"
                r.add("estimate_load (inline, sandbox)", "PASS" if ok else "FAIL",
                      f"runner={meas.get('runner')} mode={meas.get('mode')} → {res.get('workload_class')} "
                      f"{res.get('tier')}, explained_by={res.get('explained_by')}")
            elif op:
                r.add("estimate_load (inline, sandbox)", "FAIL", f"{op['status']}: {op.get('error')}")

        # 2. source upload
        source = None
        up = await step("create_upload", call("create_upload"))
        if up:
            body = tarball(ROOT / "examples/hello-ai")
            put = httpx.request(up["method"], up["url"], headers=up.get("headers", {}), content=body, timeout=60)
            if put.status_code < 300:
                source = {"kind": "upload", "upload_id": up["upload_id"]}
                r.add("create_upload + PUT", "PASS", f"{len(body)} bytes")
            else:
                r.add("create_upload + PUT", "FAIL", f"PUT returned {put.status_code}: {put.text[:200]}")

        # 3. estimate + deploy
        estimate_id = None
        if source:
            est = await step("estimate_load", call("estimate_load", {"source": source, "app": APP, "wait_s": 30}))
            if est:
                op = await step("estimate_load (operation)", finish(est["operation"], "estimate"))
                if op and op["status"] == "succeeded":
                    res = op["result"]
                    estimate_id = res.get("estimate_id")
                    r.add("estimate_load", "PASS", f"{res.get('workload_class')} → {res.get('tier')}, "
                          f"explained_by={res.get('explained_by')}")
                elif op:
                    r.add("estimate_load", "FAIL", f"{op['status']}: {op.get('error')}")

        url = None
        if source:
            args = {"app": APP, "source": source, "idempotency_key": f"smoke-{int(time.time())}", "wait_s": 30}
            if estimate_id:
                args["estimate_id"] = estimate_id
            dep = await step("deploy_app", call("deploy_app", args))
            if dep:
                op = await step("operation (deploy)", finish(dep["operation"], "deploy"))
                if op and op["status"] == "succeeded":
                    url = (op.get("result") or {}).get("url")
                    r.add("deploy_app + operation", "PASS", f"running at {url}")
                elif op:
                    r.add("deploy_app + operation", "FAIL", f"{op['status']}: {op.get('error')}")

        if url:
            try:
                deadline, resp = time.monotonic() + 60, None
                while time.monotonic() < deadline:
                    try:
                        resp = httpx.get(url, timeout=10)
                        if resp.status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(2)
                ok = resp is not None and resp.status_code == 200
                r.add("app answers on localhost", "PASS" if ok else "FAIL",
                      f"{url} → {resp.status_code if resp is not None else 'no response'}")
            except Exception as e:  # noqa: BLE001
                r.add("app answers on localhost", "FAIL", str(e))

        # 4. inspect, list, caps, access, ai
        if url:
            ins = await step("inspect_app", call("inspect_app", {"app": APP, "include": ["status", "logs", "metrics"]}))
            if ins:
                m = ins.get("metrics") or {}
                r.add("inspect_app", "PASS", f"status={ins['status']['status']}, {len(ins['logs']['entries'])} log lines, "
                      f"instances={m.get('instances')}, memory={m.get('memory_utilization')}")
        apps = await step("list_apps", call("list_apps"))
        if apps is not None:
            r.add("list_apps", "PASS", f"{len(apps.get('apps', []))} apps")
        if url:
            caps = await step("set_caps", call("set_caps", {"app": APP, "caps": {"max_instances": 1}}))
            if caps:
                r.add("set_caps", "PASS", f"max_instances={caps['caps'].get('max_instances')}")
            acc = await step("set_access", call("set_access", {"app": APP, "add": [{"principal": "user:reviewer@example.com"}]}))
            if acc:
                r.add("set_access", "PASS", f"{len(acc.get('access', []))} principals")
            ai = await step("ai_config", call("ai_config", {"app": APP, "provider": "meta", "model": "muse-spark-1.3"}))
            if ai:
                r.add("ai_config", "PASS", json.dumps(ai)[:120])

        # 5. data tools
        cubes = await step("data_list_cubes", call("data_list_cubes"))
        if cubes:
            names = [c["name"] for c in cubes.get("cubes", [])]
            r.add("data_list_cubes", "PASS", ", ".join(names[:6]))
            cube = "orders" if "orders" in names else names[0]
            desc = await step("data_describe_cube", call("data_describe_cube", {"cube": cube}))
            if desc:
                meas = [m["path"] for m in desc.get("measures", [])]
                dims = [d["path"] for d in desc.get("dimensions", [])]
                r.add("data_describe_cube", "PASS", f"{cube}: {len(meas)} measures, {len(dims)} dimensions")
                if meas:
                    q = {"measures": [meas[0]], "limit": 5, **({"dimensions": [dims[0]]} if dims else {})}
                    res = await step("data_query", call("data_query", {"query": q}))
                    if res is not None:
                        r.add("data_query", "PASS", f"{len(res.get('rows', []))} rows")
                if dims:
                    sv = await step("data_search_values", call("data_search_values", {"dimension": dims[0], "limit": 5}))
                    if sv is not None:
                        r.add("data_search_values", "PASS", dims[0])
            prop = await step("data_propose_migration", call("data_propose_migration", {
                "description": "Smoke test: add a count measure (proposed only; agents can't apply)",
                "ops": [{"op": "add_measure", "name": f"{cube}.smoke_count_{int(time.time())}", "agg": "count"}]}))
            if prop:
                r.add("data_propose_migration", "PASS", f"status={prop.get('status')}; a person applies it in the console")

        # 6. delete (needs a person's approval in the console)
        if url:
            d = await step("delete_app", call("delete_app", {"app": APP, "confirm": True}))
            if d:
                op = d["operation"]
                if op["status"] == "awaiting_approval":
                    a = console.post(f"/v1/operations/{op['id']}/approve", json={"comment": "smoke test"})
                    if a.status_code != 200:
                        r.add("delete_app", "FAIL", f"console approve returned {a.status_code}: {a.text[:200]}")
                    else:
                        op = await step("operation (delete)", finish(a.json(), "delete"))
                if op and op.get("status") == "succeeded":
                    r.add("delete_app (approved in console)", "PASS", "soft-deleted, restorable for 7 days")
                elif op:
                    r.add("delete_app (approved in console)", "FAIL", f"{op['status']}: {op.get('error')}")

    # 7. gateway
    tok = s.get("SHIPCUBE_GATEWAY_APP_TOKEN", "")
    try:
        g = httpx.Client(base_url=gateway, timeout=150)
        if g.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]}).status_code != 401:
            r.add("gateway rejects unauthenticated calls", "FAIL")
        else:
            r.add("gateway rejects unauthenticated calls", "PASS")
        resp = g.post("/v1/chat/completions", headers={"Authorization": f"Bearer {tok}"},
                      json={"model": "default", "max_tokens": 1500,
                            "messages": [{"role": "user", "content": "Reply with the single word: pong"}]})
        provider = resp.headers.get("X-ShipCube-Provider", "")
        if provider == "mock":
            r.add("gateway model call", "SKIP", "no META_API_KEY and LOCAL_LLM unset (see .env)")
        elif resp.status_code == 200:
            text = resp.json()["choices"][0]["message"]["content"]
            r.add("gateway model call", "PASS", f"{provider}/{resp.headers.get('X-ShipCube-Model')}: {text[:60]!r}")
        else:
            r.add("gateway model call", "FAIL", f"{resp.status_code} {resp.text[:200]}")
    except httpx.TransportError as e:
        r.add("gateway", "FAIL", f"unreachable: {e}")


def main() -> None:
    r = Report()
    try:
        asyncio.run(run(r))
    except Exception as e:  # noqa: BLE001
        r.add("smoke run", "FAIL", f"{type(e).__name__}: {e}")
        traceback.print_exc()
    passed = sum(1 for _, s, _ in r.rows if s == "PASS")
    print(f"\n{passed} passed, {sum(1 for _, s, _ in r.rows if s == 'FAIL')} failed, "
          f"{sum(1 for _, s, _ in r.rows if s == 'SKIP')} skipped")
    sys.exit(1 if r.failed else 0)


if __name__ == "__main__":
    main()
