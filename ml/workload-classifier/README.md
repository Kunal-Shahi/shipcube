# shipcube-classifier

Workload classifier behind `estimate_load`: the shared feature space (`FEATURES`), weak labels built from public cluster traces, and a LightGBM model exported to `src/shipcube_classifier/artefacts/model.json` for pure-Python inference (no LightGBM needed at runtime).

```python
from shipcube_classifier import classify
classify({"cpu_mean_cores": 0.9, "mem_peak_gb": 0.4, "duration_s": 600}, kind="script")
```

Retrain (raw data goes to `data/`, which is gitignored; macOS needs `brew install libomp`):

```bash
pip install -e ".[train,test]"
python scripts/sample_traces.py    # streams small samples; BigQuery part needs gcloud auth
python scripts/build_dataset.py
python scripts/train.py            # writes artefacts/model.json and metrics.json
```

Method, datasets, licences and metrics: [docs/research/load-estimation.md](../../docs/research/load-estimation.md).
