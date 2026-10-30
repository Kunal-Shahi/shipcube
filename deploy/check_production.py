#!/usr/bin/env python3
"""Deploy-time guard (#29): no production manifest or deploy script switches on anything local.

Fails on:
  - any *DEV_MODE* variable set to a true value
  - SHIPCUBE_BACKEND, SHIPCUBE_PROFILER or SHIPCUBE_MONITOR other than gcp, SHIPCUBE_SECRET_STORE=local,
    SHIPCUBE_EMBEDDED_WORKER=1, SHIPCUBE_ESTIMATOR=dev, SHIPCUBE_ENV=local
  - a URL pointing at localhost, 127.0.0.1, 0.0.0.0, ::1, host.docker.internal or *.localhost
  - a locally generated secret (sclocal_..., infra/local/localsecrets.py)
  - GATEWAY_ALLOW_PRIVATE_UPSTREAM=1, AUTH_PROVIDER=mock|firebase-emulator, SHIPCUBE_DEV_API_SESSION or
    FIREBASE_AUTH_EMULATOR_HOST set

CI runs it over deploy/services/*.yaml and deploy/*.sh; deploy.sh runs it again on each rendered
manifest, right before `gcloud run services replace`. The services refuse the same things at
startup on Cloud Run (services/api/shipcube_api/settings.py), so this is the earlier of two locks.
Standard library only.

    python3 deploy/check_production.py FILE...
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

TRUE = r"(?:1|true|yes|on)"
RULES = [
    (re.compile(rf"\b[A-Z_]*DEV_MODE[\"']?\s*(?:[:=]|,\s*value:)\s*[\"']?{TRUE}\b", re.I), "dev mode switched on"),
    (re.compile(r"\bSHIPCUBE_BACKEND[\"']?\s*(?:[:=]|,\s*value:)\s*[\"']?(?!gcp\b)[a-z]+", re.I), "SHIPCUBE_BACKEND other than gcp"),
    (re.compile(r"\bSHIPCUBE_SECRET_STORE[\"']?\s*(?:[:=]|,\s*value:)\s*[\"']?local\b", re.I), "local secret store"),
    (re.compile(rf"\bSHIPCUBE_EMBEDDED_WORKER[\"']?\s*(?:[:=]|,\s*value:)\s*[\"']?{TRUE}\b", re.I), "embedded worker"),
    (re.compile(r"\bSHIPCUBE_ESTIMATOR[\"']?\s*(?:[:=]|,\s*value:)\s*[\"']?dev\b", re.I), "dev estimator"),
    (re.compile(r"\bSHIPCUBE_ENV[\"']?\s*(?:[:=]|,\s*value:)\s*[\"']?local\b", re.I), "SHIPCUBE_ENV=local"),
    (re.compile(r"\bSHIPCUBE_PROFILER[\"']?\s*(?:[:=]|,\s*value:)\s*[\"']?(?!gcp\b)[a-z]+", re.I), "SHIPCUBE_PROFILER other than gcp"),
    (re.compile(r"\bSHIPCUBE_MONITOR[\"']?\s*(?:[:=]|,\s*value:)\s*[\"']?(?!gcp\b)[a-z]+", re.I), "SHIPCUBE_MONITOR other than gcp"),
    (re.compile(r"\b[a-z][a-z0-9+.-]*://(?:[^/\s\"'@]*@)?(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]|host\.docker\.internal|[a-z0-9.-]+\.localhost)\b", re.I),
     "local URL"),
    (re.compile(r"\bsclocal_[A-Za-z0-9_-]+"), "locally generated secret"),
    # Gateway and console local-only switches (they refuse these on Cloud Run too).
    (re.compile(rf"\bGATEWAY_ALLOW_PRIVATE_UPSTREAM[\"']?\s*(?:[:=]|,\s*value:)\s*[\"']?{TRUE}\b", re.I), "private upstreams"),
    (re.compile(r"\bAUTH_PROVIDER[\"']?\s*(?:[:=]|,\s*value:)\s*[\"']?(?:mock|firebase-emulator)\b", re.I), "mock or emulator sign-in"),
    (re.compile(r"\b(?:SHIPCUBE_DEV_API_SESSION|FIREBASE_AUTH_EMULATOR_HOST)[\"']?\s*(?:[:=]|,\s*value:)\s*[\"']?\S", re.I),
     "local console session or auth emulator"),
]


def check(path: Path) -> list[str]:
    out = []
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        for rx, what in RULES:
            if rx.search(line):
                out.append(f"{path}:{n}: {what}: {line.strip()[:160]}")
    return out


def main(argv: list[str]) -> None:
    if not argv:
        sys.exit(__doc__)
    problems = [p for f in argv for p in check(Path(f))]
    for p in problems:
        print(f"FAIL {p}")
    if problems:
        sys.exit(1)
    print(f"production guard: {len(argv)} files clean")


if __name__ == "__main__":
    main(sys.argv[1:])
