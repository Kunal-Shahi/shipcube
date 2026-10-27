# Workstreams

Each workstream runs in its own Claude Code session, on its own branch, and opens a PR to `main`. Only touch the paths you own. To change a shared file (`README.md`, `docs/ARCHITECTURE.md`), put the proposed change in your PR description instead of editing the file.

| Workstream | Owns | Delivers |
|---|---|---|
| **Frontend** | `apps/web/**`, `docs/design/**` | Design system; sign-up and sign-in (Google); MCP setup guide; app list and detail, access, caps, monitoring screens (mock data is fine) |
| **Backend networking** | `docs/research/networking.md`, `services/mcp/**`, `services/api/**` (design and skeleton) | MCP transport and auth; control-plane API; request flow from agent → MCP → API → GCP; how user apps are reached (domains, ingress, proxy) |
| **Infra and security** | `docs/research/security-tenancy.md`, `docs/research/database-design.md`, `infra/**`, `services/gateway/**` (design) | Who can access what; Google SSO; linking a user's own GCP project; secrets; the database schema that follows from all of this |
| **Language trade-offs** | `docs/research/language-tradeoffs.md`, `research/bench/**` | Go vs Python 3 for each component, with small benchmarks and a recommendation weighted by how fast we can ship in 10 days |
| **Data layer** | the separate repo [Kunal-Shahi/shipcube-data](https://github.com/Kunal-Shahi/shipcube-data) | Semantic layer whose models live in a catalog database and change through model migrations; one warehouse database with multiple schemas and partitions; typed query levels L0–L3; MCP tools for data. It replaces `packages/semantic/` in this repo |

## Team roster

Kunal is the lead and makes every product and merge decision. Each teammate is a Claude Code session.

| Teammate | Session name | Branch / PR | Status |
|---|---|---|---|
| Frontend | `frontend` | [#3](https://github.com/Kunal-Shahi/shipcube/pull/3) | Console on mock data; auth behind an adapter interface |
| Backend networking | `networking` | [#1](https://github.com/Kunal-Shahi/shipcube/pull/1) | MCP stub (17 tools) + API skeleton; being ported from TypeScript to Python |
| Infra and security | `security` | [#2](https://github.com/Kunal-Shahi/shipcube/pull/2) | Tenancy research, DB schema, Terraform skeleton |
| Language trade-offs | `language` | [#4](https://github.com/Kunal-Shahi/shipcube/pull/4) | Recommends Python for the backend, Go for the gateway |
| Data layer | `data-layer` | shipcube-data repo | Library with five `data_` tools; 109 tests |
| Load estimator | `load-estimator` | (PR pending) | Owns `ml/workload-classifier/**`, `packages/profiler/**`, `packages/placement/**`, `docs/research/load-estimation.md` |
| Platform | `platform` | (PR pending) | Owns `packages/monitor/**`, `services/gateway/**` (build; security keeps the design), `.github/workflows/**`, `deploy/**` |

## Interfaces between teammates

- **Networking ↔ load-estimator:** `estimate_load` calls the load-estimator's in-process `estimate(source, hints)`.
- **Networking ↔ platform:** `inspect_app` calls platform's monitoring provider. Deployed apps call platform's Go AI gateway. Networking builds the real deploy path (Cloud Build → Cloud Run + IAP); platform hosts ShipCube itself and runs CI.
- **Security ↔ platform:** platform builds `services/gateway` from security's design and proposes `infra/**` changes through its PR instead of editing them.

- **Frontend ↔ security:** sign-in uses Identity Platform (Google provider). The frontend adds an Identity Platform adapter next to `apps/web/src/lib/auth/mock.ts`. The security doc owns token minting, invites and signup gating.
- **Frontend ↔ networking:** the console calls the control-plane API (`services/api` OpenAPI spec). The MCP setup guide must match the real endpoint, PAT format (`sc_pat_…`) and tool list in `docs/research/networking.md`.
- **Networking ↔ security:** the API is the only place that makes authorization decisions. Security owns the token, grant and audit tables in `docs/research/database-design.md`. Networking's access-proxy plans must match the IAP decision and the `services/gateway` design.
- **Networking ↔ data layer:** the data layer's MCP tools (`list_cubes`, `describe_cube`, `query`, `propose_migration`, `apply_migration`, `search_values`) are mounted on ShipCube's MCP server. The data layer exposes them as a Python package the MCP server can call in-process.
- **Security ↔ data layer:** warehouse row-level security is injected as filters from the caller's identity and grants. Tenant-to-schema routing uses workspace ids from the security schema.
- **Everyone ↔ language:** follow the language decision (see Decided).

## Open decisions (Kunal)

1. **Merge order** for PRs #1–#4.

## Decided

- **MCP shape (Kunal, Oct 28):**
  1. **Data tools** come from shipcube-data, mounted with a `data_` prefix: `data_list_cubes`, `data_describe_cube`, `data_query`, `data_search_values`, `data_propose_migration`. `connect_data` and `query` are removed. Warehouses are connected in the console (credentials never pass through an agent).
  2. **About 16 tools.** Merges: `get_status` + `get_logs` + `get_metrics` → `inspect_app` (`include: [status, logs, metrics]`); `grant_access` + `revoke_access` → `set_access` (`add`/`remove`, returns the full list); `get_operation` + `cancel_operation` → `operation` (`action: get | cancel`). App tools: `whoami`, `create_upload`, `estimate_load`, `deploy_app`, `operation`, `list_apps`, `inspect_app`, `set_caps`, `set_access`, `ai_config`, `delete_app`.
  3. **Muse in the contract.** The classifier produces the numbers in `estimate_load`; Muse Spark writes `reasoning`, `alternatives` and `recommended_caps`, and the result carries `explained_by`. If Muse is unavailable, rule-based reasoning is used instead. `data_propose_migration` can draft with Muse Spark.
  4. **Uploads unchanged:** signed URL first; `inline`, `git` and `image` as alternatives. Raise the `inline` limit enough for the demo app.
  5. **Approvals.** Agents act directly on ShipCube's project within default caps, and on reads, cap reductions and access removals. These need Kunal's approval: the first deploy to a linked GCP project, a cap raise above the workspace default, a `domain:` grant, and `delete_app`. They return `status: awaiting_approval` with an `approval_url`, approved in the console. Agents can never apply migrations; they only propose them. `delete_app` is a soft delete, restorable for 7 days.

- **Language (Kunal, Oct 27):** Python 3.12 for the MCP server, control-plane API, deployer, data layer, profiler, classifier and monitoring. Go only for the access proxy / AI gateway (`services/gateway`). Next.js/TypeScript for the console only. PR #1 is being ported to Python.

## How to coordinate

- Read this file and the other teammates' docs (on their branches or PRs) before changing an interface.
- If you need something from another teammate, write it in your PR description under **Needs from other workstreams** and tell Kunal. Don't edit their files.
- Teammates can't message each other directly. Kunal relays messages, or the lead session does.

## Shared decisions (already made)

- MCP-first: users build apps in their own coding agent and connect to ShipCube's MCP server. The web console handles accounts, access, caps and monitoring.
- Cloud: GCP. ShipCube's project runs on $300 of free credits. Users can link their own GCP project with Google sign-in and use their own credits.
- Identity: Google sign-in. Apps are private by default; only the owner and the people they allow can open them.
- Meta models are required: Muse Spark for load estimation and placement reasoning, Muse Code as the demo coding agent, and Muse as the default model in the AI gateway for user apps. BYOK keys work for OpenAI-compatible providers, OpenAI and Anthropic. Claude subscriptions are not supported, because Anthropic doesn't allow third-party products to use claude.ai login.
- Public repo. Never commit secrets, real customer data or anything from an employer's codebase.

## Deadline

Submissions close **Nov 4, 9:00 PM ET**. Research PRs should land by **Oct 29** so building can start on decisions.
