"""Integration test against real Cloud Monitoring and Cloud Logging.

Runs only when SHIPCUBE_IT_PROJECT is set (CI runs it through Workload Identity Federation on main;
locally use `gcloud auth application-default login`). It reads one existing Cloud Run service:

    SHIPCUBE_IT_PROJECT=shipcube-control-ks SHIPCUBE_IT_SERVICE=budget-killswitch \\
      uv run --extra gcp pytest tests/test_integration_gcp.py
"""

from __future__ import annotations

import os

import pytest

PROJECT = os.environ.get("SHIPCUBE_IT_PROJECT")
pytestmark = pytest.mark.skipif(not PROJECT, reason="set SHIPCUBE_IT_PROJECT to run against real GCP")


def test_real_metrics_and_logs():
    from shipcube_monitor import AppTarget
    from shipcube_monitor.gcp import GcpMonitor

    t = AppTarget(app="it", project_id=PROJECT, region=os.environ.get("SHIPCUBE_IT_REGION", "asia-south1"),
                  service=os.environ.get("SHIPCUBE_IT_SERVICE", "budget-killswitch"))
    mon = GcpMonitor()
    m = mon.metrics(t, "30d")
    assert m["requests"] >= 0 and m["cost_usd"]["compute"] >= 0
    page = mon.logs(t, limit=3)
    assert len(page["entries"]) <= 3
    for e in page["entries"]:
        assert e["timestamp"] and e["severity"]
