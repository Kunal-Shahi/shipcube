# Load estimation (`estimate_load`)

Owner: load-estimation workstream. Code: `ml/workload-classifier/`, `packages/profiler/`, `packages/placement/`.

`estimate_load` answers four questions about an app a coding agent wants to deploy:

1. What kind of workload is it?
2. Which tier should it run on?
3. How big should each instance be?
4. Roughly what will it cost per month?

It also returns caps and a short explanation.

- **Every number comes from the classifier, the rules and the profiler's measurements.**
- **Muse Spark writes the text** (`reasoning`, the trade-off of each alternative) and may adjust `recommended_caps` within bounds.
- When the classifier isn't confident, ShipCube's rule set decides the class and the estimate is flagged.
- When Muse is unavailable, the text comes from rules too. Both fallbacks are logged.

```
source ─► materialize ─► summarize (static) ─► profile: Cloud Run job sandbox (gen2, no egress)
                                                 │ (DEV_MODE: local subprocess; can't run → static profile, flagged)
features ─► classifier (versioned, checksum-verified) × structural prior ─► class, confidence
            └─ confidence < gate ─► rule set decides, low_confidence = true
class + profile + hints ─► tier/target ─► resources ─► monthly cost range ─► rule caps, alternatives
facts ─► Muse Spark (timeouts, retries; reasoning, trade-offs, caps within bounds) ─► EstimateResult
         └─ unavailable ─► rule-based text, explained_by = "rules"
```

## 1. Interface

```python
from shipcube_placement import estimate
r = estimate(source, hints, operation_id=op.id, request_id=req_id, traceparent=headers.get("traceparent"))
r.model_dump()                     # -> the estimate_load operation result
```

- **`source`** is the control plane's `Source` dict:
  - `inline` or `git`.
  - `upload`: pass `fetch_upload(upload_id) -> local tarball path`, which reads GCS.
  - `image`: static estimate only.
- **`hints`**: `{expected_users, schedule, region, sample_input, traffic}`, all optional. `traffic` is `office_hours` or `always_on`. When it's omitted, API-only apps (every route under `/api`, `/v1` and similar) are treated as `always_on`.
- **Keyword arguments:**
  - `runner=` defaults to `runner_from_env()`, chosen by **`SHIPCUBE_PROFILER`**:
    - `gcp`: `CloudRunJobRunner` (production)
    - `local`: `DockerRunner`, the same image in a local container (§4.3)
    - `inproc`: `LocalRunner` in this process, only with `SHIPCUBE_DEV_MODE=1`
    - unset: `inproc` in DEV_MODE, otherwise `gcp`
  - `explainer=` defaults to `FallbackExplainer` (Muse, then rules).

**Output.** The networking PR #1 §2.1 fields, plus `explained_by` from the Oct 28 decision, plus three fields added by this PR:

`estimate_id, workload_class, confidence, tier, target, resources, monthly_cost_usd{low, expected, high}, profile, recommended_caps, reasoning, alternatives[], explained_by, decided_by, low_confidence, model_version`

| Field | Meaning |
|---|---|
| `target` | `cloud-run-service`, `cloud-run-job`, `cloud-run-gpu`, `gke-autopilot` or `static-snapshot` |
| `decided_by` | `classifier`, or `rules` when the classifier was under its confidence gate |
| `low_confidence` | `true` when the rules decided |
| `model_version` | The classifier version, e.g. `wc-20261027-5b477361` |
| `explained_by` | The Muse model id, or `rules` |
| `recommended_caps` | Exactly the control plane's `Caps` fields and bounds |

`profile` carries:
- the measurements and the feature vector
- class probabilities, the classifier's own call and the rule set's call
- the code summary
- `cost_basis`: which prices were used, and that the free tier isn't subtracted
- every assumption made
- `explainer_fallback_reason`, with the key redacted

**Install** (Python 3.12):
```bash
pip install "shipcube-classifier[gcp] @ git+https://github.com/Kunal-Shahi/shipcube@main#subdirectory=ml/workload-classifier" \
            "shipcube-profiler @ git+https://github.com/Kunal-Shahi/shipcube@main#subdirectory=packages/profiler" \
            "shipcube-placement @ git+https://github.com/Kunal-Shahi/shipcube@main#subdirectory=packages/placement"
```
Configuration is in §9.

## 2. Data

### 2.1 Sources and licences

| Trace | What we take | How it's sampled | Licence |
|---|---|---|---|
| [Azure Functions 2019](https://github.com/Azure/AzurePublicDataset/blob/master/AzureFunctionsDataset2019.md) | Day 1: invocations per minute, trigger, duration percentiles, per-app memory | Streams the 143 MB release and stops after the day-1 files; 12,000 functions with ≥1 call | CC BY 4.0 |
| [Azure VM 2019 (V2)](https://github.com/Azure/AzurePublicDataset/blob/master/AzurePublicDatasetV2.md) `vmtable` | avg/max/p95 CPU, core bucket, lifetime, `vmcategory` | First 400,000 rows of the gzip stream, reservoir sample of 20,000 | CC BY 4.0 |
| [Alibaba cluster-trace-v2018](https://github.com/alibaba/clusterdata/tree/master/cluster-trace-v2018) | `batch_instance` (offline); `container_meta` + `container_usage` (online: CPU, memory, disk I/O %, net) | First 1.5 M / 3 M rows of each stream (~50 MB downloaded) | No licence file. Published for research; derived aggregates only, nothing redistributed. Cite the trace. |
| [Google cluster-data 2019](https://github.com/google/cluster-data/blob/master/ClusterData2019.md), cell `a` | `instance_usage` aggregated per instance, joined to priority | BigQuery `TABLESAMPLE SYSTEM (0.05 PERCENT)`, ≤30,000 instances | CC BY 4.0 |

- **Raw data is never committed.** `data/` is gitignored, and `scripts/sample_traces.py` re-downloads the samples.
- **BigQuery cost:** the job billed **0.8 GB**, which is free under 1 TB/month.
  - `bq` checks `--maximum_bytes_billed` against a worst-case scan of ~400 GB before running, so that cap is a guard, not the expected cost.
  - Dry runs underestimate the cost on this table.

### 2.2 Unit alignment

| Source | Conversion |
|---|---|
| Borg | 1.0 NCU = 64 vCPU; 1.0 NMU = 256 GiB (assumed) |
| Alibaba | CPU 100 = 1 core; memory 100 = one machine, assumed 256 GiB; `disk_io_percent` 100 = 200 MiB/s; net 100 = 10 Gbit/s |
| Azure VM | **Memory buckets are allocations, not usage.** A 32 GB VM at 1% CPU looked like 275 GiB per core, so VM memory features are left unknown. This one fix moved 2,400 VMs out of a bogus `memory_heavy`. |

- CPU utilisation with no allocation in the trace is measured against `max(1, ceil(p95))` cores. The profiler uses the same rule.
- `mem_per_core_gb` is peak memory over **mean** CPU. Allocating memory spikes p95 CPU, which hid memory-heavy apps.

### 2.3 Features (`FEATURES`)

| Feature | Meaning |
|---|---|
| `cpu_mean_cores`, `cpu_p95_cores` | Resource level |
| `cpu_util_mean` | Mean over allocation |
| `cpu_cv`, `cpu_peak_to_mean` | Variance and burstiness of CPU |
| `mem_mean_gb`, `mem_peak_gb` | Memory level |
| `mem_per_core_gb` | Peak memory over mean CPU |
| `io_mb_per_s`, `net_mb_per_s` | I/O while active |
| `io_per_cpu_s` | I/O per CPU-second |
| `invocations_per_hour`, `invocation_cv`, `idle_fraction` | Invocation frequency and burstiness |
| `duration_s` | Seconds per invocation |

A feature a source can't observe is NaN.

### 2.4 Labels: weak supervision with physical thresholds (`rules.py`, `weak_labels.py`)

No public trace contains workload classes. Each trace has one coarse *side signal* that is not in the feature vector, and the labels come from that signal plus physical rules.

Side signals:
- **Azure Functions trigger:** timer → batch; http, orchestration → interactive; queue, event, storage → io_event.
- **VM category:** Delay-insensitive → batch; Interactive → interactive.
- **Alibaba:** batch instances → batch; online containers → interactive.
- **Borg priority:** ≤115 → batch; ≥120 → interactive.

**The thresholds are fixed physical values, the same in every source.** The first version used within-source quantiles. They balanced the classes but made "memory-heavy" mean something different in each trace, and the model couldn't transfer between traces.

| Class | Rule (applied in this order) |
|---|---|
| `memory_heavy` | peak ≥ 1 GiB, ≥ 4 GiB per core of mean CPU, CPU utilisation ≤ 50% |
| `compute_heavy` | ≥ 1.5 cores busy at ≥ 60% utilisation |
| `io_bound` | ≥ 150 MiB of disk+network per CPU-second at ≤ 40% utilisation, or an event-triggered function running ≥ 5 s |
| `batch_cron` | the side signal says batch |
| `bursty_interactive` | interactive, and idle ≥ 50% of minutes, or invocation CV ≥ 3, or CPU CV ≥ 0.6 (peak/mean ≥ 4 when no CV) |
| `steady_service` | any other interactive row |

`rules.py` holds the same thresholds as a pure-Python `rule_class(features, shape)`. ShipCube uses it at runtime as the low-confidence fallback (§3.5).

The dataset has **61,954 trace rows**:

| Class | Rows |
|---|---|
| batch_cron | 27,250 |
| memory_heavy | 11,808 |
| steady_service | 11,456 |
| bursty_interactive | 9,116 |
| compute_heavy | 1,777 |
| io_bound | 598 |

### 2.5 Target-domain apps: the calibration suites (`calibration/`)

Traces are a different domain from apps ShipCube profiles: different units, different missing features. So real apps were written and run through the real profiler.

- **Dev suite** (`suite.apps()`): 26 apps in 6 families (batch, compute, memory, io, bursty, steady), 2 runs each. 51 runs completed; one compute run failed.
  - Used to measure transfer, calibrate confidence and pick the gate.
  - Also used to find profiler/feature mismatches; §3.4 lists them.
- **Held-out suite** (`suite.holdout_apps()`): 10 differently written programs, one per class pair.
  - It was meant to be looked at once, but it was looked at **three times** (§3.4). It's still never used to fit anything.

The runs are committed as `calibration/runs.csv` and `holdout.csv` (features only, generated from our own code), so training is reproducible without re-profiling. Each run records both the intended label and the rule label; they agree on 90% of runs. The disagreements are apps that, on this machine, didn't cross the threshold they were designed for (for example, a single-core "compute" app), and they're kept as honest errors.

## 3. Model

### 3.1 Training (`scripts/train.py`)

- **Model:** LightGBM multiclass, 15 leaves, 220 rounds.
- **Weights:** class-balanced *within each source*, and every source weighs the same in total, so no trace's units dominate.
- **Masking augmentation:** every training row gets a copy with 30% of its features blanked.
- **Training data: traces only.** Adding the dev-suite runs to training was tried. With one app family held out, macro-F1 fell to about 0.65, because the model learned shortcuts such as "a profiled script that isn't batch is compute, memory or I/O".

### 3.2 Evaluation, most to least optimistic

Model `wc-20261027-5b477361`; full report in `artefacts/metrics.json`.

| Evaluation | n | Accuracy | Macro-F1 |
|---|---|---|---|
| Trace held-out split (random 20%) | 12,356 | 0.903 | 0.870 |
| Leave one trace source out (mean of 5) | — | — | 0.39 |
| **Traces → profiled dev apps** (no profiled app in training) | 51 runs / 26 apps | **0.980** | **0.979** |
| **Held-out apps** (third look, §3.4) | 10 | 1.00 | 1.00 |
| Rules alone on dev / held-out apps | 51 / 10 | 0.90 / 0.90 | — |

Leave-one-source-out macro-F1 per held-out trace:

| Held-out source | Macro-F1 |
|---|---|
| Alibaba batch | 0.67 |
| Alibaba online | 0.46 |
| Azure Functions | 0.20 |
| Azure VM | 0.31 |
| Borg | 0.31 |

Leave-one-source-out stays low for a structural reason: each trace is the *only* source of some features. Invocation patterns exist only in Azure Functions, and I/O only in Alibaba containers. Profiled apps have every feature, which is why transfer to them is much better than transfer between traces.

On the trace split, per-class F1 is:

| Class | F1 |
|---|---|
| batch_cron | 0.89 |
| bursty_interactive | 0.79 |
| steady_service | 0.92 |
| memory_heavy | 1.00 |
| io_bound | 0.62 |
| compute_heavy | 1.00 |

On profiled dev apps, the only error is one single-core "compute" run read as `batch_cron` (its rule label agrees).

Trace held-out confusion matrix (rows = true, columns = predicted):

| | batch | bursty | steady | memory | io | compute |
|---|---|---|---|---|---|---|
| batch_cron | 4735 | 436 | 182 | 1 | 47 | 0 |
| bursty_interactive | 246 | 1428 | 0 | 0 | 72 | 0 |
| steady_service | 181 | 0 | 2091 | 1 | 2 | 0 |
| memory_heavy | 4 | 4 | 1 | 2447 | 0 | 0 |
| io_bound | 18 | 0 | 1 | 0 | 112 | 0 |
| compute_heavy | 0 | 0 | 0 | 0 | 0 | 347 |

Feature gain:

| Feature | Gain share |
|---|---|
| `cpu_util_mean` | 18% |
| `cpu_mean_cores` | 12% |
| `mem_per_core_gb` | 11% |
| `mem_peak_gb` | 11% |
| `duration_s` | 8% |
| all others | ≤6% each |

### 3.3 Structural prior

The profiler knows the app's shape. This is the same kind of side signal the labels came from, and the features alone can't recover it: a script that sleeps between steps has CPU variance the traces associate with bursty services.

| Shape | Prior |
|---|---|
| `script` (ran to completion) | Interactive classes are impossible: probability 0 |
| `web` | `batch_cron` scaled by ×0.05 |
| `worker` (a script that never exited) | No prior |

### 3.4 How the profiled-app numbers got here (honest history)

Each step was a profiler or feature fix found on the dev suite. The held-out suite was evaluated after steps 4 and 6 and at the end, so it is no longer pristine. Fresh held-out apps are listed under "Next" in §8.

1. **Quantile labels → physical thresholds.** Leave-one-source-out mean macro-F1 went from 0.31 to 0.45.
2. **Azure VM memory allocation removed** (§2.2).
3. **macOS disk I/O:** read through `proc_pid_rusage` (`ri_diskio_bytes*`); rusage block counts read 0. Linux uses psutil `io_counters`.
4. **`mem_per_core` over mean CPU** (§2.2). *First held-out look: model 8/10.* The two steady APIs were read as bursty, and a temperature of 0.36 made those mistakes look certain.
5. **Temperature floor at 1** (never sharpen), and the profiler's **CPU CV over 1 s buckets** instead of 100 ms samples. *Second look: 8/10*, with different web-app mistakes.
6. **Web apps' CPU CV left unknown.** A few seconds of synthetic load says nothing about how CPU varies over a day; for web apps, burstiness comes from the traffic model. *Third look: 10/10.* Dev suite: 0.98.

### 3.5 Confidence and the low-confidence fallback

- **Temperature scaling** is fitted on the dev-suite predictions and kept only if it beats no scaling in a family-cross-fitted ECE, with a floor of 1.
  - The current model keeps T = 1. Its ECE is 0.043 uncalibrated, and a fitted T of 0.84 would have sharpened.
- **Gate (`min_confidence`):** the lowest threshold at which ≥85% of accepted dev-suite predictions are correct, with a policy floor of **0.5**. It is 0.5 now.
- **Policy at runtime (`decide()`):**
  - If confidence ≥ the gate, the classifier decides.
  - Below the gate, `rule_class` decides, and the estimate carries `decided_by: "rules"` and `low_confidence: true`. The rule-based reasoning, or Muse's when available (it's told about the flag), says so.
  - Disagreement between the classifier and the rules is recorded in `profile.rules_class` and in `assumptions`.
- **Results:**

  | Suite | Policy accuracy | Flagged |
  |---|---|---|
  | Dev | 0.98 | 2% |
  | Held-out | 1.00 | 0% |

  When the classifier and the rules disagreed (8% of dev runs, 10% of held-out), the classifier was right every time.

### 3.6 Versioning and serving (`registry.py`)

- **Version:** `train.py` writes `model.json` with its version (`wc-<date>-<sha256[:8]>` over the trees and calibration) and `manifest.json` with the file's sha256.
- **Publishing:** `scripts/publish_model.py gs://<bucket>/models/workload-classifier --latest` uploads `<version>/{model,manifest,metrics}.json` with `ifGenerationMatch=0`, so published versions are immutable. It then moves `latest.json`.
- **Loading (`load_model()`):**
  - Reads `SHIPCUBE_MODEL_URI` (`gs://…/<version|latest>`, or `bundled`), with an optional `SHIPCUBE_MODEL_SHA256` pin.
  - Verifies the sha256 against the manifest (and the pin), plus `meta.version`, and caches verified bytes locally.
  - A configured GCS model that can't be fetched or fails verification **raises `ModelLoadError`**. There's no silent fallback to the bundled copy.
- **Logging:** every estimate carries `model_version`, and `model.loaded` is logged with the version and digest.
- **Inference:** pure Python; LightGBM parity is under 2e-6 on raw scores. About 0.3 ms per prediction.

## 4. Profiler (`packages/profiler`)

### 4.1 Steps

1. **`materialize`:** `inline` (≤50 files / 256 KB, no path traversal), `git` (https, shallow), `upload` (tar `filter="data"`). `image` → `NotRunnable`.
2. **`summarize`:** static parsing only; user code is never imported. It finds the entrypoint, framework, routes, `api_only`, libraries, written output types, and a 60-line excerpt.
3. **Runners:**
   - **`CloudRunJobRunner` (production, implemented):** packages `app/` plus the profiler and the classifier's feature code with a generated Dockerfile, and uploads it with `ifGenerationMatch=0`. Then:
     1. Cloud Build builds and pushes the image (the build has egress for requirements).
     2. A Cloud Run v2 job is created with the security profiler rule: gen2, a zero-role SA, no secrets, Direct VPC egress `ALL_TRAFFIC` into a VPC whose only firewall rule denies egress, 1 vCPU / 2 GiB, 600 s, `maxRetries: 0`. The image is pinned by digest.
     3. The job runs, and the agent's one-line `SHIPCUBE_PROFILE {json}` result is read back from Cloud Logging.
     4. The job and the tarball are deleted.

     Every call has a timeout, and idempotent calls are retried with backoff. Failures raise `ProfilerError(stage)`, where stage is config, upload, build, job, run or logs. `estimate()` then logs `profile.failed` and falls back to a static profile, noted in `assumptions`.
   - **`LocalRunner`:** a subprocess with psutil sampling, rlimits, a scrubbed environment, and a sitecustomize that blocks non-loopback connects and DNS. It is used *inside* the job sandbox by `agent.py`, and directly **only in `SHIPCUBE_DEV_MODE=1`** (tests, demo). On its own it's not a security boundary.
   - **`StaticRunner`:** typical numbers when code can't run; `mode: "static"`, logged.
4. **`to_features`:**
   - CPU, memory and I/O come from the measurements. CPU CV is over 1 s buckets, and unknown for web apps.
   - Invocation features come from the traffic model: `office_hours` (30 requests per active user-hour, 9 h a day) or `always_on` (Poisson around the clock). For scripts they come from the cron schedule, defaulting to daily.
   - Every assumption is returned.

### 4.2 Verified on real GCP (Oct 27)

The resources come from security's Terraform module `infra/terraform/modules/profiler` (commit `025172a`):
- the sandbox in `shipcube-apps-ks`
- the bucket `shipcube-ks-ml` in `shipcube-control-ks`

The runs used Kunal's user credentials, not the worker SA, so the worker's least-privilege IAM is still untested.

**Integration test:** `test_gcp_integration_profiles_the_batch_sample` passes in about 90 s. In sequence:
1. Cloud Build built the image.
2. A gen2 job ran in the deny-all VPC as `profiler-sandbox`.
3. The result came back from Cloud Logging.
4. The job and the tarball were deleted.

**End to end, `estimate()` with no DEV_MODE:** the sandbox profiler, the GCS model (`latest`, sha256-pinned) and Muse.

| App | Profile | Class / decided by | Tier | Muse | Wall time |
|---|---|---|---|---|---|
| nightly_batch | measured on GCP: 6.9 s, 0.96 vCPU, 28 MiB, psutil I/O; webhook to `hooks.example.com` **blocked** | batch_cron (0.88) / classifier | T2 | muse-spark-1.3, 1 attempt | ~70 s |
| dashboard | measured on GCP: 120/120 requests, 0.11 vCPU, 41 MiB | bursty_interactive (0.87) / classifier | T1 | muse-spark-1.3, 1 attempt | 63 s |

Every log line carried `estimate_id`, `operation_id` and `logging.googleapis.com/trace`.

**Bugs the fakes hid, found and fixed on real GCP:**
1. **Cloud Build's operation name.** A regional build returns a global-style operation name (`operations/build/<project>/…`), and polling it returns 404. The runner now polls `projects/{p}/locations/{r}/builds/{id}`.
2. **Logging read quota.** User credentials from `gcloud auth application-default login` bill API quota to gcloud's shared OAuth client project, where `entries.list` returned 429 for the whole wait. One end-to-end run fell back to a static profile, flagged and logged, as designed. Two fixes:
   - The client now sets the quota project explicitly (`x-goog-user-project` = the profiler project, or `SHIPCUBE_QUOTA_PROJECT`). The caller needs `serviceusage.serviceUsageConsumer` there.
   - Log polling is quota-aware: at most one query per 5 s, a timestamp bound on the filter, and a 429 means back off and keep waiting until the deadline.

**Model registry on GCS:** `wc-20261027-5b477361` published to `gs://shipcube-ks-ml/models/workload-classifier/`, with sha256 `0c559d32…`.
- `latest` and pinned loads pass the checksum, and the bytes are identical to the bundled copy.
- A wrong pin and a missing version raise `ModelLoadError`.
- Re-publishing leaves the immutable objects untouched (412).

### 4.3 Local sandbox: `SHIPCUBE_PROFILER=local` (Docker)

The local stack runs the profiler in a Docker container instead of the Cloud Run job (`docker_runner.py`).

**Same image as production.** It uses the same deterministic build context and Dockerfile as Cloud Build:
- `python:3.12-slim`, with the app's requirements installed at build time (with network, as in Cloud Build)
- the agent, running as non-root uid 10001, with `/app` and the agent code read-only

The image tag is a hash of the context, so unchanged code is not rebuilt.

**Isolation, enforced by Docker:**

| Control | Setting |
|---|---|
| Network | `--network none`: loopback only, no DNS |
| Filesystem | `--read-only` root; `/tmp` is a nosuid, nodev tmpfs (512 MiB) |
| User and privileges | `--user 10001:10001`, `--cap-drop ALL`, `--security-opt no-new-privileges` |
| CPU and memory | `--cpus 1`, `--memory 2048m`, swap off: the Cloud Run job's 1 vCPU / 2 GiB |
| Processes and files | `--pids-limit 256`, `nofile 1024` |
| Wall clock | 600 s; the container is killed after that |
| Host access | nothing passes from the host environment except the hints; no host mounts |

**Same metrics.** The agent measures inside the container with Linux psutil (CPU, RSS, I/O bytes, processes), plus wall time and blocked egress. Its result is read from `docker run` stdout instead of Cloud Logging. It then feeds the same classifier, policy and explainer: Muse when `META_API_KEY` is set, rules otherwise.

**Tests** (`tests/test_docker_runner.py`; these run on CI's ubuntu runners, which have Docker):
- **No network.** The app uses the raw `_socket` module, so the Python hook can't stop it. It tries TCP to 1.1.1.1:53, 8.8.8.8:443 and the Docker bridge, and DNS for example.com, and checks that `/proc/net/dev` shows only `lo`.
  - A control run of the same image *with* the default network connects (exit 3). So the test does fail when the network is reachable.
- **Locked down:** the app isn't root, can't write `/app`, `/usr` or `/etc`, can write `/tmp`, and has no capabilities.
- **Limits:** a memory hog is reported as over its limit; a runaway is stopped by the timeout.
- **Hygiene:** requirements install at build and import offline; the image is reused for the same code; no containers are left behind; no host environment leaks in.
- **End to end:** both sample apps are measured, and `estimate()` picks the sandbox from the environment alone.

**Measured on Kunal's Mac** (Colima, 4 vCPU, arm64):
- nightly_batch: 1.0 s, 0.87 vCPU, 27 MiB, psutil I/O, webhook blocked
- dashboard: 120/120 requests, 0.06 vCPU, 39 MiB

Through `estimate()`, these give the same classes and tiers as the Cloud Run job (§4.2), explained by `muse-spark-1.3`. A first run builds the image in about 15 s.

**Configuration:**

| Variable | Default | Purpose |
|---|---|---|
| `SHIPCUBE_PROFILER_DOCKER` | `docker` | Docker CLI |
| `DOCKER_HOST` | Docker's default | Daemon to use |
| `SHIPCUBE_PROFILER_CPUS` | 1 | CPU limit |
| `SHIPCUBE_PROFILER_MEMORY_MB` | 2048 | Memory limit (swap off) |
| `SHIPCUBE_PROFILER_PIDS` | 256 | Process limit |
| `SHIPCUBE_PROFILER_TIMEOUT_S` | 600 | Wall-clock limit |
| `SHIPCUBE_PROFILER_TMPFS_MB` | 512 | Size of `/tmp` |
| `SHIPCUBE_PROFILER_BUILD_TIMEOUT_S` | 900 | Image build limit |

`shipcube_profiler.docker_runner.prune_images()` removes profiler images older than 24 h. It's the local equivalent of the registry's cleanup policy.

**Limit:** this is container isolation on the developer's machine, not the Cloud Run job's microVM, so production still uses `gcp`.

## 5. Placement (`packages/placement`)

### 5.1 Tier rules

| Condition | Tier → target |
|---|---|
| GPU libraries | T4 → `cloud-run-gpu` |
| Script that writes HTML/MD and has users | T0 → `static-snapshot` |
| Any other script | T2 → `cloud-run-job` + Scheduler |
| Web and (steady, or ≥300 users, or compute-heavy with ≥100 users) | T3 → `cloud-run-service` with min 1, or `gke-autopilot` above 4 vCPU or 16 GiB |
| Other web | T1 → `cloud-run-service`, scales to zero |

### 5.2 Sizing

- **vCPU:** 1.3 × max(p95, 0.8 × peak). Single-process apps are capped at 1.
- **Memory:** 1.5 × peak + 0.1 GiB.
- **Cloud Run CPU/memory constraints:** <1 vCPU → ≤512 MiB; >4 GiB → ≥2 vCPU; >8 GiB → ≥4 vCPU.
- **Concurrency by class:** bursty and steady 80, io_bound 200, memory_heavy 20.
- **Max instances:** 2–10.
- **Timeouts:** web 60–300 s; jobs 3 × the measured run time.

### 5.3 Prices

Prices come from the Cloud Billing Catalog API, fetched 2026-10-27 (`pricing.json`): Cloud Run `152E-C115-5142` and GKE `CCD8-9BF1-090E`. us-central1 and asia-south1 are identical.

| Item | Price |
|---|---|
| Request-based CPU | $0.000024 per vCPU-second |
| Request-based memory | $0.0000025 per GiB-second |
| Requests | $0.40 per million |
| Instance-based and jobs CPU | $0.000018 per vCPU-second |
| Instance-based and jobs memory | $0.000002 per GiB-second |
| L4 GPU | $0.0001867/s |
| Autopilot pod vCPU | $0.0445/h |
| Autopilot pod memory | $0.0049225 per GiB-hour |
| Cloud Scheduler | $0.10 per job-month |

The free tier is not subtracted, so the estimate is conservative.

### 5.4 Cost model and caps

Monthly cost by tier:

| Tier | How the monthly cost is computed |
|---|---|
| T1 | requests × max(latency, 100 ms), plus a cold-start allowance |
| T2 | runs × run time, plus one Scheduler job |
| T3 | min instances × 730 h |
| T4 | 2 GPU-hours a day |

- The range widens as confidence drops.
- **Caps:** `max_instances`, `concurrency`, `cpu`, `memory` and `timeout_s` come from the resources. `requests_per_minute` is max(60, 4 × peak), and `monthly_spend_usd` is max($5, 2 × the high cost).

## 6. Muse Spark explainer

- **Model:** `muse-spark-1.3` through the OpenAI-compatible client, `META_BASE_URL`, `META_API_KEY`. The key is never logged or returned.
- **Request:** `reasoning_effort="low"` and `max_tokens=6000`, since hidden reasoning tokens count against the limit.
- **Timeouts and retries:**
  - Each attempt has a timeout (`SHIPCUBE_MUSE_TIMEOUT_S`, default 30) inside an overall deadline (`SHIPCUBE_MUSE_DEADLINE_S`, default 60).
  - Timeouts, 429, 5xx and connection errors are retried up to 3 times with exponential backoff and jitter. The SDK's own retries are off, so every attempt is logged as `muse.call`.
  - Auth errors, bad requests and bad replies aren't retried.
- **Typed errors:** `MuseError(kind)`, where kind is one of `not_configured`, `auth`, `rate_limited`, `timeout`, `unavailable`, `bad_request` or `bad_response`.
- **Fallback:** `FallbackExplainer` is stateless (safe across concurrent estimates). It logs `explainer.fallback` with the reason and returns rule text, with `explained_by: "rules"` and `profile.explainer_fallback_reason`.

**Muse can't change the numbers:**
- Class, confidence, tier, target, resources and costs are ours.
- Alternatives are matched to tiers we priced; only the `tradeoff` text is taken.
- Caps are validated, numeric caps are clamped to [0.5×, 2×] of the rule caps, `max_instances` can only go down, and `cpu` and `memory` are never taken from Muse.

**Prompt injection:** the code goes inside `<untrusted_code>`, and any copy of that tag in the code is neutralised. Tests feed a reply that tries to change everything and a payload that tries to close the tag.

## 7. Demo

```bash
python packages/placement/scripts/demo.py           # --rules (no Muse), --json, --logs (structured logs)
```

`--sandbox local` profiles in the Docker sandbox. Without it, the demo uses in-process DEV_MODE.

| App | Class | Decided by | Tier → target | Cost/month (low / expected / high) |
|---|---|---|---|---|
| Flask dashboard, 25 users | bursty_interactive (0.65–0.87 across runs) | classifier | T1 → cloud-run-service | $0.19 / $0.46 / $1.25 (0.5 vCPU) to $0.32 / $0.81 / $1.83 (1 vCPU) |
| Nightly batch job, `0 2 * * *` | batch_cron (0.98–0.99) | classifier | T2 → cloud-run-job | $0.05 / $0.10 / $0.31 |

- **Muse Spark** explains both, citing the measured numbers.
- **The batch job's webhook call** is blocked and called out in the reasoning.
- **Run-to-run variance.** The numbers vary between runs because they're measurements. The dashboard's confidence moves between 0.65 and 0.87 from run to run, and its peak CPU flips its size between 0.5 and 1 vCPU. A short load test is noisy, which is one more reason the gate exists.

## 8. Limits

- **Weak labels.** "Correct" means agreeing with physical rules plus the trace's side signal. The suites are written by us, and the held-out suite has been looked at three times.
- **Small app suites.** 26 + 10 apps can't cover user apps such as Django monoliths, GPU inference or queue workers. The 0.98 / 1.00 figures are on our own apps.
- **Trace-to-trace transfer is poor** (leave-one-source-out 0.2–0.67), because the features are split across sources.
- **`io_bound` is thin:** 598 trace rows and F1 0.62 on the trace split.
- **Unit assumptions** (§2.2) drive memory labels for Alibaba and Borg.
- **Traffic model.** Invocation features for profiled apps come from hints and a fixed traffic model, not measurement.
- **Real GCP so far.** `CloudRunJobRunner` was verified with Kunal's user credentials. The worker SA's least-privilege IAM path hasn't been exercised yet (§4.2).
- **Local runs are not a sandbox.** `LocalRunner` blocks egress only at the Python level.
- **Conservative costs.** The free tier isn't subtracted, and only two regions were checked.
- **Next:**
  1. Re-run the GCP integration test as the API worker SA.
  2. Write a fresh held-out suite, including community apps from public repos.
  3. Record live telemetry next to each estimate, so labels can come from real ShipCube apps.
  4. Add Bitbrains for more I/O signal.

## 9. Operate

### 9.1 Configuration

All configuration comes from the environment.

| Variable | Default | Purpose |
|---|---|---|
| `SHIPCUBE_PROFILER` | `gcp` (`inproc` in DEV_MODE) | Profiler sandbox: `gcp` (Cloud Run job), `local` (Docker, §4.3), or `inproc` |
| `SHIPCUBE_DEV_MODE` | unset | `1` allows `SHIPCUBE_PROFILER=inproc` (tests, laptops). Never in production |
| `SHIPCUBE_PROFILER_PROJECT`, `_REGION`, `_BUCKET`, `_REPOSITORY`, `_NETWORK`, `_SUBNET`, `_SERVICE_ACCOUNT` | required in production | Profiler sandbox; Terraform outputs (`packages/profiler/deploy/RESOURCES.md`) |
| `SHIPCUBE_PROFILER_BUILD_SERVICE_ACCOUNT` | Cloud Build default | Build identity |
| `SHIPCUBE_MODEL_URI` | `bundled` | `gs://<bucket>/models/workload-classifier/<version or latest>` |
| `SHIPCUBE_MODEL_SHA256` | unset | Pin; loading fails on mismatch |
| `SHIPCUBE_MODEL_CACHE` | `$TMPDIR/shipcube-models` | Verified local cache |
| `SHIPCUBE_QUOTA_PROJECT` | the profiler project | Project billed for API quota (`x-goog-user-project`) |
| `META_API_KEY` | — | From Secret Manager in production, never a literal |
| `META_BASE_URL` | `https://api.meta.ai/v1` | Muse endpoint |
| `SHIPCUBE_EXPLAINER_MODEL` | `muse-spark-1.3` | Muse model id |
| `SHIPCUBE_MUSE_TIMEOUT_S` | 30 | Per-attempt timeout |
| `SHIPCUBE_MUSE_DEADLINE_S` | 60 | Overall deadline across retries |
| `SHIPCUBE_LOG_LEVEL` | INFO | Log level |

### 9.2 Logs, metrics and traces

The host calls `shipcube_classifier.obs.configure_logging()` once. It logs JSON lines for Cloud Logging.

**Common fields** on every line:
- `severity`, `message`, `time`, `logger`, `event`
- `estimate_id`, `operation_id`, `request_id`
- `logging.googleapis.com/trace` and `spanId`, from the W3C `traceparent` passed to `estimate()`

**Events:**

| Event | Fields |
|---|---|
| `estimate.completed` | `duration_ms`, `outcome`, `tier`, `workload_class`, `decided_by`, `low_confidence`, `explained_by`, `model_version`, `profile_mode`, `cost_expected_usd` |
| `profile.completed`, `profile.gcp` | `duration_ms`, `runner`, `outcome` |
| `profile.failed`, `profile.static` | WARNING |
| `muse.call` | per attempt: `duration_ms`, `outcome`, `attempt`, tokens |
| `explainer.fallback` | WARNING, with `reason` |
| `model.loaded` | `version`, `sha256`, `source` |
| `gcp.retry` | retried REST call |

**Log-based metrics to create:**

| Metric | Built from |
|---|---|
| `estimate_latency` | distribution of `estimate.completed` `duration_ms` |
| `estimate_low_confidence_rate` | counter on `low_confidence=true` |
| `explainer_fallback_rate` | counter on `explainer.fallback` |
| `muse_latency` | distribution of `muse.call` `duration_ms` |
| `profiler_failures` | counter on `profile.failed`, labelled by `stage` |

**Alert** when the fallback rate or the profiler failure rate rises above 20% over 1 h.

### 9.3 Deploy

1. **Infrastructure as code.** The resources in `packages/profiler/deploy/RESOURCES.md` are managed by the infra/security workstream's Terraform (`infra/**`), not created by hand:
   - in `shipcube-apps`: the sandbox VPC with deny-all egress, the zero-role sandbox SA, the build SA, and the Artifact Registry repo with 7-day cleanup
   - in `shipcube-control-ks`: the ML bucket `shipcube-ks-ml`, with a 1-day lifecycle rule on `profiler-src/`
   - least-privilege IAM for the caller and the build SA

   Set the `SHIPCUBE_PROFILER_*` variables from its outputs.
2. Publish the model: `python ml/workload-classifier/scripts/publish_model.py gs://<bucket>/models/workload-classifier --latest`. Then set `SHIPCUBE_MODEL_URI` and `SHIPCUBE_MODEL_SHA256` from its output.
3. Install the three packages into the API worker image, and promote the same image across environments.
4. Run `SHIPCUBE_GCP_IT=1 pytest packages/profiler -k integration` against the target project.

### 9.4 Runbook

| Symptom | Check | Fix |
|---|---|---|
| `estimate_load` fails with `ProfilerError: config` | Worker env | Set the `SHIPCUBE_PROFILER_*` variables (Terraform outputs; see `deploy/RESOURCES.md`) |
| Many `profile.failed stage=build` | Cloud Build history, tag `shipcube-profiler` | App requirements don't install. The estimate still returns a static profile; the user fixes requirements |
| `profile.failed stage=logs` or many `profile.logs_throttled` | Log entries for `resource.type=cloud_run_job`, `job_name=prof-…`; the 429 message's `consumer` | If the consumer isn't our project, set `SHIPCUBE_QUOTA_PROJECT` and grant `serviceUsageConsumer`. Otherwise it's ingestion lag (raise `log_wait_s`) or the agent crashed (see the job's stderr) |
| `ModelLoadError` at startup | `model.loaded` / exception text | Wrong URI, missing `storage.objectViewer`, or a tampered object. Re-publish, or pin the previous version |
| `explainer.fallback reason=auth` | Secret Manager version of `META_API_KEY` | Rotate the key |
| `explainer.fallback reason=timeout` or `rate_limited` | `muse.call` latency | Raise `SHIPCUBE_MUSE_DEADLINE_S`, or accept the rule text |
| Low-confidence rate jumps | `estimate.completed low_confidence=true` by `model_version` | New kinds of apps; add them to the calibration suites and retrain |
| Rolling back a model | — | Set `SHIPCUBE_MODEL_URI` to the previous `<version>` (versions are immutable) and restart |
