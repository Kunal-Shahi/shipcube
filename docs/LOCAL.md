# Running ShipCube locally

The whole stack runs on one machine with Docker. Every component is the real implementation behind
the same interface production uses; one env var per component switches it between local and GCP.
Use it to test every change before it's pushed or deployed.

```bash
make up      # build, start Postgres, migrate, seed, start the API, worker and gateway
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
| Postgres 16 | container `postgres` (127.0.0.1:55432 by default), roles and RLS from `infra/db/bootstrap.sql` unmodified | (connection string) | Cloud SQL |
| Migrations | `make migrate`: `owner_defaults.sql` + `python -m shipcube_api.admin migrate` as `sc-migrator`, never on startup | | `infra/db/migrate.sh` |
| API + MCP | container `api` (127.0.0.1:18080, MCP at `/mcp`) as `sc-api` → `shipcube_app` | | Cloud Run `shipcube-mcp` |
| Worker | container `worker` as `sc-worker` → `shipcube_worker` (BYPASSRLS) | `SHIPCUBE_EMBEDDED_WORKER=0` | Cloud Run `shipcube-worker` |
| Deployer | apps as Docker containers on network `shipcube-local` | `SHIPCUBE_BACKEND=local` | `gcp` |
| Uploads | local object store (part of the local deployer) | `SHIPCUBE_BACKEND=local` | GCS |
| Load estimator | classifier + profiler + explainer | `SHIPCUBE_ESTIMATOR=placement` | same |
| Profiler sandbox | `docker run --network none` | `SHIPCUBE_PROFILER=local` | Cloud Run job |
| Monitoring | Docker stats and container logs | `SHIPCUBE_MONITOR=local` | `gcp` |
| AI gateway | container `gateway` (127.0.0.1:18090), per-app `scg_` tokens | `META_API_KEY` / `LOCAL_LLM=ollama` | Cloud Run, Google ID tokens |
| Data layer | DuckDB demo warehouse, workspace from the seed | `SHIPCUBE_DATA_WORKSPACE` | same |
| Console | `npm run dev` on the host | see below | Cloud Run |

`SHIPCUBE_DEV_MODE=1` on the API and worker only enables the dev console sign-in. The explicit
switches above win over it, so nothing falls back to canned data. The mocks (`SHIPCUBE_BACKEND=mock`,
`SHIPCUBE_ESTIMATOR=dev`, `SHIPCUBE_MONITOR=mock`) are for unit tests.

**Model calls.** With `META_API_KEY` in `.env`, the gateway and the estimator's explainer call Muse
for real. Without it, `LOCAL_LLM=ollama` starts Ollama with a small model (~400 MB) as the gateway's
upstream. With neither, the smoke test skips the model call.

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
8. the gateway: an unauthenticated call is rejected, then one model call.

Each step prints `ok`, `FAIL` or `skip`, and the exit code is non-zero on any failure. CI runs the
same `make up && make smoke` (job `local-stack`).

## Runbook

| Symptom | Fix |
|---|---|
| `Docker isn't running` | `colima start` (macOS) |
| `api` unhealthy, logs say `schema is not current` | `make migrate`. Services never migrate themselves. |
| Postgres init changed (`deploy/local/postgres/`) | `make reset`. Init scripts only run on an empty volume. |
| `already seeded` but the token doesn't work | The database was reset without the seed: `make reset && make up` |
| Port 18080, 18090 or 55432 in use (or requests reach the wrong server) | `lsof -nP -iTCP:<port> -sTCP:LISTEN`; change `SHIPCUBE_API_PORT` / `SHIPCUBE_GATEWAY_PORT` / `SHIPCUBE_PG_PORT` in `deploy/local/.env` |
| A deployed local app is stuck | `docker ps --filter label=managed-by=shipcube`, then `docker rm -f <id>`. `make reset` removes them all. |
| See what the worker is doing | `make logs worker`, or `docker compose -f deploy/local/compose.yaml run --rm admin ops` |
| Gateway model call `skip` | Set `META_API_KEY` or `LOCAL_LLM=ollama` in `deploy/local/.env`, then `make reset && make up`. The seed picks the upstream. |
