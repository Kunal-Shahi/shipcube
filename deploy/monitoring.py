#!/usr/bin/env python3
"""Uptime checks and alert policies for ShipCube's own services (Cloud Monitoring REST API).

    python3 deploy/monitoring.py --email you@example.com            # create or update everything
    python3 deploy/monitoring.py --email you@example.com --dry-run  # print what would change

Idempotent: objects are matched by display name and updated in place. Standard library only; the
access token comes from `gcloud auth print-access-token`.

Creates:
- An email notification channel.
- Uptime checks, every 5 minutes from 3 regions: console /sign-in, MCP /healthz, gateway /healthz.
- Alerts:
  - an uptime check failing from 2+ regions for 5 minutes (per service)
  - a 5xx ratio above 5% for 5 minutes (per service)
  - console or MCP p95 latency above 3 s for 10 minutes (the gateway streams, so its latency is not alerted on)
  - Cloud SQL, if shipcube-pg exists: down, CPU above 80% for 15 minutes, disk above 80%

Cost: uptime checks are free for the first 1M executions/month (SKU CA14-D3DE-E67F); this uses about
39k. Alert policies have no billing SKU today.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

API = "https://monitoring.googleapis.com/v3"
PREFIX = "ShipCube"
SERVICES = {  # Cloud Run service -> uptime path
    "shipcube-console": "/sign-in",
    "shipcube-mcp": "/healthz",
    "shipcube-gateway": "/healthz",
}


class Client:
    def __init__(self, project: str, dry_run: bool) -> None:
        self.project, self.dry_run = project, dry_run
        self.token = subprocess.check_output(["gcloud", "auth", "print-access-token"], text=True).strip()

    def call(self, method: str, path: str, body: dict | None = None, query: dict | None = None) -> dict:
        url = f"{API}/{path}" + (f"?{urllib.parse.urlencode(query)}" if query else "")
        req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body else None,
                                     headers={"Authorization": f"Bearer {self.token}",
                                              "Content-Type": "application/json",
                                              "x-goog-user-project": self.project})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            sys.exit(f"{method} {path}: {e.code} {e.read().decode()[:500]}")

    def list(self, path: str, key: str) -> list[dict]:
        out, token = [], None
        while True:
            body = self.call("GET", path, query={"pageToken": token} if token else None)
            out += body.get(key, [])
            if not (token := body.get("nextPageToken")):
                return out

    def upsert(self, collection: str, key: str, obj: dict, update_mask: str) -> str:
        """Create, or patch the object with the same displayName. Returns its resource name."""
        path = f"projects/{self.project}/{collection}"
        existing = next((o for o in self.list(path, key) if o.get("displayName") == obj["displayName"]), None)
        verb = "update" if existing else "create"
        kind = {"notificationChannels": "channel", "uptimeCheckConfigs": "uptime check",
                "alertPolicies": "alert policy"}[collection]
        print(f"{verb:6} {kind}: {obj['displayName']}")
        if self.dry_run:
            return existing["name"] if existing else f"{path}/DRY_RUN"
        if existing:
            return self.call("PATCH", existing["name"], obj, {"updateMask": update_mask})["name"]
        return self.call("POST", path, obj)["name"]


def run_url_host(service: str, project_number: str, region: str) -> str:
    return f"{service}-{project_number}.{region}.run.app"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--project", default="shipcube-control-ks")
    p.add_argument("--region", default="asia-south1")
    p.add_argument("--email", required=True, help="where alerts go")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    number = subprocess.check_output(
        ["gcloud", "projects", "describe", a.project, "--format=value(projectNumber)"], text=True).strip()
    c = Client(a.project, a.dry_run)

    channel = c.upsert("notificationChannels", "notificationChannels", {
        "displayName": f"{PREFIX} on-call email",
        "type": "email",
        "labels": {"email_address": a.email},
    }, "labels,displayName")

    for svc, path in SERVICES.items():
        host = run_url_host(svc, number, a.region)
        check = c.upsert("uptimeCheckConfigs", "uptimeCheckConfigs", {
            "displayName": f"{PREFIX} {svc}",
            "monitoredResource": {"type": "uptime_url", "labels": {"project_id": a.project, "host": host}},
            "httpCheck": {"path": path, "port": 443, "useSsl": True, "validateSsl": True,
                          "acceptedResponseStatusCodes": [{"statusClass": "STATUS_CLASS_2XX"}]},
            "period": "300s",
            "timeout": "10s",
            "selectedRegions": ["USA", "EUROPE", "ASIA_PACIFIC"],
        }, "displayName,httpCheck,period,timeout,selectedRegions")
        check_id = check.rsplit("/", 1)[-1]
        c.upsert("alertPolicies", "alertPolicies", {
            "displayName": f"{PREFIX} {svc} is down",
            "combiner": "OR",
            "conditions": [{
                "displayName": f"Uptime check failing for {svc}",
                "conditionThreshold": {
                    "filter": ('metric.type="monitoring.googleapis.com/uptime_check/check_passed" '
                               f'AND resource.type="uptime_url" AND metric.label.check_id="{check_id}"'),
                    "aggregations": [{"alignmentPeriod": "300s", "perSeriesAligner": "ALIGN_NEXT_OLDER",
                                      "crossSeriesReducer": "REDUCE_COUNT_FALSE",
                                      "groupByFields": ["resource.label.host"]}],
                    "comparison": "COMPARISON_GT", "thresholdValue": 1, "duration": "300s",
                    "trigger": {"count": 1},
                },
            }],
            "notificationChannels": [channel],
            "documentation": {"mimeType": "text/markdown", "content": (
                f"`{svc}` failed its uptime check (`{path}`) from at least two regions.\n\n"
                f"Runbook: `deploy/README.md` → Operate. First steps: "
                f"`gcloud run services describe {svc} --region {a.region}` and the service's error logs.")},
        }, "displayName,conditions,notificationChannels,documentation,combiner")

    services_filter = " OR ".join(f'resource.label.service_name="{s}"' for s in SERVICES)
    c.upsert("alertPolicies", "alertPolicies", {
        "displayName": f"{PREFIX} 5xx ratio above 5%",
        "combiner": "OR",
        "conditions": [{
            "displayName": "5xx / all requests > 5% for 5 min",
            "conditionThreshold": {
                "filter": ('metric.type="run.googleapis.com/request_count" AND resource.type="cloud_run_revision" '
                           f'AND metric.label.response_code_class="5xx" AND ({services_filter})'),
                "denominatorFilter": ('metric.type="run.googleapis.com/request_count" '
                                      f'AND resource.type="cloud_run_revision" AND ({services_filter})'),
                "aggregations": [{"alignmentPeriod": "300s", "perSeriesAligner": "ALIGN_RATE",
                                  "crossSeriesReducer": "REDUCE_SUM",
                                  "groupByFields": ["resource.label.service_name"]}],
                "denominatorAggregations": [{"alignmentPeriod": "300s", "perSeriesAligner": "ALIGN_RATE",
                                             "crossSeriesReducer": "REDUCE_SUM",
                                             "groupByFields": ["resource.label.service_name"]}],
                "comparison": "COMPARISON_GT", "thresholdValue": 0.05, "duration": "300s",
                "trigger": {"count": 1},
            },
        }],
        "notificationChannels": [channel],
        "documentation": {"mimeType": "text/markdown", "content": (
            "More than 5% of requests to a ShipCube service returned 5xx for 5 minutes. Check the service's "
            "logs at severity>=ERROR; for the gateway, `jsonPayload.message=\"chat\"` lines show the provider, "
            "status and attempts.")},
    }, "displayName,conditions,notificationChannels,documentation,combiner")

    latency_filter = ('metric.type="run.googleapis.com/request_latencies" AND resource.type="cloud_run_revision" '
                      'AND (resource.label.service_name="shipcube-console" OR resource.label.service_name="shipcube-mcp")')
    c.upsert("alertPolicies", "alertPolicies", {
        "displayName": f"{PREFIX} p95 latency above 3 s",
        "combiner": "OR",
        "conditions": [{
            "displayName": "p95 request latency > 3000 ms for 10 min",
            "conditionThreshold": {
                "filter": latency_filter,
                "aggregations": [{"alignmentPeriod": "300s", "perSeriesAligner": "ALIGN_DELTA",
                                  "crossSeriesReducer": "REDUCE_PERCENTILE_95",
                                  "groupByFields": ["resource.label.service_name"]}],
                "comparison": "COMPARISON_GT", "thresholdValue": 3000, "duration": "600s",
                "trigger": {"count": 1},
            },
        }],
        "notificationChannels": [channel],
        "documentation": {"mimeType": "text/markdown", "content": (
            "The console or MCP server is slow. Cold starts alone shouldn't hold p95 above 3 s for 10 minutes; "
            "check instance count against max instances (2), and for MCP the latency of Cloud APIs it calls.")},
    }, "displayName,conditions,notificationChannels,documentation,combiner")

    instance = f"{a.project}:shipcube-pg"
    has_sql = subprocess.run(["gcloud", "sql", "instances", "describe", "shipcube-pg", f"--project={a.project}"],
                             capture_output=True).returncode == 0
    if has_sql:
        db = f'resource.type="cloudsql_database" AND resource.label.database_id="{instance}"'
        conditions = [
            ("Cloud SQL down", "cloudsql.googleapis.com/database/up", "COMPARISON_LT", 1, "300s", "ALIGN_MIN"),
            ("Cloud SQL CPU > 80%", "cloudsql.googleapis.com/database/cpu/utilization", "COMPARISON_GT", 0.8,
             "900s", "ALIGN_MEAN"),
            ("Cloud SQL disk > 80%", "cloudsql.googleapis.com/database/disk/utilization", "COMPARISON_GT", 0.8,
             "300s", "ALIGN_MAX"),
        ]
        for name, metric, cmp, value, duration, aligner in conditions:
            c.upsert("alertPolicies", "alertPolicies", {
                "displayName": f"{PREFIX} {name}",
                "combiner": "OR",
                "conditions": [{"displayName": name, "conditionThreshold": {
                    "filter": f'metric.type="{metric}" AND {db}',
                    "aggregations": [{"alignmentPeriod": "60s", "perSeriesAligner": aligner}],
                    "comparison": cmp, "thresholdValue": value, "duration": duration, "trigger": {"count": 1}}}],
                "notificationChannels": [channel],
                "documentation": {"mimeType": "text/markdown",
                                  "content": "Runbook: `deploy/README.md` → Operate → Database."},
            }, "displayName,conditions,notificationChannels,documentation,combiner")
    else:
        print("skip   Cloud SQL alerts: shipcube-pg doesn't exist yet (re-run after deploy/cloudsql.sh)")


if __name__ == "__main__":
    main()
