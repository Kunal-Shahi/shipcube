"""Local stand-in for IAP in front of apps deployed by the local backend (deploy/local.py).

    python -m shipcube_api.local_iap        # listens on SHIPCUBE_LOCAL_IAP_PORT (8088)

An app is reached at http://<service>.localhost:8088 (browsers and curl resolve *.localhost to
loopback). Like IAP on Cloud Run, every request needs a signed-in caller who is on the app's access
list (`user:` or `domain:` principals, as set_access applied them); anything else is 401/403, and a
soft-deleted app is 404. The caller proves who they are with a ShipCube token (console session or
PAT) in `Authorization: Bearer`, or in the host-only cookie that /_iap/login sets; the proxy asks
the API's /v1/me who that is. The app receives IAP's identity headers and never sees the token.
Concurrency and request timeout come from the app's caps.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from starlette.routing import Route

from .settings import Settings, settings

log = logging.getLogger(__name__)
COOKIE = "__shipcube_iap"
IDENTITY_TTL_S = 60
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding",
       "upgrade", "host", "content-length"}

Identity = dict[str, str]  # {email, user_id}
Resolver = Callable[[str], Awaitable[Identity | None]]


def api_resolver(api_url: str) -> Resolver:
    cache: dict[str, tuple[float, Identity | None]] = {}

    async def resolve(token: str) -> Identity | None:
        hit = cache.get(token)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{api_url}/v1/me", headers={"Authorization": f"Bearer {token}"})
        ident = {"email": r.json()["email"], "user_id": r.json()["user_id"]} if r.status_code == 200 else None
        cache[token] = (time.monotonic() + IDENTITY_TTL_S, ident)
        return ident

    return resolve


def allowed(email: str, principals: list[str]) -> bool:
    email = email.lower()
    for p in principals:
        kind, _, value = p.partition(":")
        if (kind == "user" and value.lower() == email) or (kind == "domain" and email.endswith("@" + value.lower())):
            return True
    return False  # group: principals need Google Groups; not available locally


class LocalIap:
    def __init__(self, s: Settings = settings, resolve: Resolver | None = None,
                 upstream: Callable[[str], str] | None = None, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.s = s
        self.state_dir = Path(s.local_state_dir)
        self.resolve = resolve or api_resolver(os.environ.get("SHIPCUBE_LOCAL_IAP_API_URL", "http://localhost:8080"))
        if upstream is None:
            from .deploy.local import LocalDeployer

            upstream = LocalDeployer(s).upstream  # by network alias in compose, published port on the host
        self.upstream = upstream
        self.client = httpx.AsyncClient(transport=transport, timeout=None, follow_redirects=False)
        self.slots: dict[str, asyncio.Semaphore] = {}
        self.app = Starlette(routes=[
            Route("/_iap/login", self.login, methods=["GET", "POST"]),
            Route("/{path:path}", self.proxy, methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]),
        ])

    async def __call__(self, scope, receive, send) -> None:
        await self.app(scope, receive, send)

    def _state(self, request: Request) -> dict[str, Any] | None:
        service = request.headers.get("host", "").split(":")[0].split(".")[0]
        path = self.state_dir / f"{service}.json"
        if not service or "/" in service or not path.is_file():
            return None
        return json.loads(path.read_text())

    @staticmethod
    def _token(request: Request) -> str | None:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer sc_"):
            return auth[7:].strip()
        return request.cookies.get(COOKIE)

    async def login(self, request: Request) -> Response:
        """Browsers: paste a console session or PAT once; it's kept in a cookie for this app's host only."""
        if request.method == "POST":
            token = (parse_qs((await request.body()).decode()).get("token") or [""])[0].strip()
            if not token.startswith("sc_") or not await self.resolve(token):
                return HTMLResponse("<p>That token was not accepted.</p>", status_code=401)
            resp = RedirectResponse("/", status_code=303)
            resp.set_cookie(COOKIE, token, httponly=True, samesite="lax", max_age=8 * 3600)
            return resp
        host = html.escape(request.headers.get("host", ""))
        return HTMLResponse(f"<h1>Sign in to {host}</h1><form method=post><input name=token size=60 "
                            "placeholder='sc_ses_... or sc_pat_...'><button>Continue</button></form>")

    async def proxy(self, request: Request) -> Response:
        st = self._state(request)
        if st is None or st.get("ingress") != "all" or not st.get("traffic"):
            return JSONResponse({"error": {"code": "not_found", "message": "no such app"}}, status_code=404)
        token = self._token(request)
        ident = await self.resolve(token) if token else None
        if ident is None:
            if "text/html" in request.headers.get("accept", ""):
                return RedirectResponse("/_iap/login", status_code=302)
            return JSONResponse({"error": {"code": "unauthenticated", "message": "sign in: Authorization: Bearer <ShipCube token>"}},
                                status_code=401)
        if not allowed(ident["email"], st.get("access", [])):
            log.info("local iap denied", extra={"service": st["service"], "email": ident["email"]})
            return JSONResponse({"error": {"code": "permission_denied",
                                           "message": f"{ident['email']} does not have access to this app"}}, status_code=403)

        caps = st.get("caps") or {}
        rev = st["traffic"]
        started = time.monotonic()

        def record(status: int) -> None:
            """One JSON line per proxied request: the local monitor's request metrics (packages/monitor
            local.py), like Cloud Run's front end counts requests in production."""
            print(json.dumps({"shipcube_request": True, "service": st["service"], "revision": rev, "status": status,
                              "latency_ms": round((time.monotonic() - started) * 1000, 1), "method": request.method,
                              "time": datetime.now(UTC).isoformat(timespec="milliseconds")}), flush=True)

        slot = self.slots.setdefault(rev, asyncio.Semaphore(int(caps.get("concurrency", 80))))
        timeout = float(caps.get("timeout_s", 60))
        headers = {k: v for k, v in request.headers.items()
                   if k.lower() not in HOP and not k.lower().startswith("x-goog-authenticated-")
                   and not (k.lower() == "authorization" and v.lower().startswith("bearer sc_"))}
        cookies = [c for c in request.headers.get("cookie", "").split(";") if c.strip() and not c.strip().startswith(COOKIE + "=")]
        headers.pop("cookie", None)
        if cookies:
            headers["cookie"] = ";".join(cookies).strip()
        headers["x-goog-authenticated-user-email"] = f"accounts.google.com:{ident['email']}"
        headers["x-goog-authenticated-user-id"] = f"accounts.google.com:{ident['user_id']}"
        headers["x-forwarded-host"] = request.headers.get("host", "")
        try:
            await asyncio.wait_for(slot.acquire(), timeout)
        except TimeoutError:
            record(429)
            return JSONResponse({"error": {"code": "rate_limited", "message": "all instances are busy"}}, status_code=429)
        try:
            url = self.upstream(rev) + request.url.path + (f"?{request.url.query}" if request.url.query else "")
            req = self.client.build_request(request.method, url, headers=headers, content=await request.body(),
                                            timeout=httpx.Timeout(timeout))
            upstream = await self.client.send(req, stream=True)
        except (httpx.HTTPError, OSError) as e:
            slot.release()
            record(503)
            return JSONResponse({"error": {"code": "unavailable", "message": f"app did not answer: {type(e).__name__}: {e}"}},
                                status_code=503)

        async def body():
            try:
                async for chunk in upstream.aiter_bytes():  # decoded, so content-encoding is dropped below
                    yield chunk
            finally:
                await upstream.aclose()
                slot.release()
                record(upstream.status_code)

        out = {k: v for k, v in upstream.headers.items() if k.lower() not in HOP and k.lower() != "content-encoding"}
        return StreamingResponse(body(), status_code=upstream.status_code, headers=out)


def main() -> None:
    import uvicorn

    uvicorn.run(LocalIap(), host=os.environ.get("HOST", "127.0.0.1"), port=settings.local_iap_port, log_level="info")


if __name__ == "__main__":
    main()
