"""Structured JSON logs with request, operation and trace ids.

Cloud Logging parses one JSON object per line: `severity` and `message` map to its fields, and
`logging.googleapis.com/trace` links the line to a Cloud Trace span when the request carried a
W3C `traceparent` or `X-Cloud-Trace-Context` header.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import sys
import time
import uuid
from typing import Any

request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
operation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("operation_id", default=None)
trace_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("trace_id", default=None)

_STD = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
        }
        for key, var in (("request_id", request_id), ("operation_id", operation_id)):
            if (v := var.get()) is not None:
                out[key] = v
        if (t := trace_id.get()) and (project := os.environ.get("SHIPCUBE_CONTROL_PROJECT")):
            out["logging.googleapis.com/trace"] = f"projects/{project}/traces/{t}"
        out.update({k: v for k, v in record.__dict__.items() if k not in _STD and not k.startswith("_")})
        if record.exc_info:
            out["exception"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def setup_logging() -> None:
    if getattr(setup_logging, "_done", False):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(os.environ.get("SHIPCUBE_LOG_LEVEL", "INFO"))
    for noisy in ("httpx", "httpcore", "urllib3", "google.auth"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    setup_logging._done = True  # type: ignore[attr-defined]


def trace_from_headers(headers: Any) -> str | None:
    if tp := headers.get("traceparent"):  # 00-<trace-id>-<span-id>-<flags>
        parts = tp.split("-")
        if len(parts) == 4 and len(parts[1]) == 32:
            return parts[1]
    if xc := headers.get("x-cloud-trace-context"):
        return xc.split("/")[0] or None
    return None


def new_request_id(headers: Any) -> str:
    rid = headers.get("x-request-id")
    return rid if rid and len(rid) <= 64 else uuid.uuid4().hex
