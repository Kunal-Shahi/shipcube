"""End-to-end run through the MCP server against real GCP, with timings per step.

    SHIPCUBE_TOKEN=sc_pat_... uv run python scripts/e2e_gcp.py <app-dir> <app-name> [grantee@example.com]

Steps: create_upload -> PUT the tarball to the signed URL -> estimate_load -> deploy_app ->
inspect_app (live Cloud Run status) -> unauthenticated GET (must be stopped by IAP) ->
set_access add the grantee -> inspect_app.
"""

import asyncio
import io
import json
import os
import sys
import tarfile
import time
from pathlib import Path

import httpx
import httpx2
from mcp.client.client import Client
from mcp.client.streamable_http import streamable_http_client

URL = os.environ.get("SHIPCUBE_MCP_URL", "http://localhost:8080/mcp")
TOKEN = os.environ["SHIPCUBE_TOKEN"]


def tarball(path: Path) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for f in sorted(path.rglob("*")):
            if f.is_file() and not any(p.startswith(".") or p == "__pycache__" for p in f.relative_to(path).parts):
                tar.add(f, arcname=str(f.relative_to(path)))
    return buf.getvalue()


async def main(app_dir: Path, app: str, grantee: str | None) -> None:
    timings: dict[str, float] = {}
    http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {TOKEN}"}, timeout=httpx2.Timeout(30, read=120))
    async with Client(streamable_http_client(URL, http_client=http)) as client:

        async def call(name: str, args: dict) -> dict:
            t0 = time.monotonic()
            res = await client.call_tool(name, args, progress_callback=lambda p, t, m: print(f"    {p:g}/{t:g} {m}") or asyncio.sleep(0))
            timings[name if name not in timings else f"{name} #{sum(k.startswith(name) for k in timings) + 1}"] = round(time.monotonic() - t0, 1)
            if res.is_error:
                raise SystemExit(f"{name} failed: {res.content[0].text}")
            return res.structured_content

        async def follow(op: dict, label: str) -> dict:
            t0 = time.monotonic()
            while not op["done"]:
                op = (await call("operation", {"id": op["id"], "wait_s": 55}))["operation"]
            timings[f"{label} (total)"] = round(time.monotonic() - t0, 1)
            if op["status"] != "succeeded":
                raise SystemExit(f"{label} {op['status']}: {op.get('error')}")
            return op

        print("> create_upload")
        up = await call("create_upload", {})
        data = tarball(app_dir)
        t0 = time.monotonic()
        r = httpx.put(up["url"], content=data, headers=up["headers"], timeout=60)
        timings["PUT tarball"] = round(time.monotonic() - t0, 1)
        print(f"  PUT {len(data)} bytes -> {r.status_code}")
        r.raise_for_status()
        source = {"kind": "upload", "upload_id": up["upload_id"]}

        print("> estimate_load")
        est = (await call("estimate_load", {"source": source, "app": app, "wait_s": 30}))["operation"]
        est = await follow(est, "estimate")
        print(f"  tier={est['result']['tier']} explained_by={est['result']['explained_by']}")

        print("> deploy_app")
        t_deploy = time.monotonic()
        dep = (await call("deploy_app", {"app": app, "source": source, "estimate_id": est["result"]["estimate_id"],
                                         "wait_s": 55}))["operation"]
        dep = await follow(dep, "deploy")
        timings["deploy end to end"] = round(time.monotonic() - t_deploy, 1)
        print(f"  {json.dumps(dep['result'])}")
        url = dep["result"]["url"]

        print("> inspect_app status")
        st = await call("inspect_app", {"app": app, "include": ["status"]})
        print(f"  runtime={json.dumps(st['status']['runtime'])}")

        print("> GET without signing in (IAP should stop it)")
        r = httpx.get(url, follow_redirects=False, timeout=30)
        print(f"  {r.status_code} -> {r.headers.get('location', '')[:120]}")

        if grantee:
            print(f"> set_access add user:{grantee}")
            acc = await call("set_access", {"app": app, "add": [{"principal": f"user:{grantee}"}]})
            print(f"  access={[b['principal'] for b in acc['access']]} pending={acc['operation']}")
            st = await call("inspect_app", {"app": app, "include": ["status"]})
            print(f"  ready={st['status']['runtime']['ready']} iap={st['status']['runtime']['iap_enabled']}")

    print("\nTimings (s):")
    for k, v in timings.items():
        print(f"  {k:28} {v}")
    print(json.dumps({"step_timings_s": dep["result"].get("timings_s")}))


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]), sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None))
