"""Minimal FastAPI service, and a reverse-proxy hop when UPSTREAM is set.

Run: uvicorn app:app --port $PORT
"""
import os

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, Response

UPSTREAM = os.environ.get("UPSTREAM")
app = FastAPI()

if UPSTREAM:
    import httpx

    client = httpx.AsyncClient(
        base_url=UPSTREAM,
        limits=httpx.Limits(max_connections=512, max_keepalive_connections=512),
    )

    @app.api_route("/{path:path}", methods=["GET", "POST"])
    async def proxy(path: str, request: Request):
        r = await client.request(
            request.method, "/" + path, content=await request.body(),
            headers={k: v for k, v in request.headers.items() if k.lower() != "host"},
        )
        return Response(r.content, status_code=r.status_code,
                        media_type=r.headers.get("content-type"))
else:
    @app.get("/healthz", response_class=PlainTextResponse)
    async def healthz():
        return "ok"

    @app.api_route("/echo", methods=["GET", "POST"])
    async def echo(request: Request):
        return {"ok": True, "path": request.url.path, "tier": "T1"}
