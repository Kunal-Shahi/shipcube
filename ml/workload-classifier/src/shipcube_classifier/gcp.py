"""Minimal authenticated GCP REST client shared by the model registry and the profiler.

Every call has a timeout. Idempotent calls (GET, and anything the caller marks
idempotent) are retried with exponential backoff and jitter on 429, 5xx and network
errors. Failures surface as GcpError with the HTTP status and a short message; response
bodies are truncated and never include credentials.

Credentials come from Application Default Credentials (the service account on Cloud
Run, or `gcloud auth application-default login` locally).
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any

log = logging.getLogger("shipcube.gcp")

RETRYABLE = {408, 429, 500, 502, 503, 504}
SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


class GcpError(Exception):
    def __init__(self, status: int | None, message: str, *, method: str = "", url: str = ""):
        super().__init__(f"{method} {url} -> {status}: {message}")
        self.status, self.method, self.url = status, method, url

    @property
    def not_found(self) -> bool:
        return self.status == 404

    @property
    def conflict(self) -> bool:
        return self.status == 409


class GcpClient:
    def __init__(self, session: Any = None, *, timeout_s: float = 30.0, max_attempts: int = 4,
                 backoff_s: float = 0.5):
        if session is None:
            import google.auth
            from google.auth.transport.requests import AuthorizedSession
            creds, _ = google.auth.default(scopes=SCOPES)
            session = AuthorizedSession(creds)
        self.session = session
        self.timeout_s, self.max_attempts, self.backoff_s = timeout_s, max_attempts, backoff_s

    def request(self, method: str, url: str, *, json: Any = None, data: bytes | None = None,
                params: dict | None = None, headers: dict | None = None, idempotent: bool | None = None,
                ok: tuple[int, ...] = (200,), raw: bool = False, timeout_s: float | None = None) -> Any:
        idempotent = method in ("GET", "HEAD", "DELETE") if idempotent is None else idempotent
        attempts = self.max_attempts if idempotent else 1
        last: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                r = self.session.request(method, url, json=json, data=data, params=params, headers=headers,
                                         timeout=timeout_s or self.timeout_s)
            except Exception as e:  # requests' ConnectionError / Timeout
                last = GcpError(None, f"{type(e).__name__}: {str(e)[:200]}", method=method, url=url)
            else:
                if r.status_code in ok:
                    return r.content if raw else (r.json() if r.content else {})
                last = GcpError(r.status_code, r.text[:300], method=method, url=url)
                if r.status_code not in RETRYABLE:
                    raise last
            if attempt < attempts:
                delay = self.backoff_s * 2 ** (attempt - 1) * (1 + random.random() * 0.25)
                log.warning("gcp retry", extra={"fields": {"event": "gcp.retry", "method": method, "url": url,
                                                           "attempt": attempt, "delay_s": round(delay, 2),
                                                           "error": str(last)[:200]}})
                time.sleep(delay)
        assert last is not None
        raise last

    def get(self, url: str, **kw) -> Any:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw) -> Any:
        return self.request("POST", url, **kw)

    def delete(self, url: str, **kw) -> Any:
        return self.request("DELETE", url, ok=(200, 204, 404), **kw)

    def wait_operation(self, url: str, *, timeout_s: float, poll_s: float = 2.0) -> dict:
        """Poll a long-running operation (Cloud Build / Cloud Run style) until done."""
        end = time.monotonic() + timeout_s
        while True:
            op = self.get(url)
            if op.get("done"):
                if "error" in op:
                    raise GcpError(op["error"].get("code"), op["error"].get("message", "")[:300], method="OP", url=url)
                return op
            if time.monotonic() > end:
                raise GcpError(None, f"operation not done after {timeout_s:.0f}s", method="OP", url=url)
            time.sleep(poll_s)
