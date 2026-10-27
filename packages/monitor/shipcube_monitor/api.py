"""Adapter from the control-plane API's Telemetry interface (services/api/shipcube_api/telemetry.py)
to this package. The API installs it at startup when shipcube-monitor is importable.

The API passes its app view plus `gcp_project`, `service_name` and `app_id` (store.get_app_for_telemetry).
"""

from __future__ import annotations

from typing import Any

from .errors import MonitorUnavailable
from .types import AppTarget, MonitorProvider


def _mib(value: Any) -> int:
    s = str(value or "512Mi")
    if s.endswith("Gi"):
        return int(float(s[:-2]) * 1024)
    if s.endswith("Mi"):
        return int(float(s[:-2]))
    return int(float(s) / (1024 * 1024))


def target_for(app: dict[str, Any]) -> AppTarget:
    caps = app.get("caps") or {}
    return AppTarget(
        app=app["name"], project_id=app["gcp_project"], region=app["region"], service=app["service_name"],
        app_id=app.get("app_id"), cpu=float(caps.get("cpu") or 1), memory_mib=_mib(caps.get("memory")),
        caps=caps,
    )


class MonitorTelemetry:
    def __init__(self, provider: MonitorProvider, unavailable: type[Exception]) -> None:
        self._p = provider
        self._unavailable = unavailable  # the API's TelemetryUnavailable, so it answers 503-style errors

    def logs(self, app: dict[str, Any], limit: int, severity: str | None, page_token: str | None) -> dict[str, Any]:
        try:
            return self._p.logs(target_for(app), limit=limit, severity=severity, page_token=page_token)
        except MonitorUnavailable as e:
            raise self._unavailable(str(e)) from e

    def metrics(self, app: dict[str, Any], window: str) -> dict[str, Any]:
        try:
            return self._p.metrics(target_for(app), window)  # type: ignore[arg-type]
        except MonitorUnavailable as e:
            raise self._unavailable(str(e)) from e
