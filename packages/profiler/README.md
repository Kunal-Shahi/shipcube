# shipcube-profiler

Runs an app on sample input and measures CPU, RSS, disk and network I/O, wall time, process count and outbound network calls. It then maps the profile, plus the `expected_users`, `schedule` and `traffic` hints, onto the classifier's features.

**Components:**
- `materialize(source, dir)`: handles `inline`, `git` and `upload` sources; `image` raises `NotRunnable`.
- `summarize(dir)`: static inspection (entrypoint, framework, routes, `api_only`, libraries).
- `CloudRunJobRunner` (production): Cloud Build image → Cloud Run v2 job in a gen2, no-egress sandbox → result read from Cloud Logging → cleanup. Failures raise `ProfilerError(stage)`.
- `LocalRunner`: a subprocess with psutil, rlimits, a scrubbed env and a no-egress socket/DNS hook.
  - It runs inside the job sandbox (`agent.py`).
  - It's used directly only with `SHIPCUBE_DEV_MODE=1`.
  - On its own it's not a security boundary.
- `StaticRunner`: typical numbers when code can't run (`mode: "static"`).
- `default_runner()`: `CloudRunJobRunner.from_env()`, or `LocalRunner` in DEV_MODE.

Tests and the demo use two sample apps: `samples/dashboard` (Flask, bursty) and `samples/nightly_batch` (a nightly job).

## Configure

| Variable | Purpose |
|---|---|
| `SHIPCUBE_DEV_MODE` | `1` for local runs only |
| `SHIPCUBE_PROFILER_PROJECT`, `_REGION`, `_BUCKET`, `_REPOSITORY`, `_NETWORK`, `_SUBNET`, `_SERVICE_ACCOUNT` | Required in production |
| `SHIPCUBE_PROFILER_BUILD_SERVICE_ACCOUNT` | Optional build identity |

## Deploy

```bash
# Resources: managed by Terraform in infra/ (spec: deploy/RESOURCES.md). Set SHIPCUBE_PROFILER_* from its outputs.
SHIPCUBE_GCP_IT=1 pytest -k integration      # real-GCP integration test
```

## Operate

- **Events:** `profile.gcp`, `profile.completed`, `profile.failed` (with `stage`), `gcp.retry`.
- **Jobs** are named `prof-<estimate id>` and deleted after each run.
- **Source tarballs** expire after 1 day, and **images** after 7 days.

The runbook is in [docs/research/load-estimation.md](../../docs/research/load-estimation.md) §9.4.
