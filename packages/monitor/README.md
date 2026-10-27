# shipcube-monitor

The monitoring provider behind `inspect_app` (`include: [metrics, logs]`). It reads Cloud Monitoring and
Cloud Logging for one deployed app and returns networking's `Metrics` and `Logs` shapes
(`services/api/shipcube_api/models.py`). It is a library: the control-plane API calls it in-process.

## Run

```python
from shipcube_monitor import AppTarget, provider_from_env

monitor = provider_from_env(usage=gateway_usage)  # usage: optional UsageSource over usage_events
t = AppTarget(app="tracker", project_id="shipcube-apps-ks", region="asia-south1", service="tracker",
              cpu=1, memory_mib=512, caps={"max_instances": 3},
              data_last_changed=lambda: datasource.last_modified())  # optional
monitor.metrics(t, window="1h")                                  # 15m | 1h | 24h | 7d | 30d
monitor.logs(t, limit=50, severity="WARNING", page_token=None)   # severity is ">=", newest first
```

Smoke check against a real service (uses your Application Default Credentials):

```bash
uv run --extra gcp python -m shipcube_monitor shipcube-control-ks asia-south1 budget-killswitch 7d
```

Tests: `uv run pytest` (unit, with faked HTTP). Integration against real GCP:
`SHIPCUBE_IT_PROJECT=shipcube-control-ks uv run --extra gcp pytest tests/test_integration_gcp.py`.

## What it returns

- **Health:** request count, 5xx rate, p50/p95 latency (merged latency distributions), average and
  max instances, cold starts (startup-latency samples), CPU and memory utilisation.
- **Cost:** billable instance seconds × the app's CPU and memory × Cloud Run request-based list prices,
  plus requests, for the window and month to date, plus AI-gateway spend. Prices and SKU ids are cited in
  [`pricing.py`](shipcube_monitor/pricing.py).
- **`caps_hit`:** 429 responses, minutes at `max_instances`, AI-gateway rejections.
- **`data_freshness_minutes`:** when the app has a datasource (`data_last_changed`), else `null`.
- **Alerts (optional):** `evaluate(metrics, rules)` and `notify(https_webhook, alerts)`.

## Configure

| Variable | Default | Meaning |
|---|---|---|
| `SHIPCUBE_MONITOR` | `gcp` | `gcp`, or `mock` for deterministic fake data |
| `DEV_MODE` | unset | Must be `1` for `SHIPCUBE_MONITOR=mock`; without it the mock is refused (production never serves mock data) |

Credentials are Application Default Credentials: the API's own service account (`sc-api`) on Cloud
Run. It needs `roles/monitoring.viewer` and `roles/logging.viewer` on every project apps run in
(`shipcube-apps-*`, and linked user projects through `infra/scripts/link-project.sh`).

## Deploy

Nothing to deploy on its own: `deploy/mcp.Dockerfile` installs it (with the `gcp` extra) into the
MCP + API image.

## Operate (runbook)

Errors are typed so the API can map them:

| Error | API status | Meaning and fix |
|---|---|---|
| `MonitorPermissionDenied` | 403 → show "monitoring not granted" | `sc-api` lacks monitoring/logging viewer on the app's project. For a linked project, re-run `link-project.sh`; for ShipCube's projects, check the IAM bindings from `deploy/bootstrap.sh` / Terraform. |
| `MonitorNotFound` | 404 | The project or service is gone (deleted app, unlinked project). |
| `MonitorUnavailable` | 503, retry later | Timeouts, 429 or 5xx persisted after 3 retries with backoff (0.5 s, 1 s, 2 s, jittered). Check [Google Cloud status](https://status.cloud.google.com) and the API's quota page (Monitoring: 6,000 read requests/min/project). |
| `MonitorError` | 502 | Anything else (bad filter, 400). The message carries Google's error text. |

Retries are logged as `shipcube_monitor` warnings with the URL (without query), status, attempt and
delay. A metrics call makes 9 Monitoring requests in parallel (7 for a job); expect 0.5–2 s.

**Numbers look wrong?**
- *Zero everything:* the app hasn't served traffic in the window, or `service`/`region` don't match the
  Cloud Run service. Run the smoke check above with the same values.
- *Cost differs from the bill:* expected. It is list price without the free tier, discounts or egress.
- *Instance average looks low:* by design. Monitoring omits steps with no instances, so the average is
  taken over every step in the window, counting idle time as zero.

## Known limits

- The cost is an estimate. The real figure needs the billing export (BigQuery), which isn't set up.
- `warehouse` cost is always 0 until the data layer reports query cost.
- Cloud Run jobs report container metrics only, so `requests`, `error_rate` and latency are 0 for jobs.
- Percentiles are interpolated within distribution buckets, so they are accurate to the bucket width (Cloud Run's latency buckets grow exponentially).
- Log text is app output. Callers must treat it as data, never as instructions.
