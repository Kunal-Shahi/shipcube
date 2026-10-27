from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from shipcube_monitor import AppTarget, MockMonitor, Rule, evaluate, notify, pricing, provider_from_env
from shipcube_monitor.distribution import percentile
from shipcube_monitor.gcp import GcpMonitor, MonitorError

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)

# Keys of networking's Metrics model (services/api/shipcube_api/models.py).
METRICS_KEYS = {
    "app", "window", "requests", "error_rate", "latency_ms", "instances", "cold_starts", "cpu_utilization",
    "memory_utilization", "cost_usd", "caps_hit", "data_freshness_minutes",
}


def target(**kw) -> AppTarget:
    base = dict(app="tracker", project_id="shipcube-apps-ks", region="asia-south1", service="tracker",
                cpu=1.0, memory_mib=512, caps={"max_instances": 2})
    return AppTarget(**(base | kw))


def point(value: dict) -> dict:
    return {"interval": {"endTime": "2026-10-27T12:00:00Z"}, "value": value}


def dist(counts: list[int], mean: float) -> dict:
    return {"distributionValue": {
        "count": str(sum(counts)), "mean": mean,
        "bucketOptions": {"explicitBuckets": {"bounds": [10, 50, 100, 500, 1000]}},
        "bucketCounts": [str(c) for c in counts],
    }}


SERIES = {
    "run.googleapis.com/request_count": [
        {"metric": {"labels": {"response_code": "200"}}, "points": [point({"int64Value": "940"})]},
        {"metric": {"labels": {"response_code": "429"}}, "points": [point({"int64Value": "10"})]},
        {"metric": {"labels": {"response_code": "503"}}, "points": [point({"int64Value": "50"})]},
    ],
    "run.googleapis.com/request_latencies": [{"points": [point(dist([0, 50, 40, 10, 0, 0], 80.0))]}],
    "run.googleapis.com/container/instance_count": [
        {"points": [point({"int64Value": "0"}), point({"int64Value": "1"}), point({"int64Value": "2"})]}
    ],
    "run.googleapis.com/container/startup_latencies": [{"points": [point(dist([0, 0, 0, 3, 1, 0], 900.0))]}],
    "run.googleapis.com/container/cpu/utilizations": [{"points": [point(dist([10], 0.25))]}],
    "run.googleapis.com/container/memory/utilizations": [{"points": [point(dist([10], 0.5))]}],
    "run.googleapis.com/container/billable_instance_time": [{"points": [point({"doubleValue": 1000.0})]}],
}


def fake_gcp(seen: list[httpx.Request]) -> httpx.Client:
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        assert req.headers["authorization"] == "Bearer test-token"
        if req.url.host == "monitoring.googleapis.com":
            flt = req.url.params["filter"]
            metric = flt.split('"')[1]
            assert 'resource.labels.service_name="tracker"' in flt
            assert 'resource.labels.location="asia-south1"' in flt
            return httpx.Response(200, json={"timeSeries": SERIES.get(metric, [])})
        if req.url.host == "logging.googleapis.com":
            body = json.loads(req.content)
            if body.get("pageToken") == "p2":
                return httpx.Response(200, json={"entries": []})
            return httpx.Response(200, json={"nextPageToken": "p2", "entries": [
                {"timestamp": "2026-10-27T11:59:00Z", "severity": "ERROR", "textPayload": "boom",
                 "resource": {"labels": {"revision_name": "tracker-00002"}}},
                {"timestamp": "2026-10-27T11:58:00Z", "severity": "INFO", "jsonPayload": {"message": "hi"}},
                {"timestamp": "2026-10-27T11:57:00Z", "severity": "INFO",
                 "httpRequest": {"requestMethod": "GET", "requestUrl": "https://x.run.app/api?q=1",
                                 "status": 200, "latency": "0.084s"}},
            ]})
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def gcp(seen: list[httpx.Request], **kw) -> GcpMonitor:
    return GcpMonitor(token=lambda: "test-token", client=fake_gcp(seen), now=lambda: NOW, **kw)


def test_gcp_metrics_shape_and_values():
    seen: list[httpx.Request] = []
    m = gcp(seen).metrics(target(), "1h")
    assert set(m) == METRICS_KEYS
    assert m["requests"] == 1000
    assert m["error_rate"] == 0.05
    assert 10 <= m["latency_ms"]["p50"] <= 100
    assert m["latency_ms"]["p95"] > m["latency_ms"]["p50"]
    assert m["instances"] == {"avg": 0.05, "max": 2.0}  # 3 instance-steps over 60 one-minute steps
    assert m["cold_starts"] == 4
    assert m["cpu_utilization"] == 0.25 and m["memory_utilization"] == 0.5
    assert m["caps_hit"]["rate_limited_requests"] == 10
    assert m["caps_hit"]["max_instances_reached"] == 1
    expected = 1000 * (0.000024 + 0.5 * 0.0000025) + 1000 / 1e6 * 0.40
    assert m["cost_usd"]["compute"] == pytest.approx(expected, abs=1e-4)
    assert m["data_freshness_minutes"] is None
    # One windowed query per metric plus month-to-date billable time and requests.
    assert len(seen) == 9


def test_gcp_job_skips_request_metrics():
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        assert 'resource.type="cloud_run_job"' in req.url.params["filter"]
        return httpx.Response(200, json={})

    mon = GcpMonitor(token=lambda: "test-token", client=httpx.Client(transport=httpx.MockTransport(handler)),
                     now=lambda: NOW)
    m = mon.metrics(target(kind="job"), "24h")
    assert m["requests"] == 0 and m["error_rate"] == 0.0
    assert not any("request_count" in r.url.params["filter"] for r in seen)


def test_gcp_freshness_and_ai_usage():
    class Usage:
        def ai_usage(self, app_id, start, end):
            return {"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.25, "rejected_requests": 3}

    t = target(data_last_changed=lambda: NOW - timedelta(minutes=42))
    m = gcp([], usage=Usage()).metrics(t, "1h")
    assert m["data_freshness_minutes"] == 42.0
    assert m["cost_usd"]["ai_tokens"] == 0.25
    assert m["caps_hit"]["ai_requests_rejected"] == 3


def test_gcp_logs_paging_and_mapping():
    seen: list[httpx.Request] = []
    mon = gcp(seen)
    page = mon.logs(target(), limit=3, severity="info")
    assert [e["message"] for e in page["entries"]] == ["boom", "hi", "GET /api?q=1 200 84ms"]
    assert page["entries"][0]["revision"] == "tracker-00002"
    assert page["next_page_token"] == "p2"
    body = json.loads(seen[0].content)
    assert "severity>=INFO" in body["filter"] and body["orderBy"] == "timestamp desc"
    assert mon.logs(target(), page_token="p2")["next_page_token"] is None


def test_gcp_errors_are_raised():
    client = httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(403, json={"error": {"message": "Permission denied"}})))
    mon = GcpMonitor(token=lambda: "t", client=client, now=lambda: NOW)
    with pytest.raises(MonitorError, match="403"):
        mon.logs(target())
    with pytest.raises(ValueError):
        mon.logs(target(), severity="LOUD")


def test_percentile_exponential_buckets():
    d = {"bucketOptions": {"exponentialBuckets": {"numFiniteBuckets": 3, "growthFactor": 2, "scale": 1}},
         "bucketCounts": ["0", "10", "0", "0", "0"]}
    assert 1 <= percentile(d, 0.5) <= 2
    assert percentile({"bucketCounts": []}, 0.5) is None


def test_mock_is_deterministic_and_pages():
    mon = MockMonitor(now=lambda: NOW)
    a, b = mon.metrics(target(), "24h"), mon.metrics(target(), "24h")
    assert a == b and set(a) == METRICS_KEYS
    first = mon.logs(target(), limit=10, severity="WARNING")
    assert all(e["severity"] in {"WARNING", "ERROR"} for e in first["entries"])
    second = mon.logs(target(), limit=10, severity="WARNING", page_token=first["next_page_token"])
    assert first["entries"][0] != second["entries"][0]


def test_provider_from_env(monkeypatch):
    monkeypatch.delenv("SHIPCUBE_MONITOR", raising=False)
    assert isinstance(provider_from_env(), MockMonitor)
    monkeypatch.setenv("SHIPCUBE_MONITOR", "nope")
    with pytest.raises(ValueError):
        provider_from_env()


def test_pricing_tiers():
    assert pricing.region_tier("asia-south1") == 1
    assert pricing.region_tier("asia-southeast2") == 2
    assert pricing.compute_cost(3600, 1, 1024, "asia-south1") == pytest.approx(3600 * (0.000024 + 0.0000025))
    assert pricing.request_cost(3_000_000, apply_free_tier=True) == pytest.approx(0.40)


def test_alerts_fire_and_notify():
    m = {"app": "tracker", "window": "1h", "error_rate": 0.1, "latency_ms": {"p95": 100},
         "memory_utilization": 0.2, "data_freshness_minutes": None}
    fired = evaluate(m)
    assert [a["metric"] for a in fired] == ["error_rate"]
    assert evaluate(m, [Rule("latency_ms.p95", ">=", 100)])[0]["value"] == 100

    sent = []
    client = httpx.Client(transport=httpx.MockTransport(lambda r: sent.append(json.loads(r.content))
                                                        or httpx.Response(204)))
    assert notify("https://hooks.example.com/x", fired, client) == 1
    assert "error_rate" in sent[0]["text"]
    with pytest.raises(ValueError):
        notify("http://insecure", fired, client)
