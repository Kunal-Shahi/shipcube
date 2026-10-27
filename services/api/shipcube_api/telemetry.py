"""Logs and metrics for inspect_app. The platform workstream owns the real implementation
(Cloud Logging + Cloud Monitoring); until it lands, MockTelemetry returns canned data.

Set `telemetry` to another implementation at startup (`shipcube_api.telemetry.telemetry = ...`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Protocol


class Telemetry(Protocol):
    def logs(self, app: dict[str, Any], limit: int, severity: str | None, page_token: str | None) -> dict[str, Any]:
        """{entries: [{timestamp, severity, message, revision}], next_page_token}"""

    def metrics(self, app: dict[str, Any], window: str) -> dict[str, Any]:
        """Matches models.Metrics."""


class MockTelemetry:
    def logs(self, app: dict[str, Any], limit: int, severity: str | None, page_token: str | None) -> dict[str, Any]:
        lines = [
            ("INFO", "GET /api/revenue?region=SP 200 84ms"),
            ("INFO", "GET / 200 12ms"),
            ("WARNING", "data layer: query scanned all partitions (no time range)"),
            ("INFO", "Cold start: 1.9s"),
            ("ERROR", "upstream warehouse timeout after 30s"),
        ]
        base = datetime.now(UTC)
        out = []
        for i in range(min(limit, 20)):
            sev, msg = lines[i % len(lines)]
            ts = (base - timedelta(seconds=37 * i)).isoformat(timespec="seconds").replace("+00:00", "Z")
            out.append({"timestamp": ts, "severity": sev, "message": msg, "revision": app["revision"]})
        return {"entries": [e for e in out if not severity or e["severity"] == severity], "next_page_token": None}

    def metrics(self, app: dict[str, Any], window: str) -> dict[str, Any]:
        return {
            "app": app["name"], "window": window, "requests": 412, "error_rate": 0.012,
            "latency_ms": {"p50": 84, "p95": 410}, "instances": {"avg": 0.4, "max": 2}, "cold_starts": 6,
            "cpu_utilization": 0.21, "memory_utilization": 0.43,
            "cost_usd": {"compute": 0.03, "ai_tokens": 0.01, "warehouse": 0.0, "month_to_date": 0.62},
            "caps_hit": {"rate_limited_requests": 3, "max_instances_reached": 0},
            "data_freshness_minutes": 42,
        }


telemetry: Telemetry = MockTelemetry()
