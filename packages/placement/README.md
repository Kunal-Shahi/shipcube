# shipcube-placement

`estimate(source, hints) -> EstimateResult`, the engine behind the MCP tool `estimate_load`. It profiles the code, classifies it, picks a tier and target, sizes it, prices it from Cloud Run / GKE list prices (`pricing.json`), recommends caps, and has Muse Spark explain the result (rule-based fallback).

```python
from shipcube_placement import estimate
r = estimate({"kind": "inline", "files": [{"path": "app.py", "content": "..."}]}, {"expected_users": 25})
r.model_dump()   # estimate_id, workload_class, confidence, tier, target, resources, monthly_cost_usd,
                 # profile, recommended_caps, reasoning, alternatives, explained_by
```

```bash
uv pip install -e ../../ml/workload-classifier -e ../profiler -e ".[test]"
python scripts/demo.py            # Muse Spark if META_API_KEY is set (.env), else rules
pytest                            # SHIPCUBE_LIVE_MUSE=1 also calls the real API
```

See [docs/research/load-estimation.md](../../docs/research/load-estimation.md).
