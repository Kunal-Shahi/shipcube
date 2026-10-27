"""Typed errors, so the API can map them to HTTP statuses (403, 404, 503) instead of a generic 500."""

from __future__ import annotations


class MonitorError(RuntimeError):
    """A Cloud Monitoring or Cloud Logging call failed."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class MonitorPermissionDenied(MonitorError):
    """The caller's service account lacks monitoring.viewer / logging.viewer on the app's project."""


class MonitorNotFound(MonitorError):
    """The project or resource doesn't exist."""


class MonitorUnavailable(MonitorError):
    """Timeouts, 429 or 5xx that persisted after retries. Safe to retry later."""
