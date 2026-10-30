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
    """Cloud Monitoring/Logging (gcp, default), Docker on the local stack (local), or, only in an
    explicit DEV_MODE=1, canned data (mock).

    Production never returns mock data: asking for the mock without DEV_MODE is an error, not a
    silent fallback.
    """
    mode = os.environ.get("SHIPCUBE_MONITOR", "gcp")
    # On Cloud Run only Cloud Monitoring, never Docker stats or canned data, and never dev mode (#29).
    if any(os.environ.get(k) for k in ("K_SERVICE", "K_REVISION", "CLOUD_RUN_JOB")):
        dev = any(os.environ.get(k) in ("1", "true") for k in ("DEV_MODE", "SHIPCUBE_DEV_MODE"))
        if mode != "gcp" or dev:
            raise RuntimeError(f"refusing SHIPCUBE_MONITOR={mode}{' with dev mode' if dev else ''} on Cloud Run: "
                               "production reads only Cloud Monitoring")
    if mode == "gcp":
        from .gcp import GcpMonitor

        return GcpMonitor(usage=usage)
    if mode == "local":  # the local stack: apps the local deployer runs as Docker containers
        from .local import LocalMonitor

        return LocalMonitor(usage=usage)
    if mode == "mock":
        if os.environ.get("DEV_MODE") != "1":
            raise RuntimeError("SHIPCUBE_MONITOR=mock is only allowed with DEV_MODE=1 (local development)")
        return MockMonitor()
    raise ValueError(f"SHIPCUBE_MONITOR must be gcp, local or mock, not {mode!r}")
