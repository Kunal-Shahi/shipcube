# shipcube-monitor

The monitoring provider behind `inspect_app` (`include: [metrics, logs]`). Output matches networking's
`Metrics` and `Logs` models in `services/api/shipcube_api/models.py`.

| Mode | Set | Reads |
|---|---|---|
| `mock` (default) | `SHIPCUBE_MONITOR=mock` | Deterministic numbers per app and window; no network |
| `gcp` | `SHIPCUBE_MONITOR=gcp` | Cloud Monitoring + Cloud Logging REST, with Application Default Credentials |

```python
from shipcube_monitor import AppTarget, provider_from_env

monitor = provider_from_env(usage=gateway_usage)  # usage: optional UsageSource over usage_events
t = AppTarget(app="tracker", project_id="shipcube-apps-ks", region="asia-south1", service="tracker",
              cpu=1, memory_mib=512, caps={"max_instances": 3},
              data_last_changed=lambda: datasource.last_modified())  # optional
monitor.metrics(t, window="1h")                       # 15m | 1h | 24h | 7d | 30d
monitor.logs(t, limit=50, severity="WARNING", page_token=None)  # severity is ">=", newest first
```

**Metrics:** request count, 5xx rate, p50/p95 latency (from merged latency distributions), instance count,
cold starts (startup-latency samples), CPU and memory utilisation, `caps_hit` (429s, minutes at
`max_instances`, AI-gateway rejections) and `data_freshness_minutes` when the app has a datasource.

**Cost:** billable instance seconds × the app's CPU and memory × Cloud Run request-based list prices,
plus requests. Prices and SKU ids are cited in [`pricing.py`](shipcube_monitor/pricing.py). It's an
estimate (no free tier, no discounts); the billing export is the real number.

**Alerts (optional):** `evaluate(metrics, rules)` and `notify(webhook_url, alerts)`.

**Permissions:** the caller needs `roles/monitoring.viewer` and `roles/logging.viewer` on the app's project
(the hosted deployer has both; linked projects grant them in `link-project.sh`).

Smoke check against a real service:

```bash
SHIPCUBE_MONITOR=gcp uv run --extra gcp python -m shipcube_monitor shipcube-control-ks asia-south1 budget-killswitch 7d
```
