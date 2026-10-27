"""Threshold alerts on a metrics result, sent to a webhook.

Rules are simple: a dotted path into the metrics dict, an operator and a threshold. Email goes through
whatever the webhook is (for example a Cloud Function or a Slack/Discord incoming webhook); ShipCube
does not run a mail server.
"""

from __future__ import annotations

import operator
from dataclasses import dataclass
from typing import Any

import httpx

OPS = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le}

DEFAULT_RULES = [
    ("error_rate", ">", 0.05),
    ("latency_ms.p95", ">", 2000),
    ("memory_utilization", ">", 0.9),
    ("data_freshness_minutes", ">", 24 * 60),
]


@dataclass(frozen=True)
class Rule:
    metric: str
    op: str
    threshold: float

    def value(self, metrics: dict[str, Any]) -> float | None:
        v: Any = metrics
        for part in self.metric.split("."):
            if not isinstance(v, dict) or part not in v:
                return None
            v = v[part]
        return None if v is None else float(v)


def evaluate(metrics: dict[str, Any], rules: list[Rule] | None = None) -> list[dict[str, Any]]:
    rules = rules if rules is not None else [Rule(*r) for r in DEFAULT_RULES]
    fired = []
    for r in rules:
        v = r.value(metrics)
        if v is not None and OPS[r.op](v, r.threshold):
            fired.append({"app": metrics.get("app"), "metric": r.metric, "value": v, "op": r.op,
                          "threshold": r.threshold, "window": metrics.get("window")})
    return fired


def notify(webhook_url: str, alerts: list[dict[str, Any]], client: httpx.Client | None = None) -> int:
    """POST the fired alerts as JSON. Returns how many were sent. The URL must be https."""
    if not alerts:
        return 0
    if not webhook_url.startswith("https://"):
        raise ValueError("alert webhook must be https")
    text = "\n".join(f"[ShipCube] {a['app']}: {a['metric']} = {a['value']:g} ({a['op']} {a['threshold']:g})"
                     for a in alerts)
    http = client or httpx.Client(timeout=10)
    http.post(webhook_url, json={"text": text, "alerts": alerts}).raise_for_status()
    return len(alerts)
