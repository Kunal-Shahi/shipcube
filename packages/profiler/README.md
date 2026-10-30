# shipcube-profiler

Runs an app on sample input and measures CPU, RSS, disk and network I/O, wall time, process count and outbound network calls. It then maps the profile, plus the `expected_users`, `schedule` and `traffic` hints, onto the classifier's features.

**Components:**
- `materialize(source, dir)`: handles `inline`, `git` and `upload` sources; `image` raises `NotRunnable`.
- `summarize(dir)`: static inspection (entrypoint, framework, routes, `api_only`, libraries).
- `CloudRunJobRunner` (production): Cloud Build image → Cloud Run v2 job in a gen2, no-egress sandbox → result read from Cloud Logging → cleanup. Failures raise `ProfilerError(stage)`.
- `LocalRunner`: a subprocess with psutil, rlimits, a scrubbed env and a no-egress socket/DNS hook.
  - It runs inside the job sandbox (`agent.py`).
  - It's used directly only with `SHIPCUBE_PROFILER=inproc` plus `SHIPCUBE_DEV_MODE=1`.
  - On its own it's not a security boundary.
- `StaticRunner`: typical numbers when code can't run (`mode: "static"`).
- `DockerRunner` (local stack): the same image as the Cloud Run job, run with `docker run --network none --read-only --user 10001 --cap-drop ALL --cpus 1 --memory 2048m --pids-limit 256` and a timeout. Build-time requirements, the same agent and metrics.
- `runner_from_env()`: picks one by `SHIPCUBE_PROFILER`: `gcp` (Cloud Run job), `local` (Docker), or `inproc` (only in DEV_MODE).

Tests and the demo use two sample apps: `samples/dashboard` (Flask, bursty) and `samples/nightly_batch` (a nightly job).

## Sandbox safety (docs/research/profiler-sandbox.md, Phase 1)

- **Build** (`policy.py`):
  - `requirements.txt` is sanitized to plain PyPI requirements: no URLs, VCS, paths, options or custom indexes.
  - Installs are wheels-only (`--only-binary=:all:`) from pypi.org, so no package code runs at build time.
  - Caps: at most 60 requirements, 500 MiB / 10,000 source files, a 300 s build, and a 2 GiB image (checked by a `size-check` build step, or `docker image inspect` locally).
- **Run** (the agent, inside the container):
  - existing limits: CPU, memory (RLIMIT_AS plus the RSS watchdog) and file size,
  - plus `RLIMIT_NPROC` 256, a 512 MiB disk-write watchdog, and stderr kept as a 64 KiB tail (drained continuously).
- **Result integrity:**
  - Each run gets a random nonce (`SHIPCUBE_RESULT_NONCE`), and the runner accepts only the result line carrying it.
  - The agent makes itself non-dumpable (`prctl(PR_SET_DUMPABLE, 0)`). The app, running as the same uid, then can't read the agent's `/proc/<pid>/environ` or write to its stdout.
  - A forged line yields `static_reason=result_rejected`.
- **Agent integrity:** the app and its wheels can't become the agent.
  - The app's deps are installed by an unprivileged user with `pip --target /opt/appdeps`. That isn't a site dir, so no `.pth` runs, and the stdlib and site-packages are read-only. They're on the app subprocess's `PYTHONPATH` only.
  - The agent runs as `python -S -P` from a read-only `/opt/shipcube` with its own psutil, with `WORKDIR /`, so the upload is never on its `sys.path`.
  - The app gets `PYTHONNOUSERSITE=1`.
  - Tested with an upload that shadows the agent's modules and with a local malicious wheel (`.pth`, a `.data` stdlib overwrite, a psutil shadow). Against the previous image design both attacks got into the agent; now both runs are genuine.
- **Static fallbacks:** `estimate()` reports `profile.measurements.static_reason`, one of `STATIC_REASONS`. The control plane forces one with `StaticRunner(reason="quota" | "budget" | "suspended" | "busy" | "disabled")`.
- **Abuse signals:** `profile.measurements.abuse_signals`, computed host-side by `abuse_signals(profile)`, for the control plane's strikes.
- **Uploads:** checked for unpacked size (1 GiB) and entry count (20,000) before extraction.

## Configure

| Variable | Purpose |
|---|---|
| `SHIPCUBE_PROFILER` | `gcp`, `local` (Docker sandbox) or `inproc` (DEV_MODE only) |
| `SHIPCUBE_PROFILER_DOCKER`, `_CPUS`, `_MEMORY_MB`, `_PIDS`, `_TIMEOUT_S`, `_TMPFS_MB`, `_DISK_MB`, `_BUILD_TIMEOUT_S` | Local sandbox limits; the defaults match the Cloud Run job |
| `SHIPCUBE_DEV_MODE` | `1` for in-process runs only |
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
