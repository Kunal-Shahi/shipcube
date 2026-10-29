#!/usr/bin/env python3
"""Add the console's host to Identity Platform's authorized domains (Google sign-in refuses other
origins). Idempotent; changes nothing else in the project's Identity Platform config.

    python3 deploy/identity_platform.py --project shipcube-control-ks --add-domain <host>
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request


def call(method: str, url: str, project: str, body: dict | None = None) -> dict:
    token = subprocess.check_output(["gcloud", "auth", "print-access-token"], text=True).strip()
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body else None, headers={
        "Authorization": f"Bearer {token}", "x-goog-user-project": project, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {url}: {e.code} {e.read().decode()[:300]}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--project", required=True)
    p.add_argument("--add-domain", required=True)
    a = p.parse_args()
    url = f"https://identitytoolkit.googleapis.com/admin/v2/projects/{a.project}/config"
    domains = call("GET", url, a.project).get("authorizedDomains", [])
    if a.add_domain in domains:
        print(f"Identity Platform: {a.add_domain} already authorized")
        return
    call("PATCH", url + "?updateMask=authorizedDomains", a.project, {"authorizedDomains": [*domains, a.add_domain]})
    print(f"Identity Platform: authorized {a.add_domain}")


if __name__ == "__main__":
    main()
