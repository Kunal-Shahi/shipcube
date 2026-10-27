"""Real provider: Cloud Monitoring (metrics) and Cloud Logging (entries) over their REST APIs.

REST rather than the google-cloud-* client libraries keeps the dependency to httpx (+ google-auth for
the token) and makes the requests easy to fake in tests.

Metric types (https://cloud.google.com/monitoring/api/metrics_gcp_p_z#gcp-run):
- run.googleapis.com/request_count                    DELTA INT64, label response_code
- run.googleapis.com/request_latencies                DELTA DISTRIBUTION, ms
- run.googleapis.com/container/instance_count         GAUGE INT64, label state
- run.googleapis.com/container/startup_latencies      DELTA DISTRIBUTION, ms (one sample per cold start)
- run.googleapis.com/container/cpu/utilizations       DELTA DISTRIBUTION, 0..1
- run.googleapis.com/container/memory/utilizations    DELTA DISTRIBUTION, 0..1
- run.googleapis.com/container/billable_instance_time DELTA DOUBLE, seconds
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from . import pricing
from .distribution import merge, percentile
from .types import WINDOWS, AppTarget, NoUsage, UsageSource, Window, severity_at_least

MONITORING = "https://monitoring.googleapis.com/v3"
LOGGING = "https://logging.googleapis.com/v2"
MAX_MESSAGE = 2000

# Step used for gauge series (instance count), per window: enough resolution, at most ~720 points.
STEPS = {"15m": 60, "1h": 60, "24h": 300, "7d": 3600, "30d": 3600}


class MonitorError(RuntimeError):
    pass


def adc_token_provider() -> Callable[[], str]:
    """Application Default Credentials (the service's own SA on Cloud Run, gcloud ADC locally)."""
    import google.auth
    import google.auth.transport.requests

    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    request = google.auth.transport.requests.Request()

    def token() -> str:
        if not creds.valid:
            creds.refresh(request)
        return creds.token

    return token


def _rfc3339(t: datetime) -> str:
    return t.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class GcpMonitor:
    def __init__(
        self,
        token: Callable[[], str] | None = None,
        usage: UsageSource | None = None,
        client: httpx.Client | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._token = token or adc_token_provider()
        self._usage = usage or NoUsage()
        self._http = client or httpx.Client(timeout=20)
        self._now = now

    # --- HTTP -------------------------------------------------------------------

    def _headers(self, project: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token()}", "x-goog-user-project": project}

    def _check(self, r: httpx.Response) -> dict[str, Any]:
        if r.status_code >= 400:
            try:
                msg = r.json().get("error", {}).get("message", r.text)
            except json.JSONDecodeError:
                msg = r.text
            raise MonitorError(f"{r.request.url.host} returned {r.status_code}: {msg[:300]}")
        return r.json()

    def _series(
        self,
        target: AppTarget,
        metric: str,
        start: datetime,
        end: datetime,
        aligner: str,
        reducer: str,
        period_s: int,
        group_by: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        res = "cloud_run_job" if target.kind == "job" else "cloud_run_revision"
        name_label = "job_name" if target.kind == "job" else "service_name"
        params: list[tuple[str, str]] = [
            (
                "filter",
                f'metric.type="{metric}" AND resource.type="{res}" '
                f'AND resource.labels.{name_label}="{target.service}" AND resource.labels.location="{target.region}"',
            ),
            ("interval.startTime", _rfc3339(start)),
            ("interval.endTime", _rfc3339(end)),
            ("aggregation.alignmentPeriod", f"{period_s}s"),
            ("aggregation.perSeriesAligner", aligner),
            ("aggregation.crossSeriesReducer", reducer),
        ]
        params += [("aggregation.groupByFields", g) for g in group_by or []]
        out: list[dict[str, Any]] = []
        token = None
        while True:
            page = params + ([("pageToken", token)] if token else [])
            body = self._check(
                self._http.get(
                    f"{MONITORING}/projects/{target.project_id}/timeSeries",
                    params=page,
                    headers=self._headers(target.project_id),
                )
            )
            out += body.get("timeSeries", [])
            token = body.get("nextPageToken")
            if not token:
                return out

    # --- metrics ----------------------------------------------------------------

    def metrics(self, target: AppTarget, window: Window = "1h") -> dict[str, Any]:
        if window not in WINDOWS:
            raise ValueError(f"unknown window {window!r}")
        end = self._now()
        start = end - WINDOWS[window]
        month_start = end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        whole = max(60, int((end - start).total_seconds()))
        mtd = max(60, int((end - month_start).total_seconds()))
        step = STEPS[window]

        def delta(metric: str, reducer: str = "REDUCE_SUM", group_by=None, s=start, period=whole):
            return lambda: self._series(target, metric, s, end, "ALIGN_DELTA", reducer, period, group_by)

        is_service = target.kind == "service"
        queries: dict[str, Callable[[], list[dict[str, Any]]]] = {
            "instances": lambda: self._series(
                target, "run.googleapis.com/container/instance_count", start, end, "ALIGN_MAX", "REDUCE_SUM", step
            ),
            "startups": delta("run.googleapis.com/container/startup_latencies"),
            "cpu": delta("run.googleapis.com/container/cpu/utilizations"),
            "memory": delta("run.googleapis.com/container/memory/utilizations"),
            "billable": delta("run.googleapis.com/container/billable_instance_time"),
            "billable_mtd": delta("run.googleapis.com/container/billable_instance_time", s=month_start, period=mtd),
        }
        if is_service:
            queries |= {
                "requests": delta("run.googleapis.com/request_count", group_by=["metric.label.response_code"]),
                "latency": delta("run.googleapis.com/request_latencies"),
                "requests_mtd": delta("run.googleapis.com/request_count", s=month_start, period=mtd),
            }
        with ThreadPoolExecutor(max_workers=len(queries)) as pool:
            futures = {k: pool.submit(q) for k, q in queries.items()}
            r = {k: f.result() for k, f in futures.items()}

        by_code: dict[str, int] = defaultdict(int)
        for ts in r.get("requests", []):
            code = ts.get("metric", {}).get("labels", {}).get("response_code", "")
            by_code[code] += sum(_int(p) for p in ts.get("points", []))
        requests = sum(by_code.values())
        errors_5xx = sum(n for code, n in by_code.items() if code.startswith("5"))

        latency = merge(_dists(r.get("latency", [])))
        p50 = percentile(latency, 0.50) if latency else None
        p95 = percentile(latency, 0.95) if latency else None

        inst = [v for ts in r["instances"] for v in (_num(p) for p in ts.get("points", []))]
        startups = merge(_dists(r["startups"]))
        cpu = merge(_dists(r["cpu"]))
        mem = merge(_dists(r["memory"]))

        billable = sum(_num(p) for ts in r["billable"] for p in ts.get("points", []))
        billable_mtd = sum(_num(p) for ts in r["billable_mtd"] for p in ts.get("points", []))
        requests_mtd = sum(_int(p) for ts in r.get("requests_mtd", []) for p in ts.get("points", []))

        ai = self._usage.ai_usage(target.app_id, start, end)
        ai_mtd = self._usage.ai_usage(target.app_id, month_start, end)
        compute = pricing.compute_cost(billable, target.cpu, target.memory_mib, target.region)
        compute += pricing.request_cost(requests)
        mtd_total = (
            pricing.compute_cost(billable_mtd, target.cpu, target.memory_mib, target.region)
            + pricing.request_cost(requests_mtd)
            + ai_mtd["cost_usd"]
        )

        max_instances = target.caps.get("max_instances")
        return {
            "app": target.app,
            "window": window,
            "requests": requests,
            "error_rate": round(errors_5xx / requests, 4) if requests else 0.0,
            "latency_ms": {"p50": round(p50 or 0.0, 1), "p95": round(p95 or 0.0, 1)},
            "instances": {
                # Monitoring omits steps with no instances, so average over every step in the window.
                "avg": round(sum(inst) / max(len(inst), whole // step), 2) if inst else 0.0,
                "max": max(inst) if inst else 0.0,
            },
            "cold_starts": int(startups["count"]) if startups else 0,
            "cpu_utilization": round(cpu["mean"], 3) if cpu else 0.0,
            "memory_utilization": round(mem["mean"], 3) if mem else 0.0,
            "cost_usd": {
                "compute": round(compute, 4),
                "ai_tokens": round(ai["cost_usd"], 4),
                "warehouse": 0.0,
                "month_to_date": round(mtd_total, 4),
            },
            "caps_hit": {
                "rate_limited_requests": by_code.get("429", 0),
                "max_instances_reached": sum(1 for v in inst if max_instances and v >= max_instances),
                "ai_requests_rejected": ai["rejected_requests"],
            },
            "data_freshness_minutes": _freshness(target, end),
        }

    # --- logs ---------------------------------------------------------------------

    def logs(
        self,
        target: AppTarget,
        limit: int = 50,
        severity: str | None = None,
        page_token: str | None = None,
        since: timedelta = timedelta(days=7),
    ) -> dict[str, Any]:
        res = "cloud_run_job" if target.kind == "job" else "cloud_run_revision"
        name_label = "job_name" if target.kind == "job" else "service_name"
        flt = (
            f'resource.type="{res}" AND resource.labels.{name_label}="{target.service}" '
            f'AND resource.labels.location="{target.region}" AND timestamp>="{_rfc3339(self._now() - since)}"'
        )
        if severity:
            severity_at_least(severity)  # validates
            flt += f" AND severity>={severity.upper()}"
        body: dict[str, Any] = {
            "resourceNames": [f"projects/{target.project_id}"],
            "filter": flt,
            "orderBy": "timestamp desc",
            "pageSize": max(1, min(limit, 1000)),
        }
        if page_token:
            body["pageToken"] = page_token
        data = self._check(
            self._http.post(f"{LOGGING}/entries:list", json=body, headers=self._headers(target.project_id))
        )
        return {
            "entries": [_entry(e) for e in data.get("entries", [])],
            "next_page_token": data.get("nextPageToken") or None,
        }


def _int(point: dict[str, Any]) -> int:
    return int(point.get("value", {}).get("int64Value", 0))


def _num(point: dict[str, Any]) -> float:
    v = point.get("value", {})
    return float(v.get("doubleValue", v.get("int64Value", 0)))


def _dists(series: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [p["value"]["distributionValue"] for ts in series for p in ts.get("points", [])
            if "distributionValue" in p.get("value", {})]


def _freshness(target: AppTarget, now: datetime) -> float | None:
    if not target.data_last_changed:
        return None
    changed = target.data_last_changed()
    if changed is None:
        return None
    return round(max(0.0, (now - changed).total_seconds() / 60), 1)


def _entry(e: dict[str, Any]) -> dict[str, Any]:
    """One LogEntry → networking's LogEntry. Log text is app output: callers treat it as data."""
    if "textPayload" in e:
        msg = e["textPayload"]
    elif "jsonPayload" in e:
        jp = e["jsonPayload"]
        msg = jp.get("message") or jp.get("msg") or json.dumps(jp, separators=(",", ":"))
    elif "httpRequest" in e:
        h = e["httpRequest"]
        path = httpx.URL(h.get("requestUrl", "")).raw_path.decode() or "/"
        lat = h.get("latency", "")
        ms = f" {round(float(lat.rstrip('s')) * 1000)}ms" if lat.endswith("s") else ""
        msg = f"{h.get('requestMethod', '')} {path} {h.get('status', '')}{ms}".strip()
    else:
        msg = ""
    return {
        "timestamp": e.get("timestamp", ""),
        "severity": e.get("severity", "DEFAULT"),
        "message": str(msg)[:MAX_MESSAGE],
        "revision": e.get("resource", {}).get("labels", {}).get("revision_name"),
    }
