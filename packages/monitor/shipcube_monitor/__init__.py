"""ShipCube monitoring provider behind `inspect_app` (metrics and logs).

Usage from the API:

    from shipcube_monitor import AppTarget, provider_from_env
    monitor = provider_from_env()          # SHIPCUBE_MONITOR=mock (default) or gcp
    monitor.metrics(AppTarget(app="tracker", project_id="shipcube-apps-ks", region="asia-south1",
                              service="tracker"), window="1h")
    monitor.logs(target, limit=50, severity="WARNING", page_token=None)
"""

from __future__ import annotations

import os

from .alerts import Rule, evaluate, notify
from .mock import MockMonitor
from .types import AiUsage, AppTarget, MonitorProvider, NoUsage, UsageSource

__all__ = [
    "AiUsage", "AppTarget", "MockMonitor", "MonitorProvider", "NoUsage", "Rule", "UsageSource",
    "evaluate", "notify", "provider_from_env",
]


def provider_from_env(usage: UsageSource | None = None) -> MonitorProvider:
    mode = os.environ.get("SHIPCUBE_MONITOR", "mock")
    if mode == "gcp":
        from .gcp import GcpMonitor

        return GcpMonitor(usage=usage)
    if mode == "mock":
        return MockMonitor()
    raise ValueError(f"SHIPCUBE_MONITOR must be mock or gcp, not {mode!r}")
