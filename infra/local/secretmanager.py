#!/usr/bin/env python3
"""Secret Manager REST emulator for the local stack. Serves only the call ShipCube makes:

    GET /v1/projects/<any>/secrets/<name>/versions/<latest|N>:access
        -> {"name": ..., "payload": {"data": <base64>}}

Values come from .local/secrets.json (infra/local/localsecrets.py). A missing secret is 404 and an
empty one is 404 too (an unset Muse key must fail loudly, not send ""). Refuses to start on Cloud
Run or with SHIPCUBE_ENV other than `local`, and never logs values. Standard library only.

    python3 infra/local/secretmanager.py [--port 8085]
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import localsecrets as local_secrets

ACCESS = re.compile(r"^/v1/(projects/[^/]+/secrets/([A-Za-z0-9_-]+)/versions/(latest|\d+)):access$")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        m = ACCESS.match(self.path.split("?", 1)[0])
        if not m:
            return self._send(404, {"error": {"code": 404, "status": "NOT_FOUND", "message": "unknown path"}})
        value = local_secrets.load().get(m[2], "")  # re-read: values set after start are picked up
        if not value:
            return self._send(404, {"error": {"code": 404, "status": "NOT_FOUND",
                                              "message": f"secret {m[2]} has no value in .local/secrets.json"}})
        self._send(200, {"name": m[1], "payload": {"data": base64.b64encode(value.encode()).decode()}})

    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args) -> None:  # path and status only, never values
        sys.stderr.write(f"secretmanager {self.command} {self.path.split('?', 1)[0]} {args[1] if len(args) > 1 else ''}\n")


def main() -> None:
    local_secrets.refuse_production()
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else int(os.environ.get("PORT", "8085"))
    if not (local_secrets.LOCAL / "secrets.json").exists():
        sys.exit("no .local/secrets.json: run `python3 infra/local/localsecrets.py init` first")
    print(f"secretmanager emulator on :{port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
