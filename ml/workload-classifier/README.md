# shipcube-classifier

The workload classifier behind `estimate_load`. It provides:
- the shared feature space (`FEATURES`)
- physical-threshold labelling rules (`rules.py`), also used as the runtime fallback
- a LightGBM model trained on public cluster traces, served in pure Python from a versioned, checksum-verified artefact (`registry.py`)
- the shared GCP REST client (`gcp.py`)
- structured logging (`obs.py`)

## Run

```python
from shipcube_classifier import classify, load_model, rule_class
lm = load_model()                       # SHIPCUBE_MODEL_URI (bundled by default), sha256-verified
p = classify({"cpu_mean_cores": 0.9, "mem_peak_gb": 0.4, "duration_s": 600}, kind="script")
p.workload_class, p.confidence, p.low_confidence, p.model_version
```

## Configure

| Variable | Purpose |
|---|---|
| `SHIPCUBE_MODEL_URI` | `bundled` (default) or `gs://<bucket>/models/workload-classifier/<version or latest>` |
| `SHIPCUBE_MODEL_SHA256` | Optional pin |
| `SHIPCUBE_MODEL_CACHE` | Local cache |
| `SHIPCUBE_LOG_LEVEL` | Log level |

## Retrain and publish

Raw data goes to `data/`, which is gitignored. macOS needs `brew install libomp`.

```bash
pip install -e ".[train,gcp,test]"
python scripts/sample_traces.py                      # streams small trace samples (BigQuery part needs gcloud auth)
python scripts/build_dataset.py
SHIPCUBE_DEV_MODE=1 python scripts/calibrate.py      # optional: re-profile the dev suite
python scripts/train.py                              # writes artefacts/{model,manifest,metrics}.json
python scripts/publish_model.py gs://<bucket>/models/workload-classifier --latest
```

## Operate

- **Every estimate carries `model_version`.** Startup logs `model.loaded` with the version and sha256.
- **A bad or unreachable GCS model raises `ModelLoadError`.** It never silently falls back to the bundled copy.
- **To roll back,** point `SHIPCUBE_MODEL_URI` at the previous version; published versions are immutable.

Method, datasets, licences, metrics and limits are in [docs/research/load-estimation.md](../../docs/research/load-estimation.md). The runbook is in §9.
