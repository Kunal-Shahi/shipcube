# shipcube-placement

`estimate(source, hints) -> EstimateResult`, the engine behind the MCP tool `estimate_load`. It:
1. profiles the code (`shipcube-profiler`)
2. classifies it with a confidence gate and a rule fallback (`shipcube-classifier`)
3. picks a tier and target
4. sizes it
5. prices it from Cloud Run / GKE list prices (`pricing.json`)
6. recommends caps
7. has Muse Spark explain the result, with timeouts and retries, and a rule-based fallback. Muse never sees user code, file names, route paths, hostnames or app output, only derived facts (`explain.muse_facts`); see docs/research/load-estimation.md §6

## Run

```python
from shipcube_classifier.obs import configure_logging
from shipcube_placement import estimate
configure_logging()                                   # once per process: JSON logs for Cloud Logging
r = estimate({"kind": "inline", "files": [...]}, {"expected_users": 25},
             operation_id="op_123", request_id="req_9", traceparent=request.headers.get("traceparent"))
r.model_dump()   # estimate_id, workload_class, confidence, tier, target, resources, monthly_cost_usd, profile,
                 # recommended_caps, reasoning, alternatives, explained_by, decided_by, low_confidence, model_version
```

```bash
uv pip install -e "../../ml/workload-classifier[gcp]" -e ../profiler -e ".[test]"
python scripts/demo.py --logs     # DEV_MODE, local profiling; Muse if META_API_KEY is set (.env), else rules
pytest                            # SHIPCUBE_LIVE_MUSE=1 also calls the real Muse API
```

## Configure

| Variable | Purpose |
|---|---|
| `META_API_KEY` | From Secret Manager in production |
| `META_BASE_URL` | Muse endpoint |
| `SHIPCUBE_EXPLAINER_MODEL` | Default `muse-spark-1.3` |
| `SHIPCUBE_MUSE_TIMEOUT_S` | Per-attempt timeout, default 30 |
| `SHIPCUBE_MUSE_DEADLINE_S` | Overall deadline, default 60 |

The profiler and the model also need their own variables; see their READMEs.

## Deploy and operate

- **Install** the three packages into the API worker image.
- **Metrics:** create log-based metrics on `estimate.completed`, `muse.call`, `explainer.fallback` and `profile.failed`.
- **Alerts:** alert on the fallback rates.

All of this is in [docs/research/load-estimation.md](../../docs/research/load-estimation.md) §9.
