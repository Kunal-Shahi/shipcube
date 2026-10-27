"""ShipCube monitoring provider behind `inspect_app` (metrics and logs).

Usage from the API:

    from shipcube_monitor import AppTarget, provider_from_env
    monitor = provider_from_env()          # Cloud Monitoring/Logging; mock only with DEV_MODE=1
    monitor.metrics(AppTarget(app="tracker", project_id="shipcube-apps-ks", region="asia-south1",
                              service="tracker"), window="1h")
    monitor.logs(target, limit=50, severity="WARNING", page_token=None)
"""

from __future__ import annotations

import os

from .alerts import Rule, evaluate, notify
from .errors import MonitorError, MonitorNotFound, MonitorPermissionDenied, MonitorUnavailable
from .mock import MockMonitor
from .types import AiUsage, AppTarget, MonitorProvider, NoUsage, UsageSource

__all__ = [
    "AiUsage", "AppTarget", "MockMonitor", "MonitorError", "MonitorNotFound", "MonitorPermissionDenied",
    "MonitorProvider", "MonitorUnavailable", "NoUsage", "Rule", "UsageSource", "evaluate", "notify",
    "provider_from_env",
]


def provider_from_env(usage: UsageSource | None = None) -> MonitorProvider:
    """The real provider, unless SHIPCUBE_MONITOR=mock in an explicit local DEV_MODE=1.

    Production never returns mock data: asking for the mock without DEV_MODE is an error, not a
    silent fallback.
    """
    mode = os.environ.get("SHIPCUBE_MONITOR", "gcp")
    if mode == "gcp":
        from .gcp import GcpMonitor

        return GcpMonitor(usage=usage)
    if mode == "mock":
        if os.environ.get("DEV_MODE") != "1":
            raise RuntimeError("SHIPCUBE_MONITOR=mock is only allowed with DEV_MODE=1 (local development)")
        return MockMonitor()
    raise ValueError(f"SHIPCUBE_MONITOR must be gcp or mock, not {mode!r}")
