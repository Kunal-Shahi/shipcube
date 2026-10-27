# Load estimation (`estimate_load`)

Owner: load-estimation workstream. Code: `ml/workload-classifier/`, `packages/profiler/`, `packages/placement/`.

`estimate_load` answers four questions about an app a coding agent wants to deploy:

1. What kind of workload is it?
2. Which tier should it run on?
3. How big should each instance be?
4. Roughly what will it cost per month?

It also returns caps and a short explanation. The **classifier and placement rules produce every number**. **Muse Spark writes the text** (`reasoning`, alternative trade-offs) and proposes `recommended_caps` within bounds. If Muse is unavailable, rule-based text is used instead.

```
source ─► materialize ─► summarize (static) ─► profile (run on sample input) ─► features
                                                     │ (can't run → static profile)
features ─► classifier (LightGBM, pure-Python inference) × structural prior ─► class, confidence
class + profile + hints ─► tier/target ─► resources ─► monthly cost range ─► rule caps, alternatives
facts ─► Muse Spark (reasoning, trade-offs, caps within bounds) ─► EstimateResult   (fallback: rules)
```

## 1. Interface

```python
from shipcube_placement import estimate
r = estimate(source, hints)        # EstimateResult (pydantic)
r.model_dump()                     # -> the estimate_load operation result
```

- `source` is the control plane's `Source` dict: `inline`, `git`, `upload` (needs a `fetch_upload(upload_id) -> tarball path` callable, which reads GCS in production) or `image` (static estimate only).
- `hints` is `{expected_users, schedule, region, sample_input}`. All are optional.
- Keyword overrides: `runner=` (`LocalRunner` by default; `CloudRunJobRunner` in production), `explainer=` (`FallbackExplainer` by default; `RuleExplainer` for deterministic output).

**Output** (networking PR #1 §2.1, plus `explained_by` from the Oct 28 decision):
`estimate_id, workload_class, confidence, tier, target, resources, monthly_cost_usd{low, expected, high}, profile, recommended_caps, reasoning, alternatives[], explained_by`.

- `target` is one of `cloud-run-service`, `cloud-run-job`, `cloud-run-gpu`, `gke-autopilot` or `static-snapshot`.
- `resources` holds `{cpu, memory, min_instances, max_instances, concurrency, timeout_s, gpu, schedule}`.
- `recommended_caps` uses exactly the control plane's `Caps` fields and bounds.
- `alternatives[]` holds `{tier, target, monthly_cost_usd, tradeoff}`.
- `profile` carries:
  - the raw measurements
  - the feature vector
  - class probabilities, both after the structural prior and raw
  - the code summary
  - `cost_basis`: which prices were used, and that the free tier isn't subtracted
  - every assumption made
  - `explainer_error` when Muse wasn't used

**Install** (Python 3.12):
```bash
pip install "shipcube-classifier @ git+https://github.com/Kunal-Shahi/shipcube@main#subdirectory=ml/workload-classifier" \
            "shipcube-profiler @ git+https://github.com/Kunal-Shahi/shipcube@main#subdirectory=packages/profiler" \
            "shipcube-placement @ git+https://github.com/Kunal-Shahi/shipcube@main#subdirectory=packages/placement"
```
For a local checkout, use `uv pip install -e ml/workload-classifier -e packages/profiler -e "packages/placement[test]"`. Runtime dependencies are only `psutil`, `pydantic`, `openai` and `python-dotenv`. The classifier runs without LightGBM or numpy (see §3.4).

## 2. Data

### 2.1 Sources and licences

| Trace | What we take | How it's sampled | Licence |
|---|---|---|---|
| [Azure Functions 2019](https://github.com/Azure/AzurePublicDataset/blob/master/AzureFunctionsDataset2019.md) | Day 1: per-function invocations per minute (1,440 columns), trigger type, duration percentiles, per-app memory percentiles | Streams the 143 MB release `.tar.xz` and stops after the day-1 files; reservoir sample of 12,000 functions that had at least one call | CC BY 4.0 |
| [Azure VM 2019 (V2)](https://github.com/Azure/AzurePublicDataset/blob/master/AzurePublicDatasetV2.md) `vmtable` | Per-VM avg/max/p95 CPU, core and memory bucket, lifetime, `vmcategory` | First 400,000 rows of the gzip stream, reservoir sample of 20,000 | CC BY 4.0 |
| [Alibaba cluster-trace-v2018](https://github.com/alibaba/clusterdata/tree/master/cluster-trace-v2018) | `batch_instance` (offline: CPU and memory avg/max, start/end); `container_meta` and `container_usage` (online: CPU and memory utilisation, disk I/O %, net in/out) | First 1.5 M `batch_instance` rows and 3 M `container_usage` rows of each `.tar.gz` stream (~50 MB downloaded; the full files are 21 GB and 29 GB) | No licence file. Published by Alibaba "to help researchers"; we use derived aggregates for training only and redistribute nothing. Cite the trace. |
| [Google cluster-data 2019](https://github.com/google/cluster-data/blob/master/ClusterData2019.md), cell `a`, BigQuery | `instance_usage` (avg/max CPU and memory per 5-minute window) aggregated per instance, joined to `collection_events` (priority, scheduling class) | `TABLESAMPLE SYSTEM (0.05 PERCENT)`, at most 30,000 instances | CC BY 4.0 |

- **Raw data is never committed.** `ml/workload-classifier/data/` is gitignored.
- `scripts/sample_traces.py` re-downloads the samples, about 250 MB on disk in total.
- **Cost:** the BigQuery job billed **0.8 GB**, which is free under BigQuery's 1 TB/month.
  - `bq` checks `--maximum_bytes_billed` against a worst-case full-column scan (~400 GB) *before* the job runs, so the cap is set high as a guard. Dry runs underestimate it.
  - Each extra column adds about 90 GB to that worst case, so the query reads only five columns.

### 2.2 Unit alignment

The traces normalise resources differently, so we map each one onto common units. The mapping is approximate, which is one reason the labels use within-source quantiles:

- **Borg:** 1.0 NCU = 64 vCPU and 1.0 NMU = 256 GiB (assumed largest machine).
- **Alibaba:**
  - CPU: 100 = 1 core.
  - Memory: 100 = one machine, assumed 256 GiB.
  - `disk_io_percent` 100 = 200 MiB/s; `net_in/out` 100 = 10 Gbit/s.
- **Azure VMs:** the buckets are *allocations* (">24" cores → 32, ">64" GB → 96), not usage.
- **CPU utilisation** with no allocation in the trace is measured against `max(1, ceil(p95))` cores. The profiler uses the same rule.

### 2.3 Features (`shipcube_classifier.FEATURES`)

| Feature | Meaning |
|---|---|
| `cpu_mean_cores`, `cpu_p95_cores` | Resource level |
| `cpu_util_mean` | Mean over allocation |
| `cpu_cv`, `cpu_peak_to_mean` | Variance and burstiness of CPU |
| `mem_mean_gb`, `mem_peak_gb` | Memory level |
| `mem_per_core_gb` | Memory/CPU ratio |
| `io_mb_per_s`, `net_mb_per_s` | I/O while active |
| `io_per_cpu_s` | I/O per CPU-second; high means it waits on I/O |
| `invocations_per_hour` | Invocation frequency |
| `invocation_cv`, `idle_fraction` | Invocation burstiness |
| `duration_s` | Seconds per invocation |

A feature a source can't observe is NaN, and LightGBM handles NaN natively. Most sources lack some features:
- Azure Functions has no CPU.
- VMs and Borg have no invocation data.
- Only Alibaba online containers have I/O.

### 2.4 Labels: weak supervision (`weak_labels.py`)

**No public trace contains workload classes, so the labels are generated and the scores below measure agreement with these rules, not ground truth.** Each source has one coarse *side signal* that is *not* in the feature vector:

| Source | Side signal → `kind` |
|---|---|
| Azure Functions | trigger: `timer` → batch; `http`, `orchestration` → interactive; `queue`, `event`, `storage` → io_event |
| Azure VMs | `vmcategory`: Delay-insensitive → batch; Interactive → interactive; Unknown → only resource rules apply |
| Alibaba | `batch_instance` → batch; online containers → interactive |
| Borg | priority ≤115 (free and best-effort batch) → batch; 120–359 (production) and ≥360 (monitoring) → interactive |

The labelling functions are applied in order, with thresholds as **within-source quantiles** because units differ between traces:

1. `memory_heavy`: `mem_per_core_gb` ≥ max(q88, 4 GiB/core) and CPU utilisation ≤ 50%. If the source has no CPU data, top 8% of memory instead.
2. `compute_heavy`: utilisation ≥ max(q85, 55%) on **≥1.5 cores**. A single busy thread doesn't count; an earlier 0.5-core threshold labelled every Python batch job compute-heavy.
3. `io_bound`: `io_per_cpu_s` > q88 with utilisation ≤ 40%, or an `io_event` function in the top 40% of duration.
4. `batch_cron`: `kind == batch`.
5. Among interactive rows: `bursty_interactive` if burstiness (invocation CV, else CPU CV, else peak/mean) ≥ the source median or `idle_fraction` ≥ 0.6. Otherwise `steady_service`.

Rows with no usable signal are dropped. That leaves **64,327 rows**:

| Source | Rows |
|---|---|
| Borg | 28,703 |
| Alibaba batch | 15,982 |
| Azure Functions | 11,850 |
| Azure VM | 4,718 |
| Alibaba online | 3,074 |

| Class | Rows |
|---|---|
| batch_cron | 30,357 |
| bursty_interactive | 14,064 |
| steady_service | 9,461 |
| memory_heavy | 7,481 |
| compute_heavy | 1,977 |
| io_bound | 987 |

## 3. Model

### 3.1 Training (`scripts/train.py`)

- **Model:** LightGBM multiclass with 15 leaves, learning rate 0.08, feature and bagging fraction 0.8, and early stopping on a 15% validation split. It stopped at 178 rounds.
- **Class weights:** balanced.
- **Masking augmentation:** each training row gets a copy with ~30% of its features blanked. Profiled apps lack different features from the traces, so the model shouldn't depend on any single feature being present.
- **Split:** 80/20 stratified on label.
- **Commands:** `python scripts/sample_traces.py && python scripts/build_dataset.py && python scripts/train.py`. About 25 s on a laptop once the data is sampled. macOS needs `brew install libomp` for LightGBM.

### 3.2 Metrics (held-out 20%, n = 12,866)

The full report is `src/shipcube_classifier/artefacts/metrics.json`.

| | Accuracy | Macro-F1 |
|---|---|---|
| Held-out test | **0.878** | **0.853** |
| Same rows, 30% of features blanked | 0.783 | 0.752 |

Per-class F1 on the held-out test:

| Class | F1 |
|---|---|
| batch_cron | 0.88 |
| bursty_interactive | 0.81 |
| steady_service | 0.94 |
| memory_heavy | 0.99 |
| io_bound | 0.50 |
| compute_heavy | 1.00 |

Confusion matrix (rows = true, columns = predicted):

| | batch | bursty | steady | memory | io | compute |
|---|---|---|---|---|---|---|
| batch_cron | 4927 | 759 | 209 | 8 | 166 | 3 |
| bursty_interactive | 188 | 2444 | 1 | 4 | 176 | 0 |
| steady_service | 34 | 4 | 1849 | 1 | 4 | 0 |
| memory_heavy | 0 | 1 | 1 | 1494 | 0 | 0 |
| io_bound | 8 | 6 | 1 | 0 | 182 | 0 |
| compute_heavy | 0 | 0 | 0 | 0 | 0 | 396 |

By source, on the held-out test:

| Source | Accuracy | Macro-F1 |
|---|---|---|
| Alibaba batch | 1.00 | 0.83 |
| Alibaba online | 0.99 | 0.99 |
| Azure Functions | 0.73 | 0.75 |
| Azure VM | 0.89 | 0.79 |
| Borg | 0.86 | 0.90 |

**Leave one source out** (train on four traces, test on the fifth):

| Held-out source | Macro-F1 |
|---|---|
| Alibaba batch | 0.30 |
| Alibaba online | 0.26 |
| Azure Functions | 0.19 |
| Azure VM | 0.44 |
| Borg | 0.39 |

Read this as the honest number. The classes are entangled with source-specific units and with which features each source has. A brand-new kind of workload will be classified much less reliably than the held-out score suggests.

Feature importance (share of gain):

| Feature | Gain share |
|---|---|
| `cpu_mean_cores` | 19% |
| `mem_per_core_gb` | 16% |
| `duration_s` | 11% |
| `cpu_cv` | 10% |
| `cpu_p95_cores` | 8% |
| `idle_fraction` | 8% |
| all others | ≤6% each |

### 3.3 Structural prior

The profiler knows whether the code is a server (`web`) or runs to completion (`script`). That's the same kind of side signal the labels came from, and features alone can't always recover it. A once-a-day cron and a rarely opened HTTP endpoint are both idle 99.9% of the day: in the Azure Functions data, rare HTTP functions (labelled bursty) far outnumber once-a-day timers.

So `classify(features, kind)` multiplies the probabilities by a prior and renormalises:
- **Scripts:** the interactive classes are scaled by ×0.05.
- **Web apps:** `batch_cron` is scaled by ×0.05.

Before the prior was added, the nightly sample flipped between batch_cron and bursty_interactive from run to run. Both the raw and the adjusted outputs are in `profile`.

### 3.4 Artefact and inference

- `train.py` exports the booster to `artefacts/model.json`: a compact tree list, 712 KB, 1,068 trees.
- `shipcube_classifier.model` walks it in pure Python, with the same NaN/zero/default-left semantics as LightGBM. Each prediction takes about 0.3 ms, and loading the model about 5 ms.
- **Parity:**
  - Exported vs. LightGBM raw scores agree within 1e-5 on a synthetic model (`test_export_matches_lightgbm_on_synthetic_data`).
  - Argmax agreement is 100% on 500 held-out rows (checked in `train.py`).

## 4. Profiler (`packages/profiler`)

### 4.1 Steps

1. **`materialize`:** writes the source into a temp directory.
   - `inline`: ≤50 files and ≤256 KB; path traversal is rejected.
   - `git`: shallow clone over https only, optional `subdir`.
   - `upload`: tarball extracted with `filter="data"`.
   - `image`: raises `NotRunnable`, which leads to a static estimate.
2. **`summarize`:** static parsing only; user code is never imported. It finds:
   - the entrypoint and the framework (Flask, FastAPI, Django, Streamlit and others) → `web`, otherwise `script`
   - routes
   - GPU, data and network libraries
   - which output file types the code writes
   - a 60-line excerpt for Muse
3. **`LocalRunner`** (implemented, used in tests and the demo). It runs a subprocess in its own session. A psutil sampler thread records, every 100 ms across the whole process tree:
   - CPU, RSS and process count
   - I/O bytes: psutil `io_counters` on Linux; on macOS, rusage block counts, which are approximate
   - wall time
   - network calls (see "Network" below)

   **Scripts** run to completion.

   **Web apps** get a free port in `PORT` and are started with `python app.py`. Flask apps without a main guard use `flask run`; FastAPI apps use `uvicorn`. Once the port opens, they get **bursty synthetic traffic**: 3 bursts of 40 GETs at concurrency 8 over the discovered parameter-free routes, with 1.5 s gaps.

   **Limits:**
   - wall-clock timeout
   - `RLIMIT_CPU` and `RLIMIT_FSIZE`
   - `RLIMIT_AS` on Linux
   - the sampler kills the process group above the memory or process limits

   **Environment:** minimal. Nothing from the parent passes through: no API keys and no cloud credentials. A test checks this.

   **Network:** a `sitecustomize.py` hooks `socket.connect` and `getaddrinfo`. It counts outbound calls and bytes and **refuses non-loopback connections and DNS lookups** (`SHIPCUBE_NO_EGRESS=1`). The nightly sample's webhook shows up as one blocked call to `hooks.example.com`, which the reasoning mentions.
4. **`to_features(profile, hints)`:** CPU, memory and I/O come from the measurements. Invocation features come from hints:
   - **Web:** `expected_users` (default 10) with 30 requests per active user-hour over 9 active hours a day. This gives a closed-form per-minute mean, CV and idle fraction.
   - **Script:** `schedule` gives runs per day through a small cron parser. The default is one run a day.

   Every assumption is returned in `profile.assumptions`.
5. **`StaticRunner`:** when code can't run (an image, a crash, or a server that never opens its port), it uses typical numbers for the app's shape plus the hints. `profile.measurements.mode` is then `"static"`.

### 4.2 GCP mode (`CloudRunJobRunner`, stubbed)

`job_spec()` builds the real Cloud Run Admin v2 Job body, following security's profiler rule (security-tenancy.md §4):
- project `shipcube-apps`
- a service account with zero roles and no secrets
- gen2 execution environment
- Direct VPC egress `ALL_TRAFFIC` into a VPC whose only firewall rule denies egress
- 1 vCPU / 2 GiB
- a 600 s timeout and `maxRetries: 0`

The image is the app image Cloud Build already built (which has egress for dependencies) plus this package. It runs `python -m shipcube_profiler.agent`, which uses `LocalRunner` *inside* the sandbox and prints the profile JSON to the execution's logs.

`run()` raises `NotImplementedError` until the deployer can launch jobs. **LocalRunner is not a security boundary.** It only runs ShipCube's own sample apps and tests on a developer machine. Docker isn't available on Kunal's Mac, so there is no container.

## 5. Placement (`packages/placement`)

### 5.1 Tier rules

| Condition | Tier → target |
|---|---|
| GPU libraries (torch, vllm, ...) | T4 → `cloud-run-gpu` |
| Script that writes HTML/MD **and** has `expected_users` | T0 → `static-snapshot` |
| Any other script | T2 → `cloud-run-job` + Scheduler |
| Web and (`steady_service`, or ≥300 users, or compute-heavy with ≥100 users) | T3 → `cloud-run-service` with min 1, or `gke-autopilot` if it needs >4 vCPU or >16 GiB |
| Other web | T1 → `cloud-run-service`, scales to zero |

### 5.2 Sizing

- **vCPU:** 1.3 × max(p95, 0.8 × peak), rounded up to 0.5/1/2/4. A single process is capped at 1, because one Python process can't use more.
- **Memory:** 1.5 × peak + 0.1 GiB, rounded up to 512 Mi … 32 Gi.
- **Cloud Run constraints:**
  - <1 vCPU → ≤512 MiB
  - >4 GiB → ≥2 vCPU
  - >8 GiB → ≥4 vCPU
- **Concurrency by class:** bursty and steady 80, io_bound 200, memory_heavy 20.
- **Max instances:** 2 × the in-flight requests at peak divided by concurrency, clamped to 2–10.
- **Timeouts:** web: 20 × p95 latency, clamped to 60–300 s. Jobs: 3 × the measured run time, clamped to 60–3,600 s.

### 5.3 Prices

Prices come from the **Cloud Billing Catalog API, fetched 2026-10-27** (`pricing.json`): Cloud Run service `152E-C115-5142` and GKE `CCD8-9BF1-090E`. us-central1 and asia-south1 have identical Cloud Run prices.

| Item | Price |
|---|---|
| Request-based CPU | $0.000024 per vCPU-second |
| Request-based memory | $0.0000025 per GiB-second |
| Requests | $0.40 per million beyond 2 M |
| Instance-based and jobs CPU | $0.000018 per vCPU-second |
| Instance-based and jobs memory | $0.000002 per GiB-second |
| L4 GPU | $0.0001867/s |
| Autopilot pod vCPU | $0.0445/h |
| Autopilot pod memory | $0.0049225 per GiB-hour |
| Cloud Scheduler | $0.10 per job-month |

Human-readable pages: <https://cloud.google.com/run/pricing> and <https://cloud.google.com/kubernetes-engine/pricing>.

### 5.4 Cost model

| Tier | How the monthly cost is computed |
|---|---|
| T1 | requests/month × max(latency, 100 ms), plus a cold start of ~2 s per 50 requests, at request-based rates, plus requests |
| T2 | runs/month × run time at job rates, plus one Scheduler job |
| T0 | T2 refresh, plus static views at ~10 ms each |
| T3 | min instances × 730 h at instance-based rates (or Autopilot pod rates), plus requests |
| T4 | GPU + 4 vCPU + 16 GiB for 2 active hours a day |

- The range is low = 0.4–0.5 × expected and high = (2 + 2 × (1 − confidence)) × expected, so it widens when the classifier is less sure. T4 uses 1–8 active hours a day.
- **The free tier is not subtracted.** It's shared across the billing account, so the estimate is conservative. This is stated in `cost_basis`.

### 5.5 Caps

The rule caps are:

| Cap | Value |
|---|---|
| `max_instances`, `concurrency`, `cpu`, `memory`, `timeout_s` | from the resources |
| `requests_per_minute` (web) | max(60, 4 × peak) |
| `monthly_spend_usd` | max($5, 2 × high cost) |

The alternatives are re-priced with the same model. Web apps get T1, T3 and T0 (when no route takes an id). Scripts get T2, T3 and T0 (when they write files).

## 6. Muse Spark explainer

- **Client:** OpenAI-compatible client with `META_BASE_URL` (default `https://api.meta.ai/v1`) and `META_API_KEY` from the environment or `.env`. The key is never printed.
- **Model:** `muse-spark-1.3`, the newest Spark model the key can list. Override with `SHIPCUBE_EXPLAINER_MODEL`.
- **Token budget:** Muse Spark is a reasoning model, and hidden reasoning tokens count against `max_tokens`. We send `reasoning_effort="low"` with `max_tokens=6000`. At 900 tokens the reply was empty (`finish_reason=length`).
- **Input:** FACTS as JSON (class, probabilities, tier, resources, cost, measurements, code summary, hints, the candidate alternatives with costs, rule caps, assumptions), plus the entrypoint excerpt.
- **Output:** one JSON object with `reasoning` (≤120 words), `alternatives[{tier, tradeoff}]` and `recommended_caps`.

**Muse can't change the numbers:**
- `workload_class`, `confidence`, `tier`, `target`, `resources` and every cost come from our pipeline. Fields Muse returns for them are ignored.
- Alternatives are matched by tier to the ones we priced; only the `tradeoff` text is taken.
- Caps are validated against `Caps`. Only numeric caps the rules set can move, within [0.5×, 2×] of the rule value, and `max_instances` can only go down. `cpu` and `memory` are never taken from the model.

**Prompt injection:**
- User code is wrapped in `<untrusted_code>` tags. Any copy of that tag inside the code is replaced, so the code can't close the wrapper.
- The system prompt says the excerpt is data and its instructions must be ignored.
- The output validation above limits what a successful injection could change: text, and caps within bounds.
- `test_muse_output_never_changes_numbers` feeds a reply that claims a different class, tier, cost and caps. Only the text changes.

**Fallback:** if there's no key, a network or API error, an empty or non-JSON reply, or reasoning outside the length bounds, `FallbackExplainer` uses `RuleExplainer` and sets `explained_by: "rules"`. `profile.explainer_error` holds the error type and message, with the key redacted. A live test runs with `SHIPCUBE_LIVE_MUSE=1`.

**Latency:** about 10–12 s per call. `estimate_load` already runs as a long operation (networking §2.3), so this fits the 10–60 s profiling budget.

## 7. Demo (`packages/placement/scripts/demo.py`)

Output from a run on Oct 27 with Muse Spark. Exact numbers vary slightly between runs because they are measurements:

```
=== dashboard  hints={'expected_users': 25}
class    bursty_interactive  (confidence 0.99)
tier     T1 -> cloud-run-service
size     cpu 0.5, memory 512Mi, instances 0-2, concurrency 80, timeout 60s
cost     $0.19 / $0.46 / $0.93 per month (low/expected/high)
caps     {'max_instances': 2, 'concurrency': 80, 'requests_per_minute': 150, 'monthly_spend_usd': 5.0, 'cpu': '0.5', 'memory': '512Mi', 'timeout_s': 60}
why      [muse-spark-1.3] Classified as bursty_interactive at 99% confidence: Flask web app with 3 routes for 25 occasional
         users. Test run showed ~7 ms mean latency, ~0.02 vCPU mean use and ~46 MiB peak memory, well within 0.5 vCPU and
         512 MiB. No network calls. T1 Cloud Run with scale-to-zero and max 2 instances fits idle-mostly pattern at ~$0.46/month expected.
alt      T3 cloud-run-service: ~$26.36/mo. Always-on for steady latency with no cold starts, but ~$26.36/month expected vs ~$0.46/month for idle dashboard
alt      T0 static-snapshot: ~$0.38/mo. Static snapshot at ~$0.38/month expected: cheapest and instant, but /api routes would go stale

=== nightly_batch  hints={'schedule': '0 2 * * *'}
class    batch_cron  (confidence 0.99)
tier     T2 -> cloud-run-job
size     cpu 1, memory 512Mi, instances 0-1, schedule '0 2 * * *', timeout 60s
cost     $0.05 / $0.10 / $0.30 per month (low/expected/high)
caps     {'max_instances': 1, 'monthly_spend_usd': 5.0, 'cpu': '1', 'memory': '512Mi', 'timeout_s': 60}
why      [muse-spark-1.3] Classified batch_cron at 98.65% confidence: single-file script job.py with no routes that runs to
         completion on nightly schedule 0 2 * * *. Test run finished in ~772 ms using ~1.08 vCPU peak and ~28 MiB peak memory ...
         One outbound call to hooks.example.com. T2 cloud-run-job with 0 to 1 instances fits ... at $0.10/month expected.
alt      T3 cloud-run-service: ~$49.93/mo. Always-on service avoids cold starts but costs ~$49.93/month expected to idle for a nightly ~772 ms job.
alt      T0 static-snapshot: ~$0.10/mo. Static snapshot ... cheap if CSV output alone suffices, but loses job execution and webhook delivery.
```

Run it with `python packages/placement/scripts/demo.py`. Add `--rules` to skip Muse or `--json` for the full result.

## 8. Limits and what's next

- **Weak labels.** The classifier learns our rules plus cross-trace smoothing. The held-out macro-F1 of 0.85 is agreement with the rules; leave-one-source-out (0.19–0.44) is closer to how it would do on a truly new kind of workload.
- **Weak classes.** `io_bound` is the weakest class (F1 0.50, 987 rows). Only Alibaba containers and event-triggered functions provide an I/O signal, and macOS profiling can't measure disk bytes well. Linux (Cloud Run) uses psutil `io_counters`.
- **Unit alignment.** Borg's and Alibaba's normalised units are converted with assumed machine sizes (§2.2).
- **Leakage.** The split is by row, not by job. Borg instances from the same collection can appear in both train and test, so the held-out score is optimistic.
- **Synthetic invocation features.** For profiled apps these come from hints and a fixed office-hours traffic model, not from measurement. Re-placement from live telemetry (ARCHITECTURE §5) should replace them with real per-minute request counts. The feature space is the same, so no retraining is needed.
- **Short load test.** The load test is ~5 s. CPU spikes from fast requests can fall between 100 ms samples; sizing uses peak as well as p95 for that reason.
- **Local runs aren't a sandbox.** `LocalRunner` uses the current interpreter, and the app's dependencies must be installed. Egress blocking is Python-level only; subprocesses in other languages aren't hooked. Production isolation is the Cloud Run job.
- **Cost estimates are deliberately conservative.**
  - The free tier is not subtracted.
  - The cold-start overhead is a guess.
  - GKE's cluster fee is treated as shared across apps.
  - Only us-central1 and asia-south1 prices were checked; other regions are assumed equal, with a note.
- **Next:**
  1. Wire `CloudRunJobRunner.run()` once the deployer builds images.
  2. Log every estimate with its later live telemetry, so labels can come from real ShipCube apps instead of trace heuristics.
  3. Add Bitbrains GWA-T-12 for more I/O signal.
