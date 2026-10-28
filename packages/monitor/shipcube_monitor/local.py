"""Local provider (SHIPCUBE_MONITOR=local): metrics and logs for apps the local deployer runs as Docker
containers, read from the Docker Engine API over its unix socket.

Apps are found by the label `shipcube.service=<AppTarget.service>` (deploy/local contract). What it
reports, and what it can't:
- instances: running containers; cold_starts: container `start` events in the window.
- cpu/memory utilisation: one `stats` sample per running container, against the app's requested
  CPU and memory.
- requests, 5xx and 429 counts: parsed from access-log lines in the window ("GET /path HTTP/1.1" 200).
  Apps that don't log requests report 0 requests.
- latency percentiles: 0. There's no request proxy locally; Cloud Run measures them in production.
- cost: 0. Nothing is billed locally.
"""

from __future__ import annotations

import json
import os
import re
import struct
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from .errors import MonitorError, MonitorUnavailable
from .types import WINDOWS, AppTarget, NoUsage, UsageSource, Window, severity_at_least

ACCESS = re.compile(r'"(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) [^"]*" (\d{3})\b')
LEVEL = re.compile(r"\b(CRITICAL|ERROR|WARNING|WARN|INFO|DEBUG)\b")
MAX_LINES = 5000


def _client() -> httpx.Client:
    sock = os.environ.get("DOCKER_SOCKET", "/var/run/docker.sock")
    return httpx.Client(transport=httpx.HTTPTransport(uds=sock), base_url="http://docker", timeout=15)


def _frames(raw: bytes) -> list[tuple[str, str]]:
    """Split Docker's multiplexed log stream into (stream, line); plain text if the container has a TTY."""
    out: list[tuple[str, str]] = []
    if not raw or raw[0] not in (1, 2) or len(raw) < 8:
        return [("stdout", line) for line in raw.decode(errors="replace").splitlines()]
    i = 0
    while i + 8 <= len(raw):
        kind, size = raw[i], struct.unpack(">I", raw[i + 4:i + 8])[0]
        chunk = raw[i + 8:i + 8 + size].decode(errors="replace")
        out += [("stderr" if kind == 2 else "stdout", line) for line in chunk.splitlines()]
        i += 8 + size
    return out


def _severity(stream: str, text: str) -> str:
    if "Traceback" in text:
        return "ERROR"
    m = LEVEL.search(text)
    if m:
        return {"WARN": "WARNING"}.get(m[1], m[1])
    return "INFO"


class LocalMonitor:
    def __init__(self, client: httpx.Client | None = None, usage: UsageSource | None = None,
                 now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._http = client or _client()
        self._usage = usage or NoUsage()
        self._now = now

    def _get(self, path: str, **params: Any) -> httpx.Response:
        try:
            r = self._http.get(path, params=params)
        except httpx.TransportError as e:
            raise MonitorUnavailable(f"docker engine unreachable: {e}") from e
        if r.status_code >= 400:
            raise MonitorError(f"docker {path} returned {r.status_code}: {r.text[:200]}", r.status_code)
        return r

    def _containers(self, t: AppTarget, running_only: bool = False) -> list[dict[str, Any]]:
        filters = {"label": [f"shipcube.service={t.service}"]}
        return self._get("/containers/json", all="false" if running_only else "true",
                         filters=json.dumps(filters)).json()

    def _lines(self, cid: str, since: int) -> list[tuple[str, str, str]]:
        raw = self._get(f"/containers/{cid}/logs", stdout=1, stderr=1, timestamps=1, since=since,
                        tail=MAX_LINES).content
        out = []
        for stream, line in _frames(raw):
            ts, _, text = line.partition(" ")
            out.append((ts, stream, text))
        return out

    def metrics(self, target: AppTarget, window: Window = "1h") -> dict[str, Any]:
        if window not in WINDOWS:
            raise ValueError(f"unknown window {window!r}")
        end = self._now()
        start = end - WINDOWS[window]
        containers = self._containers(target)
        running = [c for c in containers if c.get("State") == "running"]

        codes: list[int] = []
        for c in containers:
            for _, _, text in self._lines(c["Id"], int(start.timestamp())):
                if m := ACCESS.search(text):
                    codes.append(int(m[1]))

        cpu = mem = 0.0
        for c in running:
            s = self._get(f"/containers/{c['Id']}/stats", stream="false").json()
            cpu += _cpu_vcpus(s) / max(target.cpu, 0.001)
            limit = s.get("memory_stats", {}).get("limit") or target.memory_mib * 1024 * 1024
            mem += s.get("memory_stats", {}).get("usage", 0) / limit
        n = len(running)

        events = self._get("/events", since=int(start.timestamp()), until=int(end.timestamp()),
                           filters=json.dumps({"type": ["container"], "event": ["start"],
                                               "label": [f"shipcube.service={target.service}"]})).text
        starts = sum(1 for line in events.splitlines() if line.strip())

        ai = self._usage.ai_usage(target.app_id, start, end)
        requests = len(codes)
        freshness = None
        if target.data_last_changed and (changed := target.data_last_changed()):
            freshness = round((end - changed).total_seconds() / 60, 1)
        return {
            "app": target.app,
            "window": window,
            "requests": requests,
            "error_rate": round(sum(1 for c in codes if c >= 500) / requests, 4) if requests else 0.0,
            "latency_ms": {"p50": 0.0, "p95": 0.0},
            "instances": {"avg": float(n), "max": float(n)},
            "cold_starts": starts,
            "cpu_utilization": round(cpu / n, 3) if n else 0.0,
            "memory_utilization": round(mem / n, 3) if n else 0.0,
            "cost_usd": {"compute": 0.0, "ai_tokens": round(ai["cost_usd"], 4), "warehouse": 0.0,
                         "month_to_date": 0.0},
            "caps_hit": {"rate_limited_requests": sum(1 for c in codes if c == 429),
                         "max_instances_reached": 0, "ai_requests_rejected": ai["rejected_requests"]},
            "data_freshness_minutes": freshness,
        }

    def logs(self, target: AppTarget, limit: int = 50, severity: str | None = None,
             page_token: str | None = None) -> dict[str, Any]:
        allowed = set(severity_at_least(severity))
        entries = []
        for c in self._containers(target):
            name = (c.get("Names") or ["/" + c["Id"][:12]])[0].lstrip("/")
            for ts, stream, text in self._lines(c["Id"], 0):
                sev = _severity(stream, text)
                if sev in allowed:
                    entries.append({"timestamp": ts, "severity": sev, "message": text[:2000], "revision": name})
        entries.sort(key=lambda e: e["timestamp"], reverse=True)
        offset = int(page_token) if page_token else 0
        page = entries[offset:offset + limit]
        nxt = offset + limit
        return {"entries": page, "next_page_token": str(nxt) if nxt < len(entries) else None}


def _cpu_vcpus(s: dict[str, Any]) -> float:
    """vCPUs in use, from one stats sample (cpu delta over system delta × online CPUs)."""
    cur, pre = s.get("cpu_stats", {}), s.get("precpu_stats", {})
    cpu_delta = cur.get("cpu_usage", {}).get("total_usage", 0) - pre.get("cpu_usage", {}).get("total_usage", 0)
    sys_delta = cur.get("system_cpu_usage", 0) - pre.get("system_cpu_usage", 0)
    if cpu_delta <= 0 or sys_delta <= 0:
        return 0.0
    return cpu_delta / sys_delta * (cur.get("online_cpus") or 1)
