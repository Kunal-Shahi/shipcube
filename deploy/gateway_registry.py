#!/usr/bin/env python3
"""Write the AI gateway's app registry (secret `gateway-registry`) from the hosted apps on Cloud Run.

Interim, until the API serves GET /internal/gateway/resolve (services/gateway/README.md). Each
ShipCube-managed Cloud Run service in the apps project (label managed-by=shipcube) becomes one entry:
its runtime service account is the principal the gateway sees in the app's Google ID token.

    python3 deploy/gateway_registry.py [--project shipcube-control-ks] [--apps-project shipcube-apps-ks]

Adds a new secret version only when the content changed. Muse (`muse-spark-1.3`) with default caps;
per-app provider config arrives with the API endpoint.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

DEFAULT = {"provider_config": {"provider": "meta", "model": "muse-spark-1.3"},
           "caps": {"requests_per_minute": 60, "ai_tokens_per_day": 200000}}


def gcloud(*args: str, stdin: str | None = None) -> str:
    r = subprocess.run(["gcloud", *args], input=stdin, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"gcloud {' '.join(args[:3])}: {r.stderr.strip()[:300]}")
    return r.stdout


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--project", default="shipcube-control-ks")
    p.add_argument("--apps-project", default="shipcube-apps-ks")
    p.add_argument("--region", default="asia-south1")
    a = p.parse_args()

    services = json.loads(gcloud("run", "services", "list", f"--project={a.apps_project}", f"--region={a.region}",
                                 "--filter=metadata.labels.managed-by=shipcube", "--format=json"))
    apps = []
    for s in services:
        labels = s["metadata"].get("labels", {})
        sa = s["spec"]["template"]["spec"].get("serviceAccountName")
        if not sa:
            continue
        apps.append({"principal": sa, "app_id": s["metadata"]["name"], "app": labels.get("shipcube-app", s["metadata"]["name"]),
                     "workspace_id": labels.get("shipcube-workspace", ""), **DEFAULT})
    body = json.dumps({"apps": sorted(apps, key=lambda x: x["app_id"])}, indent=2, sort_keys=True)

    try:
        current = gcloud("secrets", "versions", "access", "latest", "--secret=gateway-registry", f"--project={a.project}")
    except RuntimeError:
        current = ""
    if current.strip() == body.strip():
        print(f"gateway-registry unchanged ({len(apps)} apps)")
        return
    gcloud("secrets", "versions", "add", "gateway-registry", "--data-file=-", f"--project={a.project}", stdin=body)
    print(f"gateway-registry updated: {len(apps)} apps ({', '.join(x['app'] for x in apps) or 'none'})")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        sys.exit(str(e))
