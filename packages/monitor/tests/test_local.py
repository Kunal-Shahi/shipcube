from __future__ import annotations

import json
import struct
from datetime import UTC, datetime

import httpx
import pytest

from shipcube_monitor import AppTarget, MonitorUnavailable
from shipcube_monitor.local import LocalMonitor, _frames

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
T = AppTarget(app="hello", project_id="local", region="local", service="hello-ab12cd", cpu=1, memory_mib=512)


def mux(*lines: tuple[int, str]) -> bytes:
    out = b""
    for kind, text in lines:
        b = (text + "\n").encode()
        out += bytes([kind, 0, 0, 0]) + struct.pack(">I", len(b)) + b
    return out


LOGS = mux(
    (1, '2026-10-29T11:59:00.000Z 172.18.0.1 - - "GET / HTTP/1.1" 200 12'),
    (1, '2026-10-29T11:59:01.000Z 172.18.0.1 - - "GET /ai HTTP/1.1" 500 30'),
    (1, '2026-10-29T11:59:02.000Z 172.18.0.1 - - "GET /x HTTP/1.1" 429 3'),
    (2, "2026-10-29T11:59:03.000Z [2026-10-29 11:59:03] ERROR boom"),
)
STATS = {"cpu_stats": {"cpu_usage": {"total_usage": 300}, "system_cpu_usage": 2000, "online_cpus": 4},
         "precpu_stats": {"cpu_usage": {"total_usage": 100}, "system_cpu_usage": 1000},
         "memory_stats": {"usage": 128 * 1024 * 1024, "limit": 512 * 1024 * 1024}}


IAP_REQUESTS = [("hello-ab12cd", 200, 10), ("hello-ab12cd", 200, 20), ("hello-ab12cd", 500, 30),
                ("hello-ab12cd", 200, 400), ("other-app", 200, 999)]
IAP_LOGS = mux(*[(1, f"2026-10-29T11:59:{i:02d}.000Z " + json.dumps(
    {"shipcube_request": True, "service": svc, "status": st, "latency_ms": ms}))
    for i, (svc, st, ms) in enumerate(IAP_REQUESTS)])


def docker(seen: list[httpx.Request], iap: bool = False) -> httpx.Client:
    def handler(r: httpx.Request) -> httpx.Response:
        seen.append(r)
        p = r.url.path
        if p == "/containers/json":
            f = json.loads(r.url.params["filters"])
            if f == {"label": ["com.docker.compose.service=local-iap"]}:
                return httpx.Response(200, json=[{"Id": "iap", "State": "running"}] if iap else [])
            assert f == {"label": ["shipcube.service=hello-ab12cd"]}
            return httpx.Response(200, json=[{"Id": "c1", "State": "running", "Names": ["/hello-ab12cd-1"]}])
        if p == "/containers/iap/logs":
            return httpx.Response(200, content=IAP_LOGS)
        if p.endswith("/logs"):
            return httpx.Response(200, content=LOGS)
        if p.endswith("/stats"):
            return httpx.Response(200, json=STATS)
        if p == "/events":
            return httpx.Response(200, text='{"status":"start"}\n{"status":"start"}\n')
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://docker")


def test_metrics_from_docker():
    m = LocalMonitor(client=docker([]), now=lambda: NOW).metrics(T, "1h")
    assert m["requests"] == 3 and m["error_rate"] == round(1 / 3, 4)
    assert m["caps_hit"]["rate_limited_requests"] == 1
    assert m["instances"] == {"avg": 1.0, "max": 1.0} and m["cold_starts"] == 2
    assert m["cpu_utilization"] == 0.8  # 200/1000 of 4 CPUs = 0.8 vCPU against 1 requested
    assert m["memory_utilization"] == 0.25
    assert m["cost_usd"]["compute"] == 0.0 and m["latency_ms"] == {"p50": 0.0, "p95": 0.0}


def test_metrics_from_the_local_iap():
    m = LocalMonitor(client=docker([], iap=True), now=lambda: NOW).metrics(T, "1h")
    assert m["requests"] == 4 and m["error_rate"] == 0.25          # other-app's line is ignored
    assert m["latency_ms"] == {"p50": 30.0, "p95": 400.0}


def test_logs_newest_first_with_severity_and_paging():
    mon = LocalMonitor(client=docker([]), now=lambda: NOW)
    page = mon.logs(T, limit=2)
    assert page["entries"][0]["severity"] == "ERROR" and page["entries"][0]["revision"] == "hello-ab12cd-1"
    assert page["next_page_token"] == "2"
    assert [e["severity"] for e in mon.logs(T, severity="ERROR")["entries"]] == ["ERROR"]


def test_tty_logs_are_plain_text():
    assert _frames(b"hello\nworld\n") == [("stdout", "hello"), ("stdout", "world")]


def test_docker_down_is_unavailable():
    def boom(r):
        raise httpx.ConnectError("no socket", request=r)

    mon = LocalMonitor(client=httpx.Client(transport=httpx.MockTransport(boom), base_url="http://docker"))
    with pytest.raises(MonitorUnavailable):
        mon.metrics(T)
