# Running ShipCube locally

The whole stack runs on one machine with Docker. Every component is the real implementation behind
the same interface production uses; one env var per component switches it between local and GCP.
Use it to test every change before it's pushed or deployed.

```bash
make up      # local secrets, build, Postgres + migrate, seed, then API, worker, gateway, uploads, IAP
make smoke   # drive all 16 MCP tools end to end, plus the gateway
make down    # stop (data kept)    ·    make reset   # stop and delete data, seed and local apps
```

## Prerequisites

- Docker. On macOS, Colima: `brew install colima docker docker-compose`, then `colima start --cpu 4 --memory 6`.
  Link the compose plugin once: `mkdir -p ~/.docker/cli-plugins && ln -sfn "$(brew --prefix)/opt/docker-compose/bin/docker-compose" ~/.docker/cli-plugins/docker-compose`.
- `uv` (the smoke test), `openssl`, `python3`. Node 22 for the console.

## Components and their switches

Switches live in `deploy/local/.env`, created from `deploy/local/.env.local.example` on the first `make up`.

| Component | Local | Switch | GCP |
|---|---|---|---|
| Postgres 16 | `db` (127.0.0.1:54329) from `infra/local/compose.yaml` (security): the Cloud SQL IAM logins, `bootstrap.sql` and `owner_defaults.sql` unchanged, so roles and RLS match production | (connection string) | Cloud SQL |
| Secrets | `.local/secrets.env` and `.local/secrets.json` from `infra/local/localsecrets.py`, served by the `secretmanager` emulator (127.0.0.1:8085) | `SECRET_MANAGER_ENDPOINT` (DEV_MODE only) | Secret Manager |
| Migrations | `make migrate`: the API's runner as `sc-migrator` (`migrate` service), never on startup | | `infra/db/migrate.sh` |
| API + MCP | container `api` (127.0.0.1:18080, MCP at `/mcp`) as `sc-api` → `shipcube_app` | | Cloud Run `shipcube-mcp` |
| Worker | container `worker` as `sc-worker` → `shipcube_worker` (BYPASSRLS) | `SHIPCUBE_EMBEDDED_WORKER=0` | Cloud Run `shipcube-worker` |
| Deployer | `pack build` (buildpacks) or the app's Dockerfile, then a container on network `shipcube`, behind `local-iap` (networking) | `SHIPCUBE_BACKEND=local` (needs DEV_MODE) | `gcp` |
| Linked GCP projects | Simulated: the permission check passes for any project ID except `broken-*` (which reports `run.services.create` missing), and linked apps run in Docker like the rest. Exercises linking, `whoami` targets and first-deploy approval | `SHIPCUBE_BACKEND=local` | `testIamPermissions` as the workspace's `ws-*-deployer` SA |
| Uploads | `fake-gcs` (127.0.0.1:4443): signed PUT URLs and reads, the same code path as GCS | `SHIPCUBE_BACKEND=local` | GCS |
| App access (IAP) | `local-iap` (127.0.0.1:8088): `http://<service>.localhost:8088`, only principals on the app's access list (401/403/404 like IAP) | `SHIPCUBE_BACKEND=local` | IAP on Cloud Run |
| Load estimator | classifier + profiler + explainer | `SHIPCUBE_ESTIMATOR=placement` | same |
| Profiler sandbox | `docker run --network none` | `SHIPCUBE_PROFILER=local` | Cloud Run job |
| Monitoring | Docker stats and container logs | `SHIPCUBE_MONITOR=local` | `gcp` |
| AI gateway | container `gateway` (127.0.0.1:18090), per-app `scg_` tokens | `META_API_KEY` / `LOCAL_LLM=ollama` | Cloud Run, Google ID tokens |
| Data layer | DuckDB demo warehouse, workspace from the seed | `SHIPCUBE_DATA_WORKSPACE` | same |
| Console | `npm run dev` on the host | see below | Cloud Run |

`SHIPCUBE_DEV_MODE=1` on the API and worker only enables the dev console sign-in. The explicit
switches above win over it, so nothing falls back to canned data. The mocks (`SHIPCUBE_BACKEND=mock`,
`SHIPCUBE_ESTIMATOR=dev`, `SHIPCUBE_MONITOR=mock`) are for unit tests.

**Model calls.** With `MUSE_API_KEY` (or `META_API_KEY`) in the repo-root `.env`, the local secrets step
puts it in the Secret Manager emulator and the gateway and the estimator's explainer call Muse for real. Without it, `LOCAL_LLM=ollama` starts Ollama with a small model (~400 MB) as the gateway's
upstream. With neither, the smoke test skips the model call.

**Builder.** Apps without a Dockerfile are built with buildpacks, as Cloud Build does. Production's
builder (`gcr.io/buildpacks/builder`) is amd64-only, so on arm64 Docker hosts (Apple Silicon) `make up`
uses `heroku/builder:24` instead; CI and other amd64 hosts use production's. Set `SHIPCUBE_BUILDER` to
override.

**Opening a deployed app.** Apps live at `http://<service>.localhost:8088`. Sign in once at
`http://localhost:8088/_iap/login`, or send `Authorization: Bearer <PAT>`.

## Seed

`make seed` (run by `make up`) creates:

- user `dev@shipcube.local`, as owner of workspace `demo`;
- a PAT;
- a dev console token;
- a gateway app token.

Everything is written to `deploy/local/.state/seed.env` (mode 600, gitignored). These are local-only
credentials. To use the local MCP server from Claude Code:

```bash
claude mcp add --transport http shipcube-local http://localhost:18080/mcp --header "Authorization: Bearer $(grep ^SHIPCUBE_PAT= deploy/local/.state/seed.env | cut -d= -f2)"
```

## Console

```bash
cd apps/web && npm install
DEV_MODE=1 AUTH_PROVIDER=mock SHIPCUBE_API_URL=http://localhost:18080 \
SHIPCUBE_MCP_URL=http://localhost:18080/mcp \
SHIPCUBE_DEV_API_SESSION=$(grep ^SHIPCUBE_DEV_CONSOLE_TOKEN= ../../deploy/local/.state/seed.env | cut -d= -f2) \
npm run dev
```

Then open http://localhost:3000. The dev sign-in acts as the seeded user.

## The smoke test

`make smoke` connects to `http://localhost:18080/mcp` with the seeded PAT and runs:

1. list the tools (expects 16) and `whoami`;
2. `create_upload`, then PUT `examples/hello-ai`;
3. `estimate_load`, then `deploy_app`, following `operation` until done;
4. check that the app answers on its URL;
5. `inspect_app` (status, logs, metrics), then `list_apps`, `set_caps`, `set_access`, `ai_config`;
6. the five `data_*` tools;
7. `delete_app`, approved with the console token, the way a person approves in the console;
8. the gateway: an unauthenticated call is rejected, then one model call;
9. data-layer's own MCP-level smoke (`shipcube-data smoke --mcp`): exactly five data tools, partition
   pruning, and that agents can't apply migrations.

Each step prints `ok`, `FAIL` or `skip`, and the exit code is non-zero on any failure. CI runs the
same `make up && make smoke` (job `local-stack`).

## Console end-to-end tests

`make e2e` (after `make smoke`) starts the console and the Firebase Auth emulator, the `console`
compose profile from frontend's PR #34, then runs the Playwright suite in `apps/web`:

1. sign in through the emulator;
2. create a PAT;
3. check that the setup guide shows the local MCP URL;
4. find the smoke app in the list;
5. grant access and change the spending cap;
6. delete the app (approved in the console), then restore it.

It needs PR #13 (`/v1/tokens`) and PR #34 merged, and internet access (the emulator's sign-in popup
loads Google scripts). `SHIPCUBE_CONSOLE_AUTH=mock` uses the mock sign-in instead of the emulator. The
console then calls the API as the seeded dev user, whoever signs in; the API verifies only
Google-signed tokens.

## Runbook

| Symptom | Fix |
|---|---|
| `Docker isn't running` | `colima start` (macOS) |
| A container can't find a mounted file (e.g. `secretmanager` exits 2) | Colima shares only `$HOME` into its VM: keep the checkout under your home directory, not `/tmp` |
| `api` unhealthy, logs say `schema is not current` | `make migrate`. Services never migrate themselves. |
| Database roles or passwords changed (`infra/local/`) | `make reset`. `db-init` sets up an empty database. |
| App build fails in `[detector]` with an `asm_amd64` stack | An amd64-only builder on an arm64 host: set `SHIPCUBE_BUILDER=heroku/builder:24` |
| App URL returns 401/403 | Expected without a token, or for principals not on the access list: send the PAT, or sign in at `/_iap/login` |
| `already seeded` but the token doesn't work | The database was reset without the seed: `make reset && make up` |
| A port is in use (or requests reach the wrong server) | `lsof -nP -iTCP:<port> -sTCP:LISTEN`; change `SHIPCUBE_API_PORT`, `_GATEWAY_PORT`, `_DB_PORT`, `_IAP_PORT`, `_GCS_PORT` or `_SECRETMANAGER_PORT` in `deploy/local/.env` |
| A deployed local app is stuck | `docker ps --filter label=managed-by=shipcube`, then `docker rm -f <id>`. `make reset` removes them all. |
| See what the worker is doing | `make logs worker`, or `docker compose -f deploy/local/compose.yaml run --rm admin ops` |
| Gateway model call `skip` | Set `META_API_KEY` or `LOCAL_LLM=ollama` in `deploy/local/.env`, then `make reset && make up`. The seed picks the upstream. |
