"""Structured logging for the load-estimation packages.

Libraries only emit records; the host process (MCP server, API worker, CLI) calls
`configure_logging()` once. Records are JSON lines that Cloud Logging parses on Cloud
Run: `severity`, `message`, `time`, plus `logging.googleapis.com/trace` when a W3C
traceparent was passed in, and the request-scoped ids below.

Request scope (contextvars, so concurrent estimates in one process don't mix):
  estimate_id, operation_id, request_id, trace (projects/<p>/traces/<id>)

Metrics are log-based: every `estimate.completed`, `profile.completed`, `muse.call`
and `model.loaded` event carries numeric fields (duration_ms, attempts, ...) that
Cloud Logging log-based metrics turn into distributions and counters
(docs/research/load-estimation.md §9 lists them).
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone

_ctx: contextvars.ContextVar[dict] = contextvars.ContextVar("shipcube_log_ctx", default={})
_TRACEPARENT = re.compile(r"^[0-9a-f]{2}-([0-9a-f]{32})-([0-9a-f]{16})-[0-9a-f]{2}$")


@contextlib.contextmanager
def scope(**fields):
    """Add request-scoped fields (estimate_id, operation_id, request_id, trace) to logs."""
    fields = {k: v for k, v in fields.items() if v is not None}
    tp = fields.pop("traceparent", None)
    if tp and (m := _TRACEPARENT.match(tp)):
        project = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCP_PROJECT_ID")
        fields["logging.googleapis.com/trace"] = f"projects/{project}/traces/{m.group(1)}" if project else m.group(1)
        fields["logging.googleapis.com/spanId"] = m.group(2)
    token = _ctx.set({**_ctx.get(), **fields})
    try:
        yield
    finally:
        _ctx.reset(token)


def context() -> dict:
    return dict(_ctx.get())


def event(logger: logging.Logger, name: str, level: int = logging.INFO, **fields) -> None:
    logger.log(level, name, extra={"fields": {"event": name, **fields}})


@contextlib.contextmanager
def timed(logger: logging.Logger, name: str, **fields):
    """Log `<name>` with duration_ms and outcome (ok / error type) when the block ends."""
    t = time.perf_counter()
    extra: dict = {}
    try:
        yield extra
    except Exception as e:
        event(logger, name, logging.WARNING, outcome="error", error_type=type(e).__name__,
              duration_ms=round((time.perf_counter() - t) * 1000, 1), **fields, **extra)
        raise
    event(logger, name, outcome="ok", duration_ms=round((time.perf_counter() - t) * 1000, 1), **fields, **extra)


class JsonFormatter(logging.Formatter):
    SEVERITY = {logging.DEBUG: "DEBUG", logging.INFO: "INFO", logging.WARNING: "WARNING",
                logging.ERROR: "ERROR", logging.CRITICAL: "CRITICAL"}

    def format(self, record: logging.LogRecord) -> str:
        out = {
            "severity": self.SEVERITY.get(record.levelno, "DEFAULT"),
            "message": record.getMessage(),
            "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "logger": record.name,
            **_ctx.get(),
            **getattr(record, "fields", {}),
        }
        if record.exc_info:
            out["exception"] = self.formatException(record.exc_info)[-2000:]
        return json.dumps(out, default=str)


def configure_logging(level: str | int | None = None, stream=None) -> None:
    """Install the JSON formatter on the `shipcube` logger (idempotent)."""
    logger = logging.getLogger("shipcube")
    logger.setLevel(level or os.environ.get("SHIPCUBE_LOG_LEVEL", "INFO"))
    for h in list(logger.handlers):
        if getattr(h, "_shipcube", False):
            logger.removeHandler(h)
    h = logging.StreamHandler(stream or sys.stderr)
    h.setFormatter(JsonFormatter())
    h._shipcube = True  # type: ignore[attr-defined]
    logger.addHandler(h)
    logger.propagate = False
