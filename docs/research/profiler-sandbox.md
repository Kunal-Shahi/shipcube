# Profiler sandbox in production: running untrusted apps safely

Status: design, not implemented. No GCP changes were made for this doc. Owner: load-estimator workstream (`packages/profiler/**`, `packages/placement/**`), with asks of infra/security (`infra/**`) and platform (`services/api/**`).

## The question

> "Someone can send anything bursty, fishy, or a very large system that may bring the clusters down. How can we ensure the safety of our as well as other users' applications?" (Kunal)

Today `estimate_load` in production only does static analysis: `services/api/shipcube_api/estimator.py` uses `StaticRunner` unless `SHIPCUBE_PROFILER_JOB` is set. This doc covers what has to be true before that variable is set, and the profiler really runs user code.

The answer in one paragraph:
- **Where it runs.** A user's code runs only in a throwaway Cloud Run job in `shipcube-apps`, as a service account with no roles, on a network with no way out.
- **What one run can do.** Every run has fixed caps on CPU, memory, processes, disk, wall clock, build time, image size and self-generated load. The worst one run can do is waste about 10 cents and 25 minutes.
- **What one tenant can do.** Per-workspace and global admission limits stop one tenant from monopolising the profiler or the worker. A spend ledger turns the profiler off before the billing kill switch has to.
- **Abuse.** Signals measured during the run (egress attempts, CPU pinned without work, process storms) feed an auto-suspend.
- **Fallback.** Anything that can't be run safely gets a static estimate, flagged as such.

**The biggest gap today is the build, not the run.** The run is already well isolated. The Cloud Build step that installs the app's requirements:
- runs arbitrary code from the user's dependencies (sdists' `setup.py`, `-e git+…`, custom index URLs),
- with internet egress, for up to 15 minutes,
- as a build identity shared by every tenant.

Section 4 closes it.

---

## 1. Threat model

| What a user might send | What it could hurt if unchecked |
|---|---|
| A fork bomb, a memory bomb, a disk filler | The node, other tenants on the node, our quota |
| A crypto miner (at build or run time) | Our bill; Google's abuse systems could suspend the billing account, which takes every tenant down |
| Exfiltration or phone-home code | Data from our environment or other tenants; our IPs used for attacks |
| A dependency with a malicious `setup.py` | The build identity's credentials, other tenants' sources in the build bucket |
| A huge app: many dependencies, a multi-GB image, slow start | Build minutes, registry storage, worker time |
| Thousands of `estimate_load` calls | The single serial worker (deploys of every tenant queue behind it); project quotas shared with hosted apps; cost |
| A forged `SHIPCUBE_PROFILE` result line | A wrong estimate for its own app (sizing and caps are bounded later, at deploy) |
| Log flooding | Cloud Logging cost; the 429-prone Logging read quota the runner depends on |

Out of scope: a vulnerability that breaks out of Cloud Run's sandbox itself (Google's responsibility), and attacks on hosted apps that don't go through the profiler (covered by `docs/research/security-tenancy.md`).

## 2. What's enforced today vs missing

This is the short list asked for. "Enforced" means in code or in Terraform on `main` today. Profiler-specific Terraform is `infra/terraform/modules/profiler/main.tf`; the run is `packages/profiler/src/shipcube_profiler/gcp_runner.py`.

| Control | State | Where |
|---|---|---|
| Sandbox runs in `shipcube-apps`, never in the control project | **Enforced** | `module "profiler"` → `project_id = google_project.apps` |
| Runtime SA with zero roles and no secrets | **Enforced** | `profiler-sandbox` SA, no IAM bindings; `job_spec` sets no secrets |
| Deny-all egress: Direct VPC `ALL_TRAFFIC` into `profiler-no-egress`, Private Google Access off | **Enforced**; verified on GCP (the sample's webhook was blocked) | module `main.tf`; `job_spec` |
| No host or worker environment reaches the run | **Enforced** | `job_spec` env is the run id and hints only |
| 1 vCPU, 2 GiB, 600 s task timeout, `maxRetries 0`, `taskCount 1`, `parallelism 1` | **Enforced** | `job_spec` |
| Image pinned by digest; job and source deleted after the run; images expire after 7 days | **Enforced** | `_build`, runner cleanup, AR cleanup policy |
| In-container rlimits (CPU seconds, file size, address space) and an RSS watchdog; app stdout discarded | **Enforced** | `runners.py` `_preexec`, sampler, `stdout=DEVNULL` |
| Self-generated load capped: 3 bursts of 40 GETs over loopback, concurrency 8 | **Enforced** (fixed, not user-tunable) | `runners.py` `_load` |
| Non-root (uid 10001), read-only `/app` | **Enforced** in the image; the root filesystem is writable in Cloud Run (no `--read-only` equivalent) | `DOCKERFILE` |
| Inline source ≤ 2 MB / 100 files; upload ≤ 100 MiB | **Enforced** | `models.py`, `deploy/gcp.py` |
| Billing kill switch (unlinks billing at INR 24,000 gross) | **Enforced**, but it's account-wide and lags by hours | `infra/budget-killswitch`, `budget.tf` |
| Build step without arbitrary code or egress | **Phase 1**: wheels-only from pypi.org, sanitized requirements, so no package code runs. The build still has internet egress (phase 2) | `policy.py`, `DOCKERFILE` |
| Build identity scoped per tenant | **Missing**: one `profiler-build` SA reads all of `profiler-src/` | module `main.tf` |
| Image-size, dependency-count and build-time caps | **Phase 1**: 2 GiB image (`size-check` build step), 60 requirements, 500 MiB / 10,000 source files, 300 s build | `policy.py`, `build_spec` |
| Per-workspace and global profiler concurrency and rate limits | **Missing** | none; the claim query has no per-kind or per-workspace limit |
| Estimates kept off the deploy path | **Missing**: one serial worker; a profiled estimate can hold it for about 25 min | `worker.py` |
| Profiler spend ledger / soft budget stop | **Missing** | none |
| Abuse signals → auto-suspend | **Phase 1, profiler side**: `profile.abuse_signals` is computed per run. Strikes and suspension belong to Platform | `abuse.py` |
| Janitor for orphaned jobs and builds | **Missing** (cleanup happens only in the runner's own `finally`) | none |
| Separate project or quota for the profiler | **Missing**: shares regional Cloud Run, Cloud Build and Logging quotas with hosted apps | none |

**Naming:** the runtime SA is `profiler-sandbox@` (Terraform). `infra/README.md` now says so. Phase 1 fixed the README, which used to call it `sc-profiler`. A comment in `services/api/shipcube_api/settings.py` still uses the old name; that file is Platform's.

## 3. Blast radius: where a run can reach

The design keeps what is already in place and closes the two remaining paths.

```
 worker (control project, sc-worker)
   │  creates build + job, reads one log line, deletes job
   ▼
 shipcube-apps
   ├─ Cloud Build (profiler-build-<ws>)  ── egress: PyPI only, wheels only   (§4, new)
   │     └─ pushes prof-<id> to AR repo `profiler` (by digest)
   └─ Cloud Run job prof-<id>  (gen2, profiler-sandbox: 0 roles)
         └─ VPC profiler-no-egress: deny 0.0.0.0/0, no PGA, no peering, no NAT
```

- **Project.** The run is in `shipcube-apps`, and the control project is unreachable: there's no route and no identity. It never runs in `shipcube-control`.
- **Identity.**
  - `profiler-sandbox` has no project or resource roles.
  - Cloud Run always exposes the metadata server, and it can't be turned off. So the app can mint a token for this SA.
  - That token is worthless for two reasons. The SA has no grants. And with Private Google Access off and the deny-all firewall, `*.googleapis.com` is unreachable anyway. Both layers have to fail for a token to matter.
  - **Rule to keep:** never grant this SA anything, including `logging.logWriter`. Cloud Run captures stdout without it.
- **Network.**
  - There's no VPC peering, Cloud NAT, Serverless VPC connector or PSC endpoint on `profiler-no-egress`. Add a Terraform test asserting that none exist, and that the firewall has no allow rule.
  - Hosted apps are reachable only through their public URL behind IAP, which needs egress. So the profiler has no network path to another tenant.
- **Isolation from other tenants' apps.**
  - Every run is its own Cloud Run job, a separate resource from any tenant's service, with its own instance.
  - Cloud Run never co-locates two instances in one sandbox.
  - **Correction to the brief:** gVisor is Cloud Run's *first-generation* environment. `job_spec` pins **gen2**, which per Google's docs runs each instance in a VM-based sandbox with a full Linux kernel instead of gVisor's syscall emulation. Both are hardened per-instance sandboxes, and neither shares a kernel with another tenant's instance.
  - Switching the profiler to gen1 (gVisor) is possible if Direct VPC egress works on gen1 jobs, which needs checking. It would trade a smaller kernel surface for less accurate measurement, because psutil readings under gVisor are emulated. Recommendation: stay on gen2. It matches what hosted apps run on, so the numbers transfer.
- **Credentials and data.**
  - No secrets, no mounted volumes, no environment beyond the run id and hints.
  - `sample_input` is never forwarded (already true; the local Docker test asserts it).
- **Shared things that remain**: project quotas, the AR repo, the build identity and Cloud Logging. These are the real blast radius, and §5 and §7 address them. The durable fix is a **dedicated `shipcube-profiler` project**:
  - the same module with a different `project_id`,
  - its own quotas,
  - on the kill switch's `PROJECTS` list.

  Then exhausting profiler quota can't stop a tenant's app from scaling. Recommended as phase 3. It's cheap, but it's a new project, so it needs Kunal's OK.

## 4. Build stage: the biggest gap

Today `DOCKERFILE` runs `pip install -r /app/requirements.txt` as root on Cloud Build's default pool, with internet and a 900 s timeout. That means:
- any sdist's `setup.py` executes arbitrary code,
- `-e git+https://…`, `--index-url` and `--extra-index-url` fetch from anywhere,
- a miner could run for 15 minutes on our build minutes.

Design:

1. **Wheels only, PyPI only.**
   - The packager rewrites requirements before building:
     - keep plain `name[extras] <specifier>` lines,
     - drop or deny options lines (`-i`, `--extra-index-url`, `--trusted-host`, `-f`, `-e`, `-r`, `-c`) and URL or VCS requirements.
   - The Dockerfile runs `pip install --only-binary=:all: --no-cache-dir -r requirements.lock`. Installing a wheel executes no package code, so nothing user-controlled runs at build time.
   - A dependency without a wheel for `manylinux`/py3.12 fails the build. The estimate then falls back to static, noted as `profile.mode=static, static_reason=build_failed`.
   - Deny-list by default; an allow-list per organisation is a later option.
2. **Caps.**

   | Cap | Value |
   |---|---|
   | Build timeout | 900 s → **300 s** |
   | Requirement lines | ≤ 60 |
   | Final image | ≤ 2 GiB, checked from the build result's image size; a larger image is deleted and the estimate goes static |
   | Build machine | the default `e2-standard-2`; never a large machine type |

3. **Egress at build time.** Wheels-only already removes code execution. To also remove arbitrary egress, run builds in a Cloud Build private pool on a VPC whose egress is allowed only to PyPI (via a proxy or Artifact Registry remote repository of PyPI). The remote repo is simpler: AR `pypi-remote` in `shipcube-apps`, and the build gets `PIP_INDEX_URL` pointing at it and no internet.
   - Phase 2, because private pools cost an always-on worker. Measure first.
4. **Per-workspace build identity.**
   - Reuse the C-1 pattern that hosted builds already use (`sc-build-<id>`): each workspace gets `profiler-build-<ws>`.
   - Its bucket read is scoped to the `profiler-src/<ws>/` prefix, and the runner uploads there. It gets AR write on `profiler` limited to the `prof-<ws>-*` image prefix through a tag condition, or one AR repo per workspace if conditions on AR don't allow it.
   - Until then, the shared `profiler-build` SA can read any tenant's `profiler-src/` tarball for up to one day. That's acceptable only while build steps run no user code, so (1) must ship first.
5. **The agent's interpreter stays pristine.** Added after the red team's review of #57. Wheels-only stops code running at build time, but a wheel from PyPI could still ship a `.pth` file, overwrite stdlib files through `.data`, or shadow psutil, and so take over the agent at run time. So:
   - the app's deps install as an unprivileged user with `--target /opt/appdeps`, on the app's `PYTHONPATH` only;
   - the agent runs `python -S -P` with `WORKDIR /` from a read-only `/opt/shipcube` that bundles psutil.
6. **The agent and the app run as different users.** Added after the red team's review of #57. The agent is root in the container and starts the app as uid 10001, which keeps no capabilities, so the app can't stop, kill, ptrace or read the agent. Signals the app aims at the agent are counted and raise `agent_tamper`.
7. **The Dockerfile stays generated.** User files are copied to `/app` and never interpreted as a Dockerfile; this is already true. A user `Dockerfile` in the upload is ignored.

## 5. Hard caps per run

| Cap | Value | Enforced by | Today |
|---|---|---|---|
| CPU | 1 vCPU | Cloud Run `limits.cpu`; `RLIMIT_CPU` | yes |
| Memory | 2 GiB (container), agent kills the app at 90% | Cloud Run `limits.memory`; RSS watchdog; `RLIMIT_AS` | yes |
| Processes / threads | ≤ 256 | **new:** agent sets `RLIMIT_NPROC` in `_preexec` (Cloud Run has no pids limit) | no |
| Disk | ≤ 512 MiB written; `/tmp` is in-memory and counts against the 2 GiB | `RLIMIT_FSIZE` per file; **new:** agent watches total bytes written and kills the app | partial |
| Instances | 1 (`taskCount 1`, `parallelism 1`); a job has no autoscaling | job spec | yes |
| Retries | 0 | job spec | yes |
| Run wall clock | Cloud Run task timeout **600 s**; agent stops the app at 570 s and still reports | job spec + agent | yes |
| Script run time | stopped after 120 s; duration recorded as a lower bound | agent `Limits.timeout_s` | yes (configurable) |
| Web start-up | port must open within 20 s | agent `_wait_port` | yes |
| Load generator | 3 bursts × 40 GETs, concurrency 8, loopback only; **ceiling: 40 req/s, 120 requests per run**, not tunable by hints | agent `_load` | yes (make the ceiling an assertion) |
| Build time | **300 s** | `build_spec.timeout` | 900 s |
| Image size | **≤ 2 GiB** | runner checks after the build | no |
| Source size | inline 2 MB / 100 files, upload 100 MiB, unpacked ≤ 500 MiB / 10,000 files | API models; **new:** unpack check in `sources.py` | partial |
| Stderr captured | last 64 KiB only | **new:** ring buffer in the agent (today `stderr=PIPE` is read whole, so a chatty app can fill the pipe and stall) | no |
| End to end | worker gives up after 20 min and deletes the job and build | runner deadline + janitor (§8) | partial |

An `expected_users` of 1,000,000 doesn't change the run: the load generator is fixed, and hints only change the traffic model used afterwards. That's what makes the load-generator ceiling unconditional.

## 6. Concurrency and rate limits: one tenant can't starve others

**Problem today.**
- `worker.py` claims operations in `next_attempt_at` order, with no per-kind or per-workspace limit.
- The production worker is a single service running one operation at a time.
- A profiled estimate (build up to 15 min, run up to 10 min, then the log read) blocks every tenant's deploy behind it. Ten estimates from one tenant block deploys for hours.

**Design.**

1. **Separate lane.**
   - Estimates are claimed only by a profiler worker: same code, `SHIPCUBE_WORKER_KINDS=estimate`, its own Cloud Run service or thread pool.
   - Deploy workers claim `kind <> 'estimate'`.
   - This is one extra predicate in `claim()`. Estimate latency no longer touches deploy latency.
2. **Admission at the API** (`estimate_load` enqueue):

   | Limit | Default | When exceeded |
   |---|---|---|
   | Profiled estimates in flight per workspace (queued + running) | 2 | `rate_limited` (HTTP 429) with `retry_after` |
   | Profiled estimates per workspace per day | 20 | the estimate runs **static-only**, with `profile.mode=static`, `reason=quota` |
   | Same source hash + hints within 7 days | n/a | return the cached profile, without running anything |

   Static estimates aren't rate-limited beyond the API's general limits; they cost nothing but CPU in the worker.
3. **Global ceiling in the claim query.**
   - At most **3** profiler runs at once (Cloud Run job executions plus builds), and at most 1 running per workspace.
   - Expressed in SQL in the claim: `… and (select count(*) from operations r where r.kind='estimate' and r.status='running' and r.locked_until > now()) < 3 and not exists (… same workspace_id running)`, with `pg_advisory_xact_lock` around the claim to make the count race-free.
   - Fairness: order by each workspace's last-run time, then `next_attempt_at`, so a workspace with 20 queued estimates doesn't go ahead of one with 1.
4. **Queue age.** If an estimate has waited more than 10 minutes for a profiler slot, it completes as static, with `reason=busy`. The agent gets an answer instead of a stall.
5. **Quotas outside our control.** Cloud Build's concurrent-build quota, Cloud Run job execution limits and the Logging read quota (which already returned 429 once) are shared with hosted apps while the profiler lives in `shipcube-apps`. The global ceiling of 3 keeps the profiler's share small; the dedicated project (§3) removes the sharing.

## 7. Budget guard

Worst-case cost of one run at list prices (`pricing.json`, fetched 2026-10-27; Cloud Build from its pricing page, to be added to `pricing.json`):

| Piece | Worst case |
|---|---|
| Cloud Run job: 600 s × (1 vCPU × $0.000018 + 2 GiB × $0.000002) | $0.013 |
| Cloud Build: 300 s at about $0.006/build-minute (`e2-standard-2`, after the free 2,500 min) | $0.03 (900 s today: $0.09) |
| Artifact Registry: ≤ 2 GiB × 7 days | < $0.01 |
| Logging: one line, plus build logs | ~0 |
| **Total** | **≈ $0.05 per run (≈ $0.11 with today's 900 s build)** |

Design, in three layers:
1. **Spend ledger (new, immediate).**
   - The worker adds the worst-case cost of each run to a `profiler_spend` row (day, workspace) when it claims the run. It doesn't wait for the bill.
   - When today's global total crosses **$5** (about 100 worst-case runs), new estimates go static with `reason=budget`.
   - When a workspace crosses **$1**, the same applies to that workspace.
   - It reacts in seconds; Google billing data lags by hours.
2. **Label budget (new, reconciliation).**
   - A second budget, filtered to label `shipcube-role=profiler`, already set on jobs. Builds need the same label through `build_spec.tags` plus a substitution, or the dedicated project makes the filter `projects/shipcube-profiler`.
   - Monthly amount: $50, alerting at 50%, 90% and 100% to the existing Pub/Sub topic.
   - The kill switch function ignores this budget, and a small handler sets `profiler_enabled=false` instead. Shutting off billing is reserved for the account-wide cap.
3. **Kill switch (exists).** The account-wide hard cap still covers everything, the profiler included. Nothing here weakens it.

The ledger and admission state live in the API database (platform-owned tables), so `estimate_load` can say why it went static.

## 8. Abuse signals and auto-kill

**During the run (agent, already measured or cheap to add).** Each signal is written into the profile and acted on by the worker:

| Signal | Threshold | Action |
|---|---|---|
| Blocked egress attempts (`net_blocked`) | > 50, or any attempt to known mining-pool hosts or stratum ports (3333, 4444, 5555, 14444) | Kill the run now; **strike**; a mining match is a **suspend** |
| CPU pinned at ≥ 0.95 cores for > 60 s with no requests in flight (web) or no file output (script) | sustained | Strike; reported as `suspected_miner` |
| `processes_max` ≥ 200 or `RLIMIT_NPROC` hit | any | Kill; strike (fork bomb) |
| Memory watchdog or `RLIMIT_AS` kill | any | Normal outcome (memory_heavy), **not** a strike on its own |
| Disk-write watchdog | > 512 MiB | Kill; strike |
| Timeout without ever binding the port (web) | any | No strike; estimate goes static, `reason=did_not_start` |

**Around the run (worker and API).**
- **Strikes.**
  - 3 strikes in 24 h means the workspace is profiler-suspended: static-only for 24 h, plus an ops alert. A suspend from a mining match lasts until a human clears it.
  - Strikes and suspensions are rows in the API database, shown by `python -m shipcube_api.admin`.
- **Repeat sources.** The same source hash after a strike is refused for 24 h.
- **Janitor**, in worker housekeeping, every minute:
  - delete Cloud Run jobs labelled `shipcube-role=profiler` older than 30 min,
  - cancel builds tagged `shipcube-profiler` older than 10 min,
  - delete `profiler-src/` objects older than 1 h (the lifecycle rule's 1 day is the backstop).

  This covers a worker that crashed between creating the job and its `finally`.
- **Global brake.**
  - If more than 5 profiler job executions exist at once (the ceiling is 3), stop claiming estimates and alert. Something bypassed admission.
  - `SHIPCUBE_PROFILER_ENABLED=0` is a manual off switch read on every claim. Off means static.
- **Forged result lines.**
  - The app's stdout is discarded and its stderr captured by the agent, so the app can't print `SHIPCUBE_PROFILE` through its own streams.
  - It runs as the same uid as the agent, though, so in principle it could write to the agent's stdout through `/proc/<agent pid>/fd/1`.
  - Fix: the agent prints the line with a per-run nonce passed in an env var only the agent reads and then removes from the app's environment. The runner accepts only the line carrying that nonce. It's cheap.
  - The remaining risk is a tenant misleading its own estimate. That's bounded: deploy-time caps are clamped and the user can always choose the tier.

### Static pre-screen (added Oct 31)

Before anything is built or run, `shipcube_profiler.prescreen` scans the upload's files and requirements for high-confidence abuse markers. The patterns are in one file, `prescreen_patterns.py`.

- **Refuse** (`AbuseRefused`), for markers a legitimate internal app essentially never contains:
  - `crypto_miner`: miner binaries (xmrig and similar), `stratum+tcp://` URLs, xmrig flags and configs;
  - `attack_tool`: masscan, zmap, hping3 floods, slowloris and similar tools or packages;
  - `phishing_kit`: a login page with a password field that sends credentials to an exfiltration endpoint (Telegram bot, Discord or Slack webhook, form relays), or impersonates a brand and posts off-site; or server code that forwards a submitted password to one of those endpoints.
- **Flag only**, as a strike: `prescreen_mining_pool_reference` (dashboards query pool APIs), `prescreen_network_scanner` (nmap wrappers have admin uses), `prescreen_flood_script`, `prescreen_bulk_mail` (mail sent in a loop over a file or list), and `prescreen_phishing_suspect` (a brand login look-alike without exfiltration).
- **Benign look-alikes stay clean,** and tests cover them:
  - one contact-form email, and a short fixed notify list;
  - a `requests` app;
  - an own-backend login, and an external SSO login;
  - a Slack alert;
  - docs that mention DDoS;
  - a webhook app that reads `request.json` next to Python's `pass`. That one was a false positive the security review found: forwarding a submitted password to a webhook is now a strike, not a refusal.
- **Cost is bounded:**
  - in-tag regex scans are bounded (unbounded `[^>]*` was quadratic: minutes for a crafted 1 MB page);
  - text is scanned for at most 256 KB per file, 8 MB in total and 5 s, after which the verdict is marked `incomplete`.
- **Limits.** This isn't a security boundary: trivial obfuscation (concatenated strings, base64, renamed binaries, packed archives) gets past every rule. It catches careless abuse cheaply. The sandbox (§3–5) and run-time abuse signals (§8) are what hold against the rest.

## 9. What stays static-only

These sources are never run. They get a static estimate with `profile.mode=static` and a machine-readable `reason`, so the agent and the user know why:

| Case | `reason` |
|---|---|
| `image` sources: we didn't build them and can't vouch for what's inside | `image_source` |
| GPU libraries detected (torch with CUDA, etc.): the sandbox has no GPU, and a CPU run would mislead | `gpu` |
| No entrypoint found (including non-Python code, since the agent profiles Python only) | `no_entrypoint` |
| A requirement with no wheel, or the build over 300 s | `build_failed` |
| A disallowed requirements line (URL, VCS, path, option, custom index, non-PEP 508 marker) | `requirements_policy` |
| A script that exits with an error | `app_failed` |
| A result line without this run's nonce (tampering; also abuse signal `result_forged`) | `result_rejected` |
| GCP or logging failure in the profiler itself | `profiler_error` |
| Image > 2 GiB, more than 60 requirements, unpacked source > 500 MiB | `too_large` |
| Workspace over its daily quota, over its spend share, or suspended | `quota` / `budget` / `suspended` |
| Global ledger over the daily cap, or the profiler switched off | `budget` / `disabled` |
| Waited more than 10 min for a slot | `busy` |
| Web app didn't open its port in 20 s (often it needs a DB or secret it can't have in the sandbox) | `did_not_start` (the run happened; its numbers aren't used) |

Apps that need network access at run time (a database, an external API) still run. Their blocked calls are counted and named in the reasoning, as the sample batch job's webhook is today. Their measured numbers describe the app without those dependencies, and the estimate says so.

### Phase 1 interface (implemented)

- **Where it shows.** `EstimateResult.profile["measurements"]` carries:
  - `mode`: `measured` or `static`
  - `static_reason`: a code from the table above, or null
  - `abuse_signals`: a subset of `egress_flood`, `mining_pool`, `fork_bomb`, `disk_flood`, `cpu_pinned`, `result_forged`
- **Profiler codes.** The profiler sets them itself. The constants are in `shipcube_profiler.STATIC_REASONS`.
- **Control-plane codes.** For quota, budget, suspended, busy and disabled, the control plane passes `runner=StaticRunner(reason=<code>)` to `estimate()`.

## 10. Rollout

Each phase is shippable and testable on its own. `SHIPCUBE_PROFILER_JOB` is set in production only after phase 1.

| Phase | Work | Owner (paths) | Test |
|---|---|---|---|
| **1. Safe to turn on** | Wheels-only/PyPI-only requirements and 300 s build timeout (§4.1–4.2); `RLIMIT_NPROC`, disk watchdog, stderr ring buffer, result nonce (§5, §8); static fallbacks with `reason` (§9); runner deadline | load-estimator (`packages/profiler/**`) | Unit: requirements policy, image-size refusal. Docker sandbox: fork bomb is killed, a disk filler is killed, a forged result line is rejected. GCP integration test re-run as `sc-worker` |
| | Separate estimate lane, admission limits, global ceiling of 3, fairness, queue-age fallback (§6); spend ledger (§7.1); janitor (§8) | platform (`services/api/**`) | Worker tests: a fourth concurrent estimate waits; a workspace's third queued estimate gets 429; ledger over the cap → static |
| | Terraform test: no allow-egress rule, no NAT, connector or peering on `profiler-no-egress`; zero bindings on `profiler-sandbox` | infra/security (`infra/**`) | `infra/tests` |
| **2. Harden the build** | Per-workspace `profiler-build-<ws>` and prefix-scoped source (§4.4); AR PyPI remote repo plus a private pool without internet (§4.3); label budget → `profiler_enabled` (§7.2) | infra/security + load-estimator | Live test in the `build-isolation.sh` style: workspace A's build can't read B's source |
| **3. Separate quota** | Move the module to a dedicated `shipcube-profiler` project, add it to the kill switch's `PROJECTS` | infra/security; needs Kunal's OK (new project) | Integration test against the new project |

## 11. Open questions for Kunal

1. Are the daily caps right: $5/day global, $1/day and 20 runs per workspace?
2. Strike policy: is 3 in 24 h, then static-only for 24 h, right? Should a mining match always need a human?
3. Dedicated `shipcube-profiler` project in phase 3: yes or no? It's free when idle, but it's one more project on the billing account.
4. Private build pool in phase 2: it costs an always-on worker. Is wheels-only plus the PyPI remote enough on its own?

## Unrelated follow-up noticed while reading

`worker.py` passes only `expected_users` and `schedule` to the estimator. The `traffic` and `region` hints are dropped, so API-only and region-priced estimates use defaults.
