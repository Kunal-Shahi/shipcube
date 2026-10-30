# ShipCube red-team review

**Date:** 2026-10-28, re-reviewed 2026-10-30 (twice; the newest section is first). **Scope:** `Kunal-Shahi/shipcube` at `main` (`dd678f5`), the open hosting PR (`platform/hosted-deploy`), `Kunal-Shahi/shipcube-data` at `main` (`5cb920b`), and the live GCP configuration of `shipcube-control-ks` and `shipcube-apps-ks`.
**Method:** code and Terraform review, read-only `gcloud`, and light unauthenticated or invalid-token requests against ShipCube's own services. Nothing was changed, no load was generated, and no third party was touched. Two findings (S-3, S-4) were reproduced with scratch tests against a local Postgres and the mock deployer.

Severity is intrinsic: what happens if someone uses the issue. The **Live?** column says whether it can be reached in today's deployment:
- **live:** reachable now.
- **token:** reachable by anyone holding a ShipCube PAT. Today PATs are only minted by the operator CLI, so this means judges, testers and anyone they share a token with.
- **latent:** the code is merged but not deployed or not wired up yet. It becomes live when it ships.

Owners: **security** (`infra/**`, IAM, DB roles), **networking** (`services/api`, `services/mcp`), **platform** (`services/gateway`, `deploy/`, CI, monitoring), **frontend** (`apps/web`), **load-estimator** (`packages/profiler`, `packages/placement`, `ml/`), **data-layer** (`shipcube-data`).

---

## Re-review 2, 2026-10-30 afternoon (`main` at `3320b7c`)

Since the morning re-review, three PRs have merged:
- **#44** sends build logs to per-workspace GCS and removes the project-wide `logWriter` (#28).
- **#45** contains go-live fixes: `SHIPCUBE_AI_GATEWAY_URL` on the MCP service, the catalog startup check on the API's IAM-auth pool, and a registry-401 case in the isolation test.
- **#46** grants `storage.buckets.get` on the uploads bucket so Cloud Build accepts the logs folder.

All four services were redeployed. The method was as before: code review, read-only `gcloud` and Monitoring API calls, and unauthenticated or invalid-token requests.

### Production now runs `main`

| Check | Result |
|---|---|
| Images | `shipcube-mcp`, `-worker`, `-console` and `-gateway` all run `3fff7a6`, which is `main` minus #46. #46 is Terraform only. |
| C-2 (data-layer version pin) | **Fixed in production.** The deployed MCP pins shipcube-data `ab16f4c`, which contains the fix (`62f1011`). shipcube-data #2 is closed. |
| H-3 (MCP URL) | **Fixed in production.** The console has `SHIPCUBE_MCP_URL` set to ShipCube's own run.app URL. #17 can close. |
| Gateway URL returned by `ai_config` | **Fixed:** `SHIPCUBE_AI_GATEWAY_URL` is set on `shipcube-mcp`. |
| Dev mode in production | No `*DEV_MODE`, `SHIPCUBE_BACKEND`, local `SHIPCUBE_PROFILER` or local secret store env on any service. The code-level guard (#29) is still missing, but nothing sets the flags. |
| Auth negative tests | `/v1/me`, `/v1/apps`, `/v1/data/cubes` and `/mcp` return 401 unauthenticated and with a bogus PAT. `/docs` and `/openapi.json` are still public (low, known). |
| #28 | The `principalSet` `logWriter` binding is not in live IAM. Closed. |

### C-1: half applied, not closed

**Applied:**
- `sc-provisioner` exists.
- The first workspace has its own builder SA, its own registry repo `ws-<ws_short>`, and its managed folders under `ws/<uuid>/`.
- The hosted build path in the code no longer runs as `sc-builder`.

**Still open:**
- **#46 is not applied.** The uploads bucket has no `storage.buckets.get` binding yet, and Cloud Build rejects a `logsBucket` the build SA can't read. Expect **every hosted build to fail** until it is applied. No hosted build has run in the apps project since the deploy (the only builds there are profiler builds from Oct 27). This is now the biggest risk for the demo. *(platform)*
- **`sc-builder` keeps its legacy grants:**
  - `storage.objectViewer` on the whole uploads bucket;
  - `artifactregistry.writer` at project level and on the shared `shipcube` repo;
  - the hand-made `logging.logWriter`.

  Nothing builds as `sc-builder` now, so these grants are dormant. They are still the C-1 blast radius if anything can get a token for it. Finish infra/README step 4: set `legacy_shared_builder = false`, apply, then remove the `logWriter` binding. *(security)*
- `hello-tracker` (the one tenant app) still runs a **tag** from the shared repo, not a digest in its own repo. Redeploy it through the new path once builds work, so `update_caps` doesn't refuse it.

**#46 review: acceptable.** It binds a custom role holding only `storage.buckets.get` to every SA in the apps project, and only on the uploads bucket. That role allows no object reads, no listing and no IAM reads, so tenant runtime SAs learn the bucket's metadata and nothing else. A per-SA binding would be tighter, but builders are created at runtime, so this is a reasonable trade.

**#45 review: no issues.** The catalog probe now runs on the API's pool under the nil workspace and only reads the migration ledger.

### Unchanged since the morning

- **R-1 (#42)** is still latent: the hosted gateway reads `GATEWAY_REGISTRY_FILE`, not `provider_configs`. `set_ai` is unchanged (`store.py:605-614`).
- **S-3 and S-4 (#18, #19):** the code is unchanged (`store.py:576-580`), and they are now **live** because production runs `main`.
- **H-1 (#15):** `sc-api` still holds `iam.serviceAccountAdmin` and `secretmanager.admin` on the control project.
- **H-2 (#16):** `roles/editor` is still on both default compute SAs. Also note the App Engine default SA in the control project, which holds Editor too.
- **O-1:** there are still zero alert policies and zero uptime checks in both projects.
- **#29:** no `K_SERVICE` refusal outside the console. The localhost defaults remain (`settings.py:56`).
- **A-1, G-1, P-1, P-3 (#20–#23):** unchanged.

### Top 5 now (Nov 4 is in 5 days)

1. **Make hosted deploys work, then close C-1.**
   - Apply #46.
   - Run one hosted build end to end and check that it runs as `sc-build-<ws>` and logs to `ws/<uuid>/build-logs/`.
   - Redeploy `hello-tracker`.
   - Set `legacy_shared_builder = false` and remove `sc-builder`.

   *(platform, security)*
2. **Revocation correctness (S-3, S-4):** now live in production. *(networking)*
3. **Remove `roles/editor` from the default compute SAs** (and the App Engine SA). It's one command each and defuses H-2 and half of H-1. *(security)*
4. **Alerts (O-1):** run `deploy/monitoring.py`. Nothing pages anyone today. *(platform)*
5. **R-1 and #29 before anything else ships:** restrict `key_secret` by kind and provider before the gateway reads `provider_configs`, and add the shared `K_SERVICE` refusal. *(networking, platform)*

---

## Re-review, 2026-10-30 (`main` at `490642d`)

Since the first review, 12 PRs have merged: #24–#27 and #30–#40, covering the local stack, the C-1 fixes, the configurable MCP URL, the console endpoints (approvals inbox, BYOK, billing, GCP targets), the data layer in the API, hosted deploy v2 and the link-your-own-project flow. shipcube-data issues #2–#6 were closed, and its `main` (`8ded06b`) was re-checked. Method as before: code review, read-only `gcloud`, and offline tests. shipcube-data ran 161 passed and 274 skipped (the Postgres variants need a DB).

### The headline: `main` is not what's running

Most fixes landed on `main`, but **production still runs code from before them**:

| Service | Deployed image | Contains the fixes from #25/#26/#33/#35? |
|---|---|---|
| `shipcube-mcp` (API + MCP) | `api:7344f4768c5e` (Oct 28 02:22) | no |
| `shipcube-worker`, `shipcube-console` | `api:caf0a2c688cd`, `console:caf0a2c688cd` | no |
| `shipcube-gateway` | `gateway:f6630a2241de` | no |

The C-1 Terraform (#27) **isn't applied** either. `sc-provisioner` doesn't exist, there are no `ws-*` repos, and `sc-builder` still has project-wide `artifactregistry.writer` plus bucket-wide `objectViewer`. As a result:
- **C-1 (shared builder) is still live** for any token holder.
- **C-2 (version pin bypasses access rules) is now live.** `shipcube-mcp` has `SHIPCUBE_DATA_WORKSPACE` set, so the data tools are mounted. Its lockfile pins shipcube-data `5cdc6f8`, which predates the fix and ships the demo's `0005_access` rules. The data is the public Olist demo, so the impact is a broken access-control demo, not a customer-data leak. Every workspace's PAT queries that one demo catalog.
- The fixed code on `main` (`ab16f4c`) isn't deployed.

**Required order for the deploy:**
1. Fix #28.
2. Apply Terraform (`build_isolation`).
3. Deploy `main` (API, worker, console, gateway).
4. Check that a hosted build works as `sc-build-<ws>`.
5. Set `legacy_shared_builder=false`, apply, and delete `sc-builder`.

Deploying #26's code without step 2 breaks hosted builds, because the provisioner is missing.

### Status of earlier findings

| ID | On `main` | Live | Notes |
|---|---|---|---|
| C-1 shared builder | **fixed** (#26, #27) | **open** | Not applied or deployed. #27 adds R-9 and #28. |
| C-2 data version pin | **fixed** (shipcube-data `62f1011`, pinned `ab16f4c`) | **open, now live** | Gap: a pin restores old member SQL, so it can undo a masking `change_sql` (D-R3). |
| H-1 `sc-api` admin roles | open | open | Unchanged in Terraform and live. |
| H-2 hosted deployer project-wide actAs; Editor on default compute SAs | open | open | Unchanged live. R-9 adds a similar path. |
| H-3 unowned MCP host | **fixed** (#25) | fixed | Console requires `SHIPCUBE_MCP_URL`; a CI guard bans the old domain. |
| S-3 domain grant drops revocations | open | open | `store.py:576-580` unchanged. |
| S-4 push-op idempotency | partial | open | `set_caps` now hashes caps (`store.py:542`). `set_access` push is still constant (`:580`), and MCP keys are still time-bucketed. |
| G-1 Muse key uncapped | partial | open | Registry now sets 200k tokens/day and 60 rpm per app (`deploy/gateway_registry.py:21-22`), so `max_tokens` is clamped. Still no prices, and counters are per instance. Gateway image predates this. |
| A-1 any image | partial | open | Cross-tenant pulls closed (`isolation.py:71-91`); arbitrary public images still deploy on hosted. |
| P-1, P-3 profiler build and git | open | latent | |
| D-2 BigQuery ADC | fixed | — | Verified projects only; no ADC fallback. |
| D-3 secret_ref and hosts | partial | — | Workspace-prefixed secrets, public hosts and `verify-full` enforced. A URL-form secret can carry `hostaddr=` to an internal IP (D-R4). |
| D-4 DuckDB file reads | fixed | — | Refused outside dev mode; sealed with `enable_external_access=false`. |
| D-5 widening migrations / self-approval | partial | — | Approvals compare people, and widening ops need an admin. `add_segment` is still developer-approvable (D-R1). |
| O-1 no alerts | open | open | Still zero alert policies and uptime checks in both projects. |
| S-5, S-7, S-8, A-2, A-3, A-4, S-11, S-12, W-1, W-2, L-1 | open | open | Unchanged. W-1: `google-sign-in.tsx:115` still lets `/\evil.com` through. |
| S-10 console token verification | partial | — | The API now accepts session cookies with their own issuer and certs (`auth.py:140-150`). There's no revocation check, so a cookie stays valid at the API after sign-out; no `email_verified` check; certs are fetched on every request. |
| CI-1 CI deployer | **wider** | — | `deploy/wif.sh:28` lets CI act as `sc-worker` (BYPASSRLS, can impersonate the provisioner). |

### New findings

| ID | Finding | Sev | Live? | Owner |
|---|---|---|---|---|
| R-1 | BYOK: a PAT can point any stored secret at an attacker host | high | latent (MCP tool is live) | networking, platform |
| R-2 | #28: project-wide `logWriter` for every apps-project SA (merged, not yet applied) | high | latent | security |
| R-3 | Dev-mode gates incomplete (#29): `SHIPCUBE_PROFILER=local` and `SHIPCUBE_MONITOR=local` ungated; localhost fallbacks in production paths | medium | latent | platform, load-estimator, networking |
| D-R1 | Data: `add_segment` can embed arbitrary SQL (`query_to_xml`) and hidden members, approved by any developer | medium-high | latent (data routes) | data-layer |
| D-R2 | Data: model SQL can call `query_to_xml`, `set_config` and `dblink*` | medium | latent | data-layer |
| R-4 | Link verification checks project-level permissions that the link script grants per resource, which pushes users into over-granting | medium | live once #40 deploys | networking, security |
| R-9 | Build provisioner's custom role has `setIamPolicy` on every SA and repo in the apps project; the worker can impersonate it | medium | latent | security |
| R-5 | `GET /v1/gcp-targets` creates a control-project SA per workspace, for any `apps:read` token | low-medium | latent | networking |
| R-6 | PAT scopes aren't capped by the caller's role | low-medium | latent | networking |
| D-R3 | Data: a version pin restores old member SQL (can undo masking) | low-medium | latent | data-layer |
| D-R4 | Data: `hostaddr=` in a URL-form warehouse secret bypasses the host check | low-medium | latent | data-layer |
| R-7 | Catalog RLS is workspace-only (no role predicate); `catalog.audit_log` is mutable by `shipcube_app` | low | latent | data-layer, security |
| R-8 | Local deployer: `git checkout <ref>` with no `--` (option injection; local only) | low | dev only | platform |
| R-10 | `sc-web` has project-wide `roles/firebaseauth.admin` (`infra/terraform/platform.tf:14-18`) | low | latent | security |
| R-11 | Billing page shows ShipCube's platform-wide credit grant to every workspace (`workspace.py:255`) | info | latent | frontend |

**R-1. BYOK: a PAT can point any stored secret at an attacker host — high — networking, platform**
- **Evidence:** `services/api/shipcube_api/store.py:605-614` (`set_ai`) and `app.py:341-342`. `PUT /v1/apps/{app}/ai` needs only the `secrets:write` scope and is also the MCP tool `ai_config` (`services/mcp/shipcube_mcp/tools.py:241-251`).
  - `key_secret` is matched against **any** secret label in the workspace, of any kind. That includes console-saved provider keys and `warehouse-primary`.
  - `base_url` is stored unvalidated on this path.
- **Scenario:** a prompt-injected agent sets `provider=openai_compatible`, `base_url=https://<attacker>` and `key_secret=<the workspace's OpenAI key or warehouse credential>`. The gateway then sends that secret as a bearer token to the attacker. This contradicts the design rule that keys never pass through MCP. It's latent only because the hosted gateway still uses a static registry (`deploy/gateway_registry.py`). It goes live when the gateway resolves `provider_configs`.
- **Fix:**
  - Accept only `kind='ai_key'` secrets whose provider matches.
  - Don't take `base_url` from PAT callers: require a console session or an approval for `openai_compatible`.
  - In the gateway, bind each key to the base URL it was saved with.

**R-2. Project-wide `logWriter` for every apps-project SA (#28) — high — security**
- **Evidence:** `infra/terraform/modules/build_isolation/main.tf:81-85`. It was merged in #27 with the blocker unresolved. It isn't applied yet (live IAM has no `principalSet` binding), so it can still be fixed before the C-1 apply.
- **Scenario:** any tenant app's runtime SA can write log entries labelled as another tenant's service (poisoning that tenant's `get_logs`) or as a `prof-*` job (forging profiler results). It can also ingest logs without limit.
- **Fix:** Cloud Build `options.logging: GCS_ONLY` with a per-workspace logs location, then delete the binding.

**R-3. Dev-mode gates incomplete (#29) — medium — platform, load-estimator, networking**
- Gated correctly now: `SHIPCUBE_BACKEND=local` (`store_runtime.py:21-23`), the local secret store (`secret_store.py:91`), the estimator, dev console sessions, the gateway, and the console (which also refuses on `K_SERVICE` and refuses `FIREBASE_AUTH_EMULATOR_HOST` outside dev mode).
- **Not gated:**
  - `SHIPCUBE_PROFILER=local` (`packages/profiler/.../runners.py:429-431`): the Docker runner;
  - `SHIPCUBE_MONITOR=local` (`packages/monitor/shipcube_monitor/__init__.py:40-43`).
  
  Today, only the absence of a Docker socket in the production image stops the profiler escalation.
- The API, profiler and monitor still don't refuse dev mode under `K_SERVICE`, and two names remain (`SHIPCUBE_DEV_MODE`, `DEV_MODE`).
- `infra/local/localsecrets.py` says production refuses `sclocal_`-prefixed secrets, but no production code checks the prefix.
- **Localhost fallbacks in production paths:**
  - `app.py:344` returns `http://localhost:8090` as the app's `gateway_url` when `SHIPCUBE_AI_GATEWAY_URL` is unset, and `shipcube-mcp` doesn't set it live.
  - `settings.py:56` (`console_url`) and `services/mcp/shipcube_mcp/server.py:62` (OAuth `issuer_url`) also default to `http://localhost:3000`. The live services do set `SHIPCUBE_CONSOLE_URL`.
- **Fix:** as in #29. One switch name; refuse under `K_SERVICE`/`CLOUD_RUN_JOB`; gate every `local` choice; make an unset or localhost URL a startup error outside dev mode; implement the `sclocal_` refusal or drop the claim.

**D-R1. `add_segment` embeds arbitrary SQL — medium-high — data-layer**
- **Evidence:** shipcube-data `ops.py:500-516`. `add_segment` isn't in `WIDENS_EXPOSURE` (`ops.py:703`). Segments skip `visible()` (`compiler.py:442-448`), and `sqlutil.py:37-68` lets `query_to_xml`, `table_to_xml`, `dblink_exec` and `set_config` through.
- **Scenario:** an agent proposes a segment whose SQL runs `query_to_xml('select … from <another tenant schema>.…')` or references a hidden member, and any other developer approves it. Verified offline: an analyst got 22 vs 400 rows by filtering on an `hr`-only column.
- **Fix:** treat `add_segment` as widening, check visibility on segment references, and use a function allowlist for all model SQL (D-R2).

**R-4. Link verification pushes users into over-granting — medium — networking, security**
- **Evidence:** `services/api/shipcube_api/deploy/gcp.py:49-52,537-551` run project-level `testIamPermissions` for `iam.serviceAccounts.actAs` and `artifactregistry.repositories.uploadArtifacts`. `infra/scripts/link-project.sh` grants those per SA and per repo, so a correctly linked project reports "missing permissions".
- **Scenario:** to make the console turn green, the user grants project-wide `serviceAccountUser`. ShipCube can then act as the customer's default compute SA (Editor), which makes L-1 worse.
- **Fix:** test `actAs` on the two SAs and `uploadArtifacts` on the repo, at resource level.

**R-9. Provisioner can undo the isolation it creates — medium — security**
- **Evidence:** `infra/terraform/modules/build_isolation/main.tf:31-46` grants `iam.serviceAccounts.setIamPolicy` and `artifactregistry.repositories.setIamPolicy` project-wide. `sc-worker` can impersonate `sc-provisioner`.
- **Scenario:** a compromised worker grants itself `actAs` on any app's runtime SA, or write on any workspace's repo. With Editor still on the default compute SA, it can also reach Editor.
- **Fix:** IAM conditions on `resource.name` prefixes (`sc-build-*`, `ws-*`) where supported. Remove Editor from the default compute SA (H-2).

**R-5, R-6, D-R2, D-R3, D-R4, R-7, R-8, R-10, R-11** are summarised in the table above:
- R-5: the SA is created by a `GET` (`workspace.py:185-186`). There's a quota of about 100 SAs per project.
- R-6: `create_token` (`store.py:656`) only checks `data:raw`.
- D-R4: `datasources.py:201-209` checks `host` but not `hostaddr`.
- R-8: `deploy/local.py:212`.

### Local stack against the four rules (#30–#34)

| Rule | Verdict | Evidence |
|---|---|---|
| 1. No local adapter in production | **partial** | R-3. Production images still ship the local code (`deploy/local.py`, `local_iap.py`, `docker_runner.py`, `monitor/local.py`); only the gates keep it off. The API image has no docker CLI and no `pack`, which limits the damage. |
| 2. No default credentials or committed secrets | **pass** | Secrets are generated with mode 0600 and gitignored twice (`/.local/` and `.local/.gitignore`). `refuse_production()` stops the generator on Cloud Run. The `sclocal_` refusal claim is unimplemented (R-3). |
| 3. Low dev/prod drift | **pass, with caveats** | Same IAM login names, `bootstrap.sql`, `owner_defaults.sql`, `alter role … set role` defaults, migrator, migrations and catalog. The local deployer reuses `isolation.py`. **Local can't test:** tenant network isolation (all app containers share one Docker network and can call each other directly, bypassing local IAP; `deploy/local.py:253-259`); IAM; egress; the Cloud Build path; real IAP. The local warehouse uses the Postgres superuser. |
| 4. Sandbox and deployer don't expose the host | **pass (profiler) / partial (API)** | Profiler container: `--network none`, read-only root, uid 10001, `--cap-drop ALL`, `no-new-privileges`, memory, CPU and pids limits, no host env (`docker_runner.py:90-103`). Local IAP strips client identity headers (`local_iap.py:148-156`). All published ports bind `127.0.0.1` (`deploy/local/compose.yaml`). **But** the API and worker containers mount `/var/run/docker.sock` (`compose.yaml:17,90`), so locally built user code that escapes its container reaches the host. That's acceptable only because it's dev-only and gated. Don't run untrusted repos on the local stack. |

### Top 5 now (Nov 4 is in 5 days)

1. **Ship what's on `main`, in the right order (C-1, C-2 live).** Fix #28, apply `build_isolation`, deploy all four services from `main`, test a hosted build, then retire `sc-builder`. Until then, fixing things on `main` changes nothing for users. *(security, platform)*
2. **Close R-1 before the gateway reads `provider_configs`.** Restrict `key_secret` by kind and provider; `base_url` only from console sessions. *(networking)*
3. **Revocation correctness (S-3, S-4).** Unchanged since the first review, and still the most user-visible security bug. *(networking)*
4. **IAM narrowing (H-1, H-2, R-9, CI-1).** At minimum, remove `roles/editor` from both default compute SAs today. That one change defuses H-2, R-9 and the compute-SA half of H-1. *(security)*
5. **Alerts and dev-mode guards (O-1, R-3).** Run `deploy/monitoring.py` (still zero alert policies), set `SHIPCUBE_AI_GATEWAY_URL` on `shipcube-mcp`, and add the shared refusal under `K_SERVICE`. *(platform)*

New issues for this pass: see the PR description. The original findings follow, unchanged except where the table above says otherwise.

---

## Original review (2026-10-28)

### Top 5 to fix before Nov 4 (original)

1. **Isolate builds per tenant (C-1).** User build code runs as one shared builder identity that can read every tenant's source and write every tenant's images. Use a per-workspace builder SA, or at minimum IAM conditions on bucket prefix and AR package. Deploy by digest only. *(security + networking)*
2. **Remove the admin roles from internet-facing identities (H-1, H-2).** `sc-api` holds `iam.serviceAccountAdmin` and `secretmanager.admin` on the control project. The hosted deployer holds project-wide `serviceAccountUser` and `serviceAccountAdmin` on the apps project, where the default compute SA is Editor. Replace these with narrow custom roles and per-SA bindings, and drop Editor from both default compute SAs. *(security)*
3. **Make access revocation actually reach IAP (S-3, S-4).** Revoking a user can leave them in the IAP policy indefinitely while the DB and console say they're gone. *(networking)*
4. **Cap the shared Muse key and close the "any image" path (G-1, A-1).** Set a default `ai_tokens_per_day`, refuse unpriced models on the platform key, always clamp `max_tokens`, and allowlist models. Reject `image` sources outside the caller's own registry path. *(platform + networking)*
5. **Fix the cost and ops blind spots (H-3, O-1, O-2).** Stop shipping an MCP URL ShipCube doesn't control (H-3). Deploy the alert policies that `deploy/monitoring.py` already defines; none exist in either project today. Lower the kill-switch trip point and add project quota overrides. *(frontend + platform + security)*

Also fix **C-2** before the data tools are mounted in production.

---

## Summary table

| ID | Finding | Sev | Live? | Owner |
|---|---|---|---|---|
| C-1 | Shared builder identity: cross-tenant source read and image overwrite | critical | token | security, networking |
| C-2 | Data layer: pinning `model_version` drops row policies and member grants | critical | latent | data-layer |
| H-1 | `sc-api` (public) holds `serviceAccountAdmin` and `secretmanager.admin` on the control project | high | live (needs an RCE/SSRF first) | security |
| H-2 | Hosted deployer can act as any SA in the apps project, including default compute (Editor) | high | live (needs sc-api/worker compromise) | security |
| H-3 | MCP URL shown to users points at a host ShipCube doesn't control | high | live | frontend |
| S-3 | A `domain:` grant in the same request drops revocations | high | token | networking |
| S-4 | Push ops reuse idempotency keys, so revocations and cap reductions are silently skipped | high | token | networking |
| G-1 | Shared Muse key effectively uncapped per app | high | live for deployed apps | platform |
| A-1 | `image` source accepts any URI: arbitrary workloads, cross-tenant image pulls | high | token | networking |
| P-1 | Profiler image build runs untrusted `pip install` with internet; build SA optional | high | latent | load-estimator, security |
| P-3 | Git sources: `file://`, internal hosts, symlinks to `/proc/self/environ` sent to Muse | high | latent | load-estimator, networking |
| D-2 | Data layer: BigQuery via ShipCube's ADC on any project the admin names | high | latent | data-layer |
| D-3 | Data layer: `secret_ref` accepts any project; credentials can be sent to an attacker host | high | latent | data-layer |
| D-4 | Data layer: DuckDB connector reads API host files (e.g. `/proc/self/environ`) | high | latent | data-layer |
| D-5 | Data layer: one developer can propose and approve exposure-widening "additive" migrations | high | latent | data-layer |
| O-1 | No alert policies or uptime checks deployed anywhere | medium | live | platform |
| O-2 | Kill switch trips late (100% of an INR 24,000 gross cap, with billing lag); no quota backstop | medium | live | security |
| S-5 | Suspended workspaces are never enforced | medium | live | networking |
| S-6 | Privilege tables (`memberships`, `users`, `workspaces`) have no RLS but are writable by `shipcube_app` | medium | live | security |
| S-7 | Approval `request_hash` is never re-checked; git refs and image tags are mutable after approval | medium | token | networking |
| S-8 | Purge of a soft-deleted app destroys a newer app with the same name | medium | token | networking |
| A-2 | Upload signed URLs have no size limit; uploads and estimates have no rate limit | medium | token | networking |
| A-3 | `max_apps` / `deploys_per_day` are count-then-insert races | medium | token | networking |
| A-4 | Worker re-claims poison ops forever (no `max_attempts` on claim) | medium | token | networking |
| G-2..G-6 | Gateway metering gaps: retries unmetered, non-atomic caps, `max_tokens` bypass, stream under-count, client-chosen usage id | medium | live | platform |
| P-2 | User code can forge the profiler result line | medium | latent | load-estimator |
| P-4 | Profiler "no egress" still allows DNS and the metadata server | medium | latent | security |
| P-5 | No per-workspace limit on concurrent estimates | medium | latent | networking |
| I-1 | Tool-output poisoning: user-controlled text reaches the agent and the approver unlabelled | medium | token | networking, load-estimator |
| W-1 | Open redirect after sign-in (`returnTo=/\evil.com`) | medium | live once console is up | frontend |
| W-2 | Invite-only sign-up not enforced (no `beforeCreate`) | medium | live once console is up | frontend, security |
| L-1 | Linked-project deployer has project-wide Run IAM, IAP admin and scheduler admin | medium | latent | security |
| CI-1 | CI deployer has `storage.admin` and `run.admin` project-wide; Terraform state is in the same project | medium | live | platform, security |
| M-1 | Classifier accuracy is presented more strongly than the evidence supports | medium | live (docs) | load-estimator |
| R-1 | Single points of failure: zonal `db-f1-micro`, one worker, console currently failing | medium | live | platform |
| S-9..S-12, D-6..D-11, G-7..G-12, misc | Lower-severity items | low | — | see below |

**Issues filed** (labels `security` / `abuse`):
- shipcube: C-1 #14, H-1 #15, H-2 #16, H-3 #17, S-3 #18, S-4 #19, G-1 #20, A-1 #21, P-1 #22, P-3 #23.
- shipcube-data: C-2 Kunal-Shahi/shipcube-data#2, D-2 #3, D-3 #4, D-4 #5, D-5 #6.

---

## Critical

Exploit detail for the two critical findings is deliberately high-level here, because this repository is public. Kunal has the step-by-step write-ups privately.

### C-1. Shared builder identity: cross-tenant source read and image overwrite — critical — token — security, networking

**Evidence**
- `services/api/shipcube_api/deploy/gcp.py:176-183`: every hosted build runs buildpacks with `--network cloudbuild` as `builder_sa = app.target.builder_sa or self.s.builder_sa`. That is one SA (`sc-builder`) for all hosted workspaces.
- `infra/terraform/uploads.tf:22-26` grants `sc-builder` `roles/storage.objectViewer` on the whole uploads bucket (all workspaces).
- `infra/terraform/iam.tf:106-110` grants `sc-builder` `roles/artifactregistry.writer` on the whole apps project (the shared `shipcube` repo and `profiler`).
- `gcp.py:306-316`: `update_caps` re-deploys the stored image *tag*, not a digest.

**Scenario (high-level):** buildpacks execute tenant-controlled code during the build. That code runs with the builder's credentials, which reach every tenant's uploaded source and every tenant's image packages. One tenant can therefore read other tenants' code and replace the image that another tenant's app next redeploys.

**Fix**
- One builder SA per workspace, created alongside the per-app runtime SA. Bind bucket read with an IAM condition on `uploads/<ws>/` and `inline/<ws>/`, and AR write on that workspace's packages only.
- Record `image@sha256` from the build result and only ever deploy digests, including in `update_caps`, rollback and restore.
- Interim, if per-workspace SAs don't fit before Nov 4: IAM conditions on the shared SA can't separate tenants, so instead pass source to the build through a short-lived signed URL, drop the bucket-wide grant, and deploy by digest. That removes the read path and the tag-swap path.

### C-2. Data layer: pinning `model_version` drops row policies and member grants — critical — latent — data-layer

**Evidence:** `shipcube-data` `query.py:118` (`model_version: Optional[int]`), `engine.py:150` (`self._model(q.model_version)`), `catalog.py:150-190`. `load(v)` rebuilds the *whole* model as of version `v`, including `row_policies`, grants and bindings. Verified by reading the code.

**Scenario (high-level):** any `data:read` caller can ask for a model version from before access rules were added, and reads rows and columns those rules hide. `search_values` accepts the same field.

**Live?** Not today: `shipcube-mcp` has no `SHIPCUBE_DATA_WORKSPACE`, so `services/mcp/shipcube_mcp/data.py:default_backend()` doesn't mount the data tools. It becomes live the moment they are mounted.

**Fix:** always take policies, grants and bindings from head, and let a pin change member definitions only. Add a regression test that pins a pre-policy version.

---

## High

### H-1. `sc-api` holds `serviceAccountAdmin` and `secretmanager.admin` on the control project — security

**Evidence**
- `infra/terraform/iam.tf:57-67`. Live: `gcloud projects get-iam-policy shipcube-control-ks` shows `roles/iam.serviceAccountAdmin` and `roles/secretmanager.admin` for `sc-api`.
- `sc-api` runs `shipcube-mcp`, which has `allUsers` invoker by design.
- The control project's default compute SA has `roles/editor`.
- Secrets include `sql-postgres-password`.

**Scenario:** any RCE or SSRF-to-metadata in the public MCP/API process becomes control-project takeover:
- `setIamPolicy` on any SA (default compute = Editor, `budget-killswitch` = billing unlink, `sc-migrator` = DB owner) to grant itself `tokenCreator`;
- or read `sql-postgres-password` and connect as a role that bypasses RLS.

**Fix**
- Replace with a custom role holding `iam.serviceAccounts.create`/`get` only, bound with a condition on `ws-*` names.
- Grant `secretAccessor` per secret. Allow `secrets.create` under a name-prefix condition (`ws-<id>-*`).
- Make `sql-postgres-password` readable by humans only.
- Remove `roles/editor` from `340481939948-compute@` (nothing in ShipCube runs as it).

### H-2. Hosted deployer can act as any SA in the apps project — security

**Evidence**
- `infra/terraform/iam.tf:86-104`: project-level `roles/iam.serviceAccountUser`, `roles/iam.serviceAccountAdmin`, `roles/iap.admin` and the custom role with `run.services.setIamPolicy`.
- `sc-api` and `sc-worker` can impersonate it (`iam.tf:70-74`). Live IAM confirms this.
- `330999971585-compute@` has `roles/editor` on `shipcube-apps-ks`.
- The comment says these are "the same grants users give it on linked projects", but `infra/scripts/link-project.sh:63-66` scopes `serviceAccountUser` to two SAs. The hosted project is broader than linked ones.

**Scenario:** from `sc-api` or `sc-worker`, deploy a Cloud Run service or Cloud Build as the default compute SA and get Editor on the apps project, i.e. every tenant app. Or set `allUsers` invoker on any tenant app; with no organisation there is no `domain restricted sharing` policy to stop it.

**Fix**
- Bind `serviceAccountUser` per SA, using a condition on `app-*` runtime SAs plus the builders.
- Replace `serviceAccountAdmin` with a create/delete-only custom role.
- Remove Editor from the apps project's default compute SA.

### H-3. MCP URL shown to users points at a host ShipCube doesn't control — frontend

**Evidence:** `apps/web/src/lib/mcp.ts:5` hard-codes the MCP URL that the setup snippets tell users to paste, together with `Authorization: Bearer <token>`. The hosting PR keeps it as the fallback when `SHIPCUBE_MCP_URL` is unset. Checked 2026-10-28: ShipCube does not own that host. Details are with Kunal.

**Scenario (high-level):** whoever controls that host receives the PATs that users configure, and a PAT gives full workspace access within its scopes.

**Fix:** secure the host, or remove the fallback and fail the build when `SHIPCUBE_MCP_URL` is unset. Replace the example hostnames in mock data and the setup page with `example.com`.

### S-3. A `domain:` grant in the same request drops revocations — networking

**Evidence:** `services/api/shipcube_api/store.py:504-511`. `write_grants` revokes in the DB, then `if domain:` enqueues an approval-gated op containing only `{"add": domain}`. The `elif (direct or remove)` push is skipped. Reproduced with the mock deployer: after the approval was rejected, the DB listed `[owner]` and IAP listed `[owner, user:bob@…]`. `get_app` hides revoked rows, so nobody sees the leftover access.

**Fix:** always enqueue an immediate push for `direct`/`remove`, separate from the domain approval, or make the whole change wait for approval and write nothing first.

### S-4. Push ops reuse idempotency keys, so revocations and cap reductions are skipped — networking

**Evidence**
- `store.py:510`: the push op payload is the constant `{"push_only": True}`, so the request hash is identical for every `set_access` on an app.
- Caps are written before `enqueue` (`store.py:471-472`).
- The MCP derives keys from the args within a 10-minute bucket (`services/mcp/shipcube_mcp/tools.py:58-66`).

Reproduced: add bob (key k1), then remove bob with k1. This returns the old succeeded op and nothing is pushed, so IAP keeps bob. Over MCP, remove → add → remove within 10 minutes produces the same result. Caps 2→1→2→1 leave Cloud Run at 2. Delete → restore → delete returns a stale "succeeded".

**Fix:** hash the actual change. Write to the DB only when `enqueue` created a new op. Don't use time-bucketed derived keys for mutating tools.

### G-1. Shared Muse key effectively uncapped per app — platform

**Evidence**
- `deploy/deploy.sh:41` sets no `GATEWAY_PRICES`. `services/gateway/usage.go:149-160`: an unpriced model makes `cost()` return `(0,false)`, so `spend_usd_per_month` never binds.
- `store.py:58-60` workspace defaults have no `ai_tokens_per_day`.
- `server.go:224-243`: with no token cap, `max_tokens` is never clamped.
- `server.go:123`: the app chooses `model`.
- `caps.go:52-53`: counters are per-instance memory, reset on scale-to-zero. `DESIGN.md:17` says they're shared in Postgres.

**Scenario:** any deployed app (including a judge's test app) loops large-`max_tokens` requests at 120 rpm per instance and spends the Meta key's quota or balance. Counters reset whenever the gateway scales to zero.

**Fix**
- Default `ai_tokens_per_day` for every app.
- Refuse unpriced models on the platform key, and allowlist models per provider.
- Always clamp `max_tokens` (and `max_completion_tokens`, `n`).
- Move counters to the `rate_counters` table (already created in migration 0003), with reserve-then-refund at admission.

### A-1. `image` source accepts any URI — networking

**Evidence:** `services/api/shipcube_api/models.py:44-46` (`uri: str`, no validation). `gcp.py:138-139` and `171-172` skip the build entirely.

**Scenario**
- Deploy a public crypto-miner image directly, bypassing buildpacks and any review. With caps of 10 instances × 4 vCPU per app, that's real money.
- Or deploy `asia-south1-docker.pkg.dev/shipcube-apps-ks/shipcube/<another tenant's service>:<tag>` under your own app. The apps project's Cloud Run agent can pull it, so you get their code and anything baked into it.

**Fix:** for hosted workspaces, disable `image` sources or require a digest under the caller's own linked-project registry. Never accept paths in ShipCube's shared repo that don't belong to the caller's app.

### P-1. Profiler image build runs untrusted `pip install` with internet — load-estimator, security — latent

**Evidence**
- `packages/profiler/src/.../gcp_runner.py:62` runs `pip install -r /app/requirements.txt` in Cloud Build, which has egress, for up to 900 s.
- `gcp_runner.py:161-162`: the build SA is optional. Unset means the default SA, which is Editor on `shipcube-apps-ks`, the project that hosts tenant apps.
- `profiler-build` can read every in-flight `profiler-src/` tarball.

**Live?** Latent: `deploy/mcp.Dockerfile` doesn't install the estimator, so production estimates fail closed with `estimator_unavailable`. Also `services/api/shipcube_api/estimator.py:39-40` constructs `CloudRunJobRunner` with the wrong signature, so wiring it up will raise `TypeError` until fixed.

**Fix**
- Make the build SA mandatory.
- Download dependencies in a separate hash-checked step, then build in a no-egress private pool.
- Move the sandbox to its own project.
- Pass source inline instead of granting bucket read.

### P-3. Git sources reach local files and internal hosts — load-estimator, networking — latent

**Evidence**
- `packages/profiler/.../sources.py:59` allows `file://`. `GitSource.url` is unvalidated (`models.py:37-41`), and cloning runs in the API process.
- `detect.py:116-120` reads entry files with `read_text`, which follows symlinks. The excerpt goes to Muse (`packages/placement/.../explain.py:227`).

**Scenario:** a repo whose `main.py` is a symlink to `/proc/self/environ` sends the API's environment (the pepper and `META_API_KEY` on the worker) to Meta. A prompt-injected string can make Muse echo it into `reasoning`, which is returned to the caller.

**Fix:** allow https only, with a public-IP check at connect time. Reject symlinks, or resolve them and verify they stay under the root. Summarise inside the sandbox, not the API.

### D-2..D-5. Data layer, latent until the data tools are mounted — data-layer

All references are to `shipcube-data` `5cb920b`, `src/shipcube_data/`.

- **D-2. BigQuery with no secret uses ShipCube's own identity on any project the admin names** (`datasources.py:107-109`, `warehouses.py:173`). If customers grant ShipCube's single SA read access, as the design suggests, workspace B can read customer A's datasets and bill ShipCube. *Fix:* impersonate a per-workspace SA, and allow only verified projects per workspace.
- **D-3. `secret_ref.resource_name` accepts any project** (`datasources.py:22`, `tools.py:254`), and the resolved password goes to a caller-chosen `host` with `sslmode=prefer` (`datasources.py:97-103`). That enables Postgres-protocol SSRF and credential capture. *Fix:* look up the workspace's `secret_refs` row by id, block private, link-local and `/cloudsql` hosts, and default to `verify-full`.
- **D-4. DuckDB is a production connector that opens any local path** (`datasources.py:91,112-114`). Member SQL has no function denylist, so `read_text('/proc/self/environ')` works. *Fix:* remove DuckDB from production kinds, or set `enable_external_access=false` and lock the configuration.
- **D-5. Additive migrations can widen exposure, and the proposer can approve their own** (`ops.py:45,163`, `tools.py:269`). `migrate.py:239` compares `token:<id>` with `user:<id>`, so the same human passes. Adding a bare-column dimension over a finance-only column, or a new cube on the same table without policies, defeats grants and row policies. *Fix:* compare the human behind both principals. Classify `create_cube`, `add_dimension`/`add_measure` on governed cubes, and `add_join` as `change`. Reject subqueries and table references in member SQL.

---

## Medium

### O-1. No alert policies or uptime checks deployed — platform
`deploy/monitoring.py` defines uptime checks, a 5xx rate alert, p95 latency and Cloud SQL alerts. Live on 2026-10-28, `alertPolicies` returns `{}` and there are no uptime checks in either project. The console service is currently failing its startup probe (`shipcube-console-00001-rgs`), and nothing would page anyone. *Fix:* run it, or better, move the policies into Terraform (see Best practices).

### O-2. Kill switch trips late; no quota backstop — security
- The budget covers the whole billing account at INR 24,000 gross (`EXCLUDE_ALL_CREDITS`), which is about 95% of the $300 credit.
- `infra/budget-killswitch/main.py:35` acts only when `cost >= CAP`. The function runs every ~30 min but reported `cost=0.0` on Oct 27 while Cloud SQL and Cloud Run were already running, so billing data lags by hours.
- `main.py:47-50`: the update loop has no per-project `try`, so one failure aborts the rest.
- There are no `google_service_usage_consumer_quota_override` resources, so Cloud Run, Cloud Build and Compute quotas are the defaults.
- `CAP` and `PROJECTS` live in the function env, deployed by `deploy.sh` rather than Terraform.

*Fix*
- Trip at about 70% of the credit, and catch per project.
- Add quota overrides in the apps project: Cloud Run CPU per region, concurrent Cloud Builds.
- Alert if the function hasn't logged a budget update for 2 hours.

### S-5. Suspended workspaces never enforced — networking
`0001_security_schema.sql:41` defines `suspended_at`, but nothing in `shipcube_api/` reads it. `auth.py:79-86` checks only `users.disabled_at`. The obvious abuse response, suspending the workspace, does nothing. *Fix:* join `workspaces … suspended_at is null` in `_membership`/`verify_pat`.

### S-6. Privilege tables have no RLS but are writable by `shipcube_app` — security
`0001:397` grants all DML on all tables to `shipcube_app`. RLS covers tenant data tables (`0001:417-457`, `0003`), but not `memberships`, `users`, `workspaces` or `orgs`. `audit_insert … with check (true)` (`0001:458`). Any future SQL bug in the API path can promote a membership across tenants. *Fix:* revoke write on those tables, and expose the lookups through `security definer` functions with a pinned `search_path`.

### S-7. Approval binding not re-checked at execution — networking
The schema comment (`0001:309`) says `request_hash` is checked again before executing, but `worker.py` never compares it. Approved git refs (`HEAD`) and image tags can change between approval and build. *Fix:* recompute and compare in the worker. Resolve refs to a SHA and tags to a digest at request time.

### S-8. Purge destroys a newer app with the same name — networking
`deploy/base.py:38` derives service, SA and package names from `workspace/project/name`, not the app id. `store.py:403` allows reusing a slug after a soft delete, and `worker.py:328-333` purges by ref. If you delete `tracker` and deploy a new `tracker`, seven days later the purge deletes the live one. *Fix:* include `app_id` in derived names, or block slug reuse while a soft-deleted row exists.

### A-2. Uploads have no size limit; uploads and estimates have no rate limit — networking
`gcp.py:128-131` signs the PUT without `x-goog-content-length-range`, so the 100 MB check only happens at stage time. `/v1/uploads` and `/v1/estimates` have no throttle. `estimate_load` is marked `readOnlyHint=True` (`tools.py:114`), so clients may auto-approve it even though it launches a build and a job. `estimator.py:41` leaks a `mkdtemp` (RAM on Cloud Run). *Fix:* sign the length range, add per-workspace rate limits, mark the tool non-read-only, and use `TemporaryDirectory`.

### A-3. App and deploy limits race — networking
`store.py:406-409,421-424` count then insert at READ COMMITTED, so parallel `deploy_app` calls all pass. *Fix:* take `pg_advisory_xact_lock(hashtext(workspace_id))` first.

### A-4. Poison ops re-claimed forever — networking
`worker.py:88-90` re-claims expired leases and increments `attempts` without checking `max_attempts`. An input that OOM-kills the worker loops every 90 s. *Fix:* add `attempts < max_attempts` to the claim, and fail over-limit ops in housekeeping.

### G-2..G-6. Gateway metering gaps — platform
- **G-2:** POSTs are retried after transport timeouts and 5xx (`retry.go:45-57`), and failures record 0 tokens (`server.go:132-139`), so Meta bills up to three times while ShipCube meters nothing.
- **G-3:** `Allow` runs before the call and `Record` after (`server.go:138,143`). With 80 concurrent requests per instance, the cap overshoots by about 160×.
- **G-4:** `clampMaxTokens` rewrites only one of `max_tokens` or `max_completion_tokens`, and ignores `n` (`server.go:225-238`).
- **G-5:** streams count only `delta.content`, so reasoning tokens are missed (`upstream.go:41-45,190-192`), and Anthropic streams record 0 on early disconnect.
- **G-6:** the client's `X-Request-Id` becomes the usage idempotency key (`requestid.go:34-37`, `server.go:130`). Latent, until `/internal/usage` exists.

*Fix:* retry only dial errors, charge `input + max_tokens` when usage is unknown, reserve at admission, rewrite both fields and reject `n>1`, and generate usage ids server-side.

### P-2, P-4, P-5. Profiler — load-estimator, security, networking — latent
- **P-2:** the result is the newest `SHIPCUBE_PROFILE` log line (`gcp_runner.py:271-283`), and user code runs as the same uid. It can write to `/proc/1/fd/1` and forge the profile. *Fix:* use a separate uid and a per-run nonce known only to the agent.
- **P-4:** the deny-all VPC firewall doesn't cover the metadata server or its DNS resolver. DNS tunnelling and minting `profiler-sandbox` tokens remain possible. *Fix:* a Cloud DNS response policy that answers NXDOMAIN, and documenting the residual risk.
- **P-5:** nothing limits concurrent estimates. Each costs a build (900 s) and a job (600 s, 1 vCPU / 2 GiB), and they share Cloud Build concurrency with tenant deploys. *Fix:* one at a time per workspace, plus a global semaphore.

### I-1. Tool-output poisoning and approver spoofing — networking, load-estimator
- `models.py:20` accepts any text after `domain:`. `store.py:507` puts it in the approval reason shown to the human approver, so a principal like `domain:acme.com (pre-approved by security)` can mislead them.
- Build errors, GCP errors and Muse `reasoning` are returned to the agent unlabelled.
- Profiler `routes` and `remote_hosts` (attacker-controlled strings) enter Muse's prompt as trusted FACTS (`packages/placement/.../estimate.py:131-138`, `explain.py:225`).
- Numeric decisions are protected: Muse can't change class, tier or cost, and caps are clamped (`explain.py:140-158`).

*Fix:* strict hostname and email validation, an `untrusted` wrapper field for all external text, and all code-derived strings inside the untrusted block.

### W-1. Open redirect after sign-in — frontend
`apps/web/src/app/(auth)/google-sign-in.tsx:77` accepts any `returnTo` that starts with `/` but not `//`. `/\evil.com` and `/\t/evil.com` pass and resolve off-site. This makes a good phishing hop right after a real Google sign-in. *Fix:* `new URL(returnTo, location.origin)` and require the same origin.

### W-2. Invite-only sign-up not enforced — frontend, security
`apps/web/src/lib/auth/identity-platform.ts:57-77` mints a session for any verified Google account, and no `beforeCreate` blocking function exists (`docs/CHECKLIST.md:19` says "designed"). The API rejects unknown users (`auth.py:137-141`), so the impact today is limited to free Identity Platform accounts and the console shell. It is still the signup gate the abuse model relies on. *Fix:* reject non-invited users in `createSession` now, and deploy the blocking function later.

### L-1. Linked-project deployer has project-wide powers in the customer's project — security — latent
`infra/scripts/link-project.sh:47-58` grants project-level `run.developer`, Run `setIamPolicy`, `iap.admin` and `cloudscheduler.admin`. A compromised ShipCube (see H-1) can make any of the customer's Cloud Run services public, or delete them. *Fix:* use IAM conditions on a resource-name prefix or a tag, and grant IAP per service. Also verify that `admin.py:100-106` derives `deployer_sa` from the workspace id instead of trusting the CLI argument.

### CI-1. CI deployer is effectively owner of the control project — platform, security
- `deploy/wif.sh:25` grants `run.admin`, `storage.admin` and `cloudbuild.builds.editor` project-wide, plus `serviceAccountUser` on `sc-api` (which inherits H-1).
- Terraform state lives in `gs://shipcube-control-ks-tfstate` in the same project.
- WIF trusts `repository == … && ref == refs/heads/main` (`wif.sh:18`). That's good, but it doesn't pin `repository_id`, the workflow, or an environment with reviewers. Actions with `id-token: write` are pinned by tag.

*Fix:* scope grants per bucket and per SA, move state to its own project, add `repository_id` and `job_workflow_ref` conditions plus a protected environment, and pin actions by SHA.

### M-1. Classifier accuracy is presented more strongly than the evidence supports — load-estimator
`docs/ARCHITECTURE.md:149` advertises held-out accuracy and F1, but the numbers in `docs/research/load-estimation.md` §3 and `artefacts/metrics.json` say:
- Random trace split: macro-F1 0.87, but labels are weak labels from the same rule thresholds.
- Leave-one-source-out: macro-F1 **0.39** (Azure Functions 0.20).
- "Held-out apps" 10/10: n=10, written by the team, looked at three times with fixes in between. That makes it a dev set.
- Production profiling runs on 1 vCPU while the `compute_heavy` rule needs ≥ 1.5 cores, so production can never emit that class (`gcp_runner.py:190`, `rules.py:22,48`).
- Users see confidence as a percentage (`explain.py:83`).

The research doc is honest about all of this. The headline docs and UI are not. A judge who reads §3 will notice the gap. *Fix:* lead with LOSO and real-app numbers with intervals, show confidence as low/medium/high, say "estimate" rather than "predicts", and recalibrate on the real Cloud Run job.

### R-1. Single points of failure — platform
- Cloud SQL is `db-f1-micro`, `ZONAL`. Backups (14) and 7-day PITR are on, which is good.
- There is one worker (`maxScale 1`).
- The console's only revision is failing its startup probe right now.
- Postgres is the queue, the rate counter store (planned) and the catalog.

This is acceptable for a hackathon, but say so. Document the RTO, and run one restore drill from backup before Nov 4.

---

## Low

- **S-9:** `approve_operation` updates with no `status='awaiting_approval'` guard and no `FOR UPDATE` (`store.py:370-379`), so a concurrent cancel can be flipped back to queued.
- **S-10:** console token verification uses `verify_firebase_token` with no revocation or `email_verified` check, and fetches certs uncached (`auth.py:134`). The console sends a **session cookie** (`identity-platform.ts:95-99`), which that call won't accept. Switch to `verify_session_cookie(check_revoked=True)`.
- **S-11:** `secrets` in deploy requests are silently dropped (`worker.py:265`), which nudges agents to put keys in `env`. Env values are then stored in plaintext in `operations.input` and `approvals.request` (`store.py:312,323`), which workspace viewers can see.
- **S-12:** `audit_log` is never written by the API or worker. Approvals, grants and deletes leave no app-level audit trail (GCP audit logs exist).
- `BYOK base_url` is stored unvalidated (`store.py:548-553`, `models.py:186`). The gateway checks addresses at connect time (`ssrf.go`), which is good. Validate at write time too, and add NAT64, 6to4, `198.18/15` and `240/4` to the blocklist (G-9).
- **G-7:** the gateway registry cache is unbounded and keyed by an attacker-chosen header (`config.go:147-163`). Latent.
- **G-10:** JWKS refresh runs under a global mutex (`auth.go:129-140`).
- **G-12:** the gateway builds with `golang:1.23` (out of support) and tag-pinned base images.
- **D-6..D-11 (data layer):**
  - Schema-qualified cube tables skip tenant routing (`compiler.py:264-265`).
  - Tenant attributes can name another workspace (`identity.py:91-93`).
  - The L3 raw-SQL validator is a denylist with gaps (`query_to_xml`, CTE shadowing; `rawsql.py:22-67`). It is off by default.
  - Muse-drafted migrations can contain any op type, with a model-written description (`drafting.py:219-222`).
  - Segments skip visibility checks (`compiler.py:443-448`).
  - Cost guards are per query only, and `sqlglot>=25.0` is unbounded with no lockfile.
- `/docs` and `/openapi.json` are public on `shipcube-mcp`. They're harmless but advertise every route. Consider disabling them in production.
- Builder images are unpinned: `gcr.io/buildpacks/builder:latest` and `gcr.io/k8s-skaffold/pack` with no tag run with the builder's credentials. `.terraform.lock.hcl` is gitignored.
- Repo hygiene:
  - `services/gateway/gateway` is a committed 10.8 MB macOS binary with local paths; the PR deletes it.
  - `.gitignore` doesn't cover `.env.*`.
  - `deploy/deploy.sh:34` injects an unused `shipcube-dev-token` secret; delete the secret.
  - `services/api/scripts/gcp_setup.sh:30` resets the registry cleanup to 1 day, which breaks 7-day restores if re-run.
  - No secrets were found in tracked files or history.
- Console cookies: `secure` only when `NODE_ENV=production`, no `__Host-` prefix, and the mock-auth guard keys only on `K_SERVICE` (`apps/web/src/lib/auth/cookies.ts:6,9`, `lib/server/config.ts:48`).
- `shipcube-worker` has `ingress: all`. It's IAM-gated so it's fine, but `internal` costs nothing.
- Cloud SQL has `ipv4Enabled: true` with no authorized networks and `ENCRYPTED_ONLY`. Acceptable, but private IP only is the target.

---

## Product-level risks a judge or customer might raise

- **"Production standard" vs. what runs.** The estimator isn't installed in the production image. The data tools aren't mounted. The console is down. There's no alerting, T2 (jobs and scheduler) isn't built, and linked projects have never run against a real project (`services/api/README.md` "Known limits"). Each of these is fine on its own. Together they don't match a "production" claim. Say "what's live" explicitly in the README.
- **README overclaims:**
  - "GKE Autopilot" placement, "continuous monitoring … with suggestions to move an app", "spend limits so nothing runs away": the spend cap is not enforced. `store.py:66` `ENFORCED_BY` names a cost meter that doesn't exist, and `requests_per_minute` isn't enforced on IAP-direct apps.
  - "Only the owner and the people they allow can open an app" holds today only because of IAP, and S-3/S-4 break it.
- **Load estimation:** see M-1. The interesting, honest story is "rules plus a classifier that knows when it's unsure". The weak story is "predicts how your app behaves".
- **Trust model:** users hand an MCP token with deploy rights to an agent that reads untrusted code and tool output. Approvals cover the riskiest actions, which is a good design point. Make sure every widening action (domain grant, cap raise, image source, new linked project) is behind one, and that the approval page shows non-LLM text first.
- **Single-tenant shared project:** all hosted tenants share one GCP project, one builder, one registry and one NAT IP. That's fine for a demo. Say so, and keep C-1 and H-2 closed so it stays fine.

---

## What's done well

- PATs: `HMAC-SHA256(pepper, secret)` with a unique-index lookup. Expiry, revocation and disabled-user checks happen in one statement. Migration 0004's policy only reveals the row matching the presented HMAC. Live probes with garbage, wrong-secret, SQL-ish, `alg:none` and 20 KB tokens all returned 401.
- Forced RLS on tenant tables. Caller context is set per transaction with `set_config(..., true)` and a whitelisted `SET LOCAL ROLE`. All SQL is parameterized.
- Approvals require a console session (`via=console`), enforced in the API and again in RLS. Delete always needs approval, and `domain:` grants need approval. `allUsers` and `allAuthenticatedUsers` are rejected by the principal regex.
- Apps deploy with IAP on and the IAP agent as the only invoker. Live check: the direct `run.app` URL of `hello-tracker-b2ef0b` redirects to Google sign-in.
- Linked targets are resolved under RLS, and a project can be linked to only one workspace. I found no confused deputy between workspaces.
- Gateway: strict ID-token verification (RS256, kid, iss, exact aud, `email_verified`). SSRF checks run at connect time, with no redirects and https only. Keys are redacted, prompts are not logged, and body and time limits are set. It has no external dependencies.
- Profiler job: zero-role SA, deny-all VPC, `maxRetries 0`, digest-pinned image, cleanup in `finally`. The explainer can't change class, tier or cost.
- Cloud SQL: IAM auth, TLS only, deletion protection, PITR, pgaudit. The postgres password never enters Terraform state.
- Audit logs for Secret Manager, SQL, IAM and IAP go to a 365-day bucket. Buckets enforce public-access prevention.
- CI uses `pull_request` (never `pull_request_target`) with `contents: read`. WIF is keyless and conditioned on the repo and `main`. Branch protection is on.
- The budget kill switch receives updates every ~30 min and its dry run passed. Only the billing system can publish to its topic.
- `docs/research/load-estimation.md` reports its own weaknesses.

---

## Best practices: where ShipCube should head

For each area: what good looks like, where ShipCube is today, the concrete change, the owner, and whether it fits before Nov 4 (**now**) or after (**next**). Most of this is configuration, not new systems. ShipCube is on Cloud Run, Cloud Monitoring and Terraform, so everything is expressed in those terms.

### 1. One service spec with safe defaults and small overrides
- **Good:** every ShipCube service (console, MCP/API, worker, gateway, kill switch) and every user app is described by the same small spec. It has opinionated defaults: probes, CPU and memory, min/max instances, concurrency, timeout, ingress, SA, secrets by pinned version, labels. Each service or environment overrides only the fields it changes, and the spec is validated by a schema in CI.
- **Today:** settings are spread across `deploy/deploy.sh` flags, the PR's `deploy/services/*.yaml`, Terraform, and `gcp.py:_service_body` for user apps. Defaults differ between them.
- **Change:** a Terraform module `cloud_run_service` with variables for those fields and the defaults above, used for ShipCube's own services. Make `_service_body` read the same default table. Add a JSON schema for app caps and use it in both places. Owner: platform (module), networking (app defaults). **Next**. For now, write the defaults table down in `deploy/README.md`.

### 2. Health checks
- **Good:** three distinct checks:
  - a startup probe with a generous boot budget (period ~3–5 s, about 1–2 minutes total);
  - liveness on a cheap endpoint (period ~10–30 s, failure threshold 3);
  - readiness that reflects dependencies (DB reachable, schema current) and is reported, not used to kill the instance.
  Workers with no HTTP server have no HTTP probes; they report a heartbeat instead.
- **Today:** `shipcube-mcp` has startup and liveness probes on `/healthz`, which is good. The console's startup probe is failing with no alert. User apps get no probes from `_service_body`.
- **Change:**
  - Add a startup probe to the default user-app template (TCP on `$PORT`, which buildpacks always satisfy).
  - Add an `http /ready` that checks DB plus schema version to the API.
  - Have the worker export "last successful claim loop" as a log-based metric.
  - Owner: networking, platform. **Now**.

### 3. Resources, autoscaling and quotas
- **Good:** every service declares CPU, memory, concurrency and a max-instances ceiling. Scale-to-zero is the default for request services. Always-on CPU is used only where a poller needs it. A project-level quota override sits under the per-app caps as a hard backstop, so a bug can't scale past it.
- **Today:** per-service values exist and are small, which is good. User-app caps are Pydantic-bounded (`max_instances ≤ 10`). There are no project quota overrides (O-2), and the always-on worker is the largest steady cost (~$45–55/month).
- **Change:**
  - `google_service_usage_consumer_quota_override` in the apps project for Cloud Run CPU per region (e.g. 20 vCPU) and Cloud Build concurrent builds (e.g. 3).
  - A global ceiling on the sum of all apps' `max_instances` in the API.
  - Consider running the worker on request (Cloud Scheduler tick, or Pub/Sub push on enqueue) instead of always-on.
  - Owner: security (quotas), networking (ceiling), platform (worker). **Now** for quotas.

### 4. Rollout and rollback
- **Good:**
  - Images are immutable and deployed by digest, tagged with the git SHA.
  - A new revision gets a no-traffic tag and is health-checked on its tagged URL before traffic moves to it.
  - Rollback means moving traffic to the previous revision, and the deploy record in git (or the operations table) is the source of truth.
  - A stuck rollout fails with a deadline instead of hanging.
  - Migrations are expand-then-contract, so the previous revision keeps working.
- **Today:**
  - User apps deploy by tag (C-1). Rollback pins traffic, but the next deploy silently un-pins it (`services/api/README.md`).
  - The control-plane deploy is a shell script. `_wait_op` already has a 600 s deadline, which is good.
- **Change:**
  - Store and deploy digests (also fixes part of C-1).
  - In `gcp.py`, deploy user apps with `--no-traffic` plus a revision tag, check readiness, then shift traffic.
  - For ShipCube's own services, do the same in `deploy.yml`, with an automatic traffic revert if the uptime check fails within 5 minutes.
  - Owner: networking (apps), platform (control plane). **Now** for digests, **next** for staged traffic.

### 5. Alerting and SLOs as code
- **Good:**
  - Alert policies live in Terraform next to the service they watch.
  - Every alert has a severity (page or ticket), an owner, a `for` duration of 5–15 minutes against flapping, and a runbook link in its documentation field.
  - Each user-facing service has one or two SLOs, defined with Cloud Monitoring's SLO API and alerted on burn rate (for example 99.5% availability: page when 1 h and 5 min windows both burn above 14×, ticket when 6 h and 30 min windows both burn above 6×).
  - Staleness alerts cover background work: oldest queued operation, last kill-switch message, last successful worker loop.
  - Non-production never pages.
- **Today:** policies exist only as a script (`deploy/monitoring.py`) that hasn't been run (O-1). There are no SLOs, no staleness alerts and no per-app alerting.
- **Change:**
  - **Now:** run `deploy/monitoring.py` against both projects. Then add three staleness alerts as log-based or SQL-exported metrics:
    - oldest `queued` operation older than 10 minutes;
    - no `budget update` log for 2 hours;
    - an approval pending for more than 24 hours.
  - **Next:** port the policies to `infra/terraform/monitoring.tf` with a `severity` and `runbook_url` on each. Add SLOs for MCP availability (99.5%) and MCP p95 latency (< 1 s excluding `deploy_app`) with burn-rate alerts. Offer users a default "5xx > 5% for 10 minutes" alert per app, created at deploy time.
  - Owner: platform.

### 6. Logging, tracing and correlation
- **Good:**
  - Every service logs structured JSON with `severity`, a server-generated `request_id`, and the Cloud Trace field.
  - Ids propagate across the MCP → API → worker → GCP calls.
  - Trace sampling is set per service by environment (e.g. 10% in production, 100% for errors).
  - Logs never contain secrets or prompts.
  - Noisy logs are dropped at the sink, not in code.
- **Today:** the API, worker and gateway already do structured logs with request, operation and trace ids. That is the strongest operational area. The gaps:
  - client-supplied request ids are trusted for metering (G-6);
  - user-app logs are not linked to the operation that deployed them;
  - there's no app-level audit log (S-12).
- **Change:**
  - Generate ids server-side and keep the client's as `client_request_id`.
  - Label each app revision with `shipcube_operation_id`.
  - Write `audit_log` rows for approvals, grants, deletes and token use.
  - Owner: networking, platform. **Now** (small).

### 7. Secrets
- **Good:**
  - Secrets live only in Secret Manager. Services reference them by **pinned version**, so rotation is deliberate.
  - Each runtime identity can read only the secrets it needs, through per-secret IAM.
  - There are no static keys, and CI uses WIF.
  - Secret values never appear in env vars stored in the DB, tool arguments or logs.
  - Unused secrets are deleted.
- **Today:**
  - Pinned versions and WIF: good.
  - `sc-api` is secret *admin* (H-1).
  - Deploy-time `secrets` are dropped, and env values are stored in plaintext (S-11).
  - An unused `shipcube-dev-token` is still injected on `main`.
- **Change:**
  - Per-secret `secretAccessor`.
  - Implement `secrets: [{name, secret, version}]` in `_service_body` as Cloud Run secret env refs.
  - Redact env values from `operations.input`.
  - Delete the dev token.
  - Owner: security, networking. **Now**.

### 8. Network: default-deny at the edge and in egress
- **Good:**
  - Every exposed endpoint states its access policy explicitly: IAP group, public-with-auth, or internal.
  - Anything internal uses `ingress: internal`.
  - Untrusted code runs with egress denied, including DNS, and outbound calls from trusted services go through an allowlist.
  - Identity headers from clients are stripped before trusted ones are injected.
- **Today:**
  - IAP on every app, the IAP agent as the only invoker, and a deny-all profiler VPC: good.
  - The worker and kill switch have `ingress: all`.
  - Profiler DNS is open (P-4).
  - The API clones arbitrary git URLs with full egress (P-3).
  - There is no org policy blocking `allUsers`.
- **Change:**
  - `ingress: internal` on the worker.
  - A Cloud DNS response policy on the profiler VPC.
  - Https-only, public-IP-only git fetches.
  - A periodic check (Cloud Asset Inventory query or a scheduled script) that no Cloud Run service in the apps project has `allUsers`, alerting if one does. This replaces the org policy ShipCube can't have.
  - Owner: security, networking. **Now** for the check and ingress.

### 9. IAM: least privilege, in Terraform only
- **Good:**
  - All IAM lives in Terraform. There are no project-wide `serviceAccountUser`, `serviceAccountAdmin` or `secretmanager.admin` grants; these are per-resource bindings or IAM conditions on name prefixes.
  - Default compute SAs have no roles.
  - The kill switch's identity is the only one with billing rights.
  - A CI check fails the plan on any new primitive role or project-wide `actAs`.
- **Today:** H-1, H-2, CI-1 and L-1. Some grants come from scripts (`bootstrap.sh`, `gcp_setup.sh`, `wif.sh`, the kill switch's `deploy.sh`) and have drifted from Terraform.
- **Change:**
  - The narrowing in H-1 and H-2.
  - Remove Editor from both default compute SAs.
  - Import the script-made grants into Terraform.
  - A `conftest` / `gcloud asset analyze-iam-policy` step in CI.
  - Owner: security. **Now** for the narrowing.

### 10. Tenant isolation as a property you test
- **Good:** each tenant boundary has an automated negative test that runs in CI: RLS cross-workspace reads, builder identity, registry paths, data-layer policies under every query option. The tests run against the real stack (Postgres, an emulator or a staging project), not only mocks.
- **Today:** `infra/db/checks/check.sh` proves RLS against the live DB, which is excellent. Elsewhere tests cover happy paths. Nothing tests revocation propagation, idempotency hashes, slug reuse, `model_version` pinning or cross-tenant images. `test_gcp_deployer.py` uses a faked session.
- **Change:**
  - Turn every finding above into a failing test first. The scratch reproductions for S-3 and S-4 already exist.
  - Run `check.sh` in CI against the Postgres service container.
  - Add one GCP integration test per release that tries a cross-tenant image pull and expects it to be refused.
  - Owner: every workstream for its own findings. **Now** for the S-3, S-4 and C-2 tests.

### 11. Supply chain
- **Good:** base and build images are pinned by digest. Lockfiles are committed and installed with `--locked` (including Terraform's `.terraform.lock.hcl`). Actions are pinned by SHA. Dependabot or Renovate opens bump PRs. No binaries are committed.
- **Today:** uv lockfiles and `--locked` for api and migrate are good. Images, the buildpacks builder, `pack`, actions and Terraform providers are pinned by tag or not at all. `shipcube-data` has no lockfile.
- **Change:** pin the five images by digest, commit the TF lock file, pin actions by SHA, and add Dependabot. Owner: platform, data-layer. **Now** (an hour's work).

### 12. Cost guardrails in layers
- **Good:** four layers, each one tighter and faster than the next:
  1. per-app caps enforced in code;
  2. per-workspace quotas: apps, deploys, builds and estimates per day, AI tokens;
  3. project quota overrides;
  4. a billing kill switch at about 70% of the budget.
  
  A dashboard shows spend by workspace from billing export labels, and an alert fires on any day's spend above 3× the trailing average.
- **Today:** layer 1 is partial: the spend cap isn't enforced, and the AI cap fails open for unpriced models. Layer 2 covers apps and deploys only. Layer 3 is missing. Layer 4 trips at about 95%.
- **Change:** G-1, A-2, O-2. Label every Cloud Run service and build with `shipcube_workspace`, and enable billing export to BigQuery so cost per workspace is visible. Owner: platform, security, networking. **Now** for labels and the kill-switch threshold.
