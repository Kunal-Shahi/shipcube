"""Inputs and outputs of the monitoring provider.

The output dicts match networking's `Metrics` and `Logs` models (services/api/shipcube_api/models.py),
so the API can return them as they are.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal, Protocol, TypedDict

Window = Literal["15m", "1h", "24h", "7d", "30d"]

WINDOWS: dict[str, timedelta] = {
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}

SEVERITIES = ["DEFAULT", "DEBUG", "INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL", "ALERT", "EMERGENCY"]


@dataclass(frozen=True)
class AppTarget:
    """One deployed app, as the API knows it from the `apps` and `deployments` rows."""

    app: str  # slug, as shown to the agent
    project_id: str  # where it runs: ShipCube's apps project or the user's linked project
    region: str
    service: str  # Cloud Run service (or job) name
    kind: Literal["service", "job"] = "service"
    app_id: str | None = None
    # Requested resources, for utilisation context and the cost estimate.
    cpu: float = 1.0
    memory_mib: int = 512
    caps: dict[str, Any] = field(default_factory=dict)
    # Returns when the app's source data last changed, or None when it has no datasource.
    data_last_changed: Callable[[], datetime | None] | None = None


class AiUsage(TypedDict):
    input_tokens: int
    output_tokens: int
    cost_usd: float
    rejected_requests: int  # 429s from the AI gateway's caps


class UsageSource(Protocol):
    """Gateway usage for one app (from `usage_events` where source = 'gateway')."""

    def ai_usage(self, app_id: str | None, start: datetime, end: datetime) -> AiUsage: ...


class NoUsage:
    def ai_usage(self, app_id: str | None, start: datetime, end: datetime) -> AiUsage:
        return {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "rejected_requests": 0}


class MonitorProvider(Protocol):
    def metrics(self, target: AppTarget, window: Window = "1h") -> dict[str, Any]: ...

    def logs(
        self,
        target: AppTarget,
        limit: int = 50,
        severity: str | None = None,
        page_token: str | None = None,
    ) -> dict[str, Any]: ...


def severity_at_least(severity: str | None) -> list[str]:
    """Severities at or above `severity` (Cloud Logging's `severity>=X` semantics)."""
    if not severity:
        return SEVERITIES
    s = severity.upper()
    if s not in SEVERITIES:
        raise ValueError(f"unknown severity {severity!r}")
    return SEVERITIES[SEVERITIES.index(s):]
