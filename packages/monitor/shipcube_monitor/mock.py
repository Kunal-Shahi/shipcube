"""Offline provider for tests and the console: deterministic numbers per app and window."""

from __future__ import annotations

import hashlib
import random
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from . import pricing
from .types import WINDOWS, AppTarget, Window, severity_at_least

LINES = [
    ("INFO", "GET /api/revenue?region=SP 200 84ms"),
    ("INFO", "GET / 200 12ms"),
    ("WARNING", "data layer: query scanned all partitions (no time range)"),
    ("INFO", "Starting new instance. Reason: AUTOSCALING"),
    ("ERROR", "upstream warehouse timeout after 30s"),
    ("INFO", "POST /api/score 200 231ms"),
]
TOTAL_LOGS = 120


class MockMonitor:
    def __init__(self, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._now = now

    def _rng(self, *parts: str) -> random.Random:
        return random.Random(int(hashlib.sha256("|".join(parts).encode()).hexdigest()[:16], 16))

    def metrics(self, target: AppTarget, window: Window = "1h") -> dict[str, Any]:
        if window not in WINDOWS:
            raise ValueError(f"unknown window {window!r}")
        rng = self._rng(target.app, window)
        hours = WINDOWS[window].total_seconds() / 3600
        requests = int(rng.uniform(80, 160) * hours)
        avg_inst = round(rng.uniform(0.1, 0.6), 2)
        billable_s = avg_inst * hours * 3600
        compute = pricing.compute_cost(billable_s, target.cpu, target.memory_mib, target.region)
        compute += pricing.request_cost(requests)
        ai = round(requests * 0.3 * 900 / 1e6 * 0.5, 4)
        now = self._now()
        days = (now - now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)).total_seconds() / 86400
        freshness = None
        if target.data_last_changed:
            changed = target.data_last_changed()
            freshness = round((now - changed).total_seconds() / 60, 1) if changed else None
        return {
            "app": target.app,
            "window": window,
            "requests": requests,
            "error_rate": round(rng.uniform(0.0, 0.02), 4),
            "latency_ms": {"p50": round(rng.uniform(40, 120), 1), "p95": round(rng.uniform(250, 600), 1)},
            "instances": {"avg": avg_inst, "max": float(rng.randint(1, 3))},
            "cold_starts": max(1, int(rng.uniform(1, 3) * min(hours, 24))),
            "cpu_utilization": round(rng.uniform(0.1, 0.4), 3),
            "memory_utilization": round(rng.uniform(0.3, 0.6), 3),
            "cost_usd": {
                "compute": round(compute, 4),
                "ai_tokens": ai,
                "warehouse": 0.0,
                "month_to_date": round((compute + ai) / hours * 24 * max(days, 1), 4),
            },
            "caps_hit": {"rate_limited_requests": rng.randint(0, 4), "max_instances_reached": 0,
                         "ai_requests_rejected": 0},
            "data_freshness_minutes": freshness,
        }

    def logs(
        self,
        target: AppTarget,
        limit: int = 50,
        severity: str | None = None,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        allowed = set(severity_at_least(severity))
        base = self._now()
        entries = []
        for i in range(TOTAL_LOGS):
            sev, msg = LINES[i % len(LINES)]
            if sev not in allowed:
                continue
            ts = (base - timedelta(seconds=37 * i)).isoformat(timespec="seconds").replace("+00:00", "Z")
            entries.append({"timestamp": ts, "severity": sev, "message": msg, "revision": f"{target.service}-00001"})
        offset = int(page_token) if page_token else 0
        page = entries[offset:offset + limit]
        nxt = offset + limit
        return {"entries": page, "next_page_token": str(nxt) if nxt < len(entries) else None}
