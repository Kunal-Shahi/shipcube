"""Client for the control-plane API (services/api/openapi.yaml).

The MCP server holds no state of its own. Every tool is one API call (plus polling for
long-running operations), made over HTTP when SHIPCUBE_API_URL is set, or in-process through
httpx's ASGI transport when the MCP server and API run as one service. Both paths go through
the same FastAPI routes, so auth, validation and scopes behave identically.
"""

from __future__ import annotations

import os
from typing import Any

import httpx


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"{code} ({status}): {message}")
        self.status, self.code = status, code


class Api:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    @classmethod
    def from_env(cls) -> Api:
        url = os.environ.get("SHIPCUBE_API_URL")
        if url:
            return cls(httpx.AsyncClient(base_url=url, timeout=30))
        from shipcube_api.app import app  # in-process: same routes, no network hop

        return cls(httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api.internal", timeout=30))

    async def call(self, method: str, path: str, *, token: str | None, body: Any = None,
                   query: dict[str, Any] | None = None, idempotency_key: str | None = None) -> Any:
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        params = {k: v for k, v in (query or {}).items() if v is not None}
        res = await self._client.request(method, path, json=body, params=params, headers=headers)
        data = res.json() if res.content else None
        if res.is_error:
            err = (data or {}).get("error", {})
            raise ApiError(res.status_code, err.get("code", "unknown"), err.get("message", res.reason_phrase))
        return data

    async def introspect(self, token: str) -> bool:
        """True if the API recognises the token. Scope checks stay in the API."""
        try:
            await self.call("GET", "/v1/me", token=token)
        except ApiError as e:
            return e.status != 401
        return True
