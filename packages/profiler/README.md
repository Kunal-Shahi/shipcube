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
- **Agent and app are separate users.** The agent is root inside the container. Locally it keeps only the SETUID, SETGID, CHOWN and KILL capabilities; Cloud Run uses its default container capabilities. It starts the app as uid 10001, which keeps no capabilities, so the app can't signal, ptrace or read the agent.
  - The sampler thread reads the app's `/proc` entries with `setfsuid`/`setfsgid` set to the app's uid, so the agent needs no `CAP_SYS_PTRACE`.
  - Signals the app aims at the agent count as `tamper_attempts`, which raises the abuse signal `agent_tamper`.
  - `docker run --init` keeps the agent off PID 1, so its protection comes from the uid split, not from PID 1's immunity to signals.
- **No privilege gain.** The app runs with `PR_SET_NO_NEW_PRIVS` (set in its `preexec_fn`), and the image has every setuid and setgid bit stripped, since Cloud Run has no no-new-privileges flag.
- **No symlinks.** Git checkouts contain none (see above); in uploads every symlink is deleted before anything reads the tree.
- **Static fallbacks:** `estimate()` reports `profile.measurements.static_reason`, one of `STATIC_REASONS`. The control plane forces one with `StaticRunner(reason="quota" | "budget" | "suspended" | "busy" | "disabled")`.
- **Abuse signals:** `profile.measurements.abuse_signals`, computed host-side by `abuse_signals(profile)`, for the control plane's strikes.
- **Git sources:**
  - `https://` only. `file://`, ssh, `git://` and `ext::` are refused; `materialize(..., allow_file_urls=True)` exists for tests.
  - The host must be public. It's resolved once, every address must be public (no private, loopback, link-local/metadata, CGNAT or reserved ranges), and git then connects to that checked address (`http.curloptResolve`) with redirects off, so DNS rebinding or a redirect can't reach an internal host.
  - Clones are shallow: `--depth 1 --single-branch --no-tags`.
  - Three steps:
    1. clone `--no-checkout`;
    2. sum the blob sizes from `git ls-tree -r -l` and refuse past 256 MiB or 20,000 files *before* anything is written;
    3. check out exactly those bytes: the repo's `.gitattributes` are ignored (`GIT_ATTR_SOURCE` set to the empty tree, and also dropped from the index for git < 2.40), along with `core.attributesFile`, autocrlf and eol, so `working-tree-encoding` can't inflate it. `core.symlinks=false` makes links small plain files holding their target path, and any link left over is refused.
  - Files over 50 MiB aren't fetched (`--filter=blob:limit` plus `GIT_NO_LAZY_FETCH`).
  - One 120 s deadline covers all three steps, a size watchdog stays as a backstop, and a partial clone is removed.
  - `subdir` must resolve inside the checkout without passing through a link.
  - No submodules or LFS, and the worker's system and global git config is ignored.
- **Uploads:** checked for unpacked size (1 GiB) and entry count (20,000) before extraction.

## Phase 2 build isolation (#22)

- **Offline build.** Cloud Build runs `fetch-wheels`, then `docker build --network=none`, then `size-check`.
  - The fetch step is the only one with any network. It downloads wheels only, and pip checks each file against the sha256 the index publishes.
  - The image then installs only those files (`--no-index`).
- **Build identity.** A build service account is required; Cloud Build's default SA is never used. Per run, the caller can point the build at the workspace's own builder SA (`sc-build-<ws>`), its uploads folder (`source_prefix`), its repo (`repository`) and its logs folder (`build_logs`). `CloudRunJobRunner(build_service_account=..., bucket=..., source_prefix=..., repository=..., build_logs=...)`.
- **No internet (`offline_builds` in `infra/terraform/modules/profiler/build.tf`, off by default):**
  - wheels come from an Artifact Registry PyPI mirror (`SHIPCUBE_PROFILER_PIP_INDEX`) and the base image from a Docker Hub mirror (`SHIPCUBE_PROFILER_BASE_IMAGE`);
  - builds run on a private pool with no external IP (`SHIPCUBE_PROFILER_WORKER_POOL`).

## Abuse pre-screen

`prescreen(app_dir) -> Verdict` runs in `estimate()` before anything is built or run: file names, requirements and a few regexes from `prescreen_patterns.py` (the one file to review), plus two structural checks (mail sent in a loop; login pages or handlers that send credentials elsewhere).

- **High confidence** (`crypto_miner`, `attack_tool`, `phishing_kit`): `estimate()` raises `AbuseRefused` (`.codes`, `.verdict`). Platform enforces the same verdict at deploy.
- **Low confidence** (`mining_pool_reference`, `network_scanner`, `flood_script`, `bulk_mail`, `phishing_suspect`): allowed, added to `profile.measurements.abuse_signals` as `prescreen_<code>` (strikes).
- The verdict is in `profile.prescreen`. Hits name the rule and the file, never the matched text.

## Configure

| Variable | Purpose |
|---|---|
| `SHIPCUBE_PROFILER` | `gcp`, `local` (Docker sandbox) or `inproc` (DEV_MODE only) |
| `SHIPCUBE_PROFILER_DOCKER`, `_CPUS`, `_MEMORY_MB`, `_PIDS`, `_TIMEOUT_S`, `_TMPFS_MB`, `_DISK_MB`, `_BUILD_TIMEOUT_S` | Local sandbox limits; the defaults match the Cloud Run job |
| `SHIPCUBE_DEV_MODE` | `1` for in-process runs only |
| `SHIPCUBE_PROFILER_PROJECT`, `_REGION`, `_BUCKET`, `_REPOSITORY`, `_NETWORK`, `_SUBNET`, `_SERVICE_ACCOUNT` | Required in production |
| `SHIPCUBE_PROFILER_BUILD_SERVICE_ACCOUNT` | **Required** build identity (never Cloud Build's default SA) |
| `SHIPCUBE_PROFILER_SOURCE_PREFIX`, `_BUILD_LOGS` | Per-workspace source folder and `gs://` logs folder (phase 2) |
| `SHIPCUBE_PROFILER_PIP_INDEX`, `_BASE_IMAGE`, `_WORKER_POOL` | AR mirrors and the private pool (phase 2; Terraform outputs) |

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
