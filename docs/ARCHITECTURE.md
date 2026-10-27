# Architecture

This is a clean-room design: no code from any employer's codebase is reused.

## 0. Product shape (MCP-first)

ShipCube is an **infrastructure facilitator**, not an app builder. Users build apps in their own coding agent (Claude Code, Cursor, Muse Code, ...). The agent talks to ShipCube's **MCP server**, which is the main interface. The web console is for signing up, linking accounts, managing access and caps, and monitoring.

**MCP tools (first draft; the backend workstream owns the final list):**

| Tool | Purpose |
|---|---|
| `estimate_load` | Profile code or a container and return the workload class, sizing, monthly cost and reasoning |
| `deploy_app` | Build and deploy to the chosen tier on ShipCube's or the user's GCP project |
| `set_caps` | Spend, request-rate, concurrency and resource caps per app |
| `grant_access` / `revoke_access` | Allow users, Google groups or domains to open an app |
| `connect_data` / `query` | Register a data source and query it through the semantic layer |
| `get_status` / `get_logs` / `get_metrics` | Health, logs, monitoring |
| `ai_config` | Choose the model provider for the app (Muse default, BYOK) |

**Where Meta's models run:**
- Muse Spark powers load estimation (it reads the code alongside the classifier's prediction), cap recommendations and placement reasoning.
- Muse Code is the demo coding agent, connected through our MCP.
- Apps that need a model use the AI gateway, with Muse as the default provider.

**GCP:** ShipCube's own project runs on $300 of free credits. Users can link their own GCP project with Google sign-in, and ShipCube then deploys into that project and bills to it. The infra and security workstream decides how (service-account impersonation, workload identity federation, or OAuth scopes).

**Access:** every app is private by default. Only the owner and the people they allow can open it. Identity comes from Google sign-in.

## 1. Semantic DSL (minimal)

The design aim is very few required keys. Everything else is inferred or optional.

| Object | Required | Optional |
|---|---|---|
| Cube | `name`, `table` or `sql` | `joins`, `description`, `meta`, `row_filter` |
| Dimension | `name`, `sql` (shorthand: `name: column`) | `type` (inferred from the warehouse schema), `primary_key`, `description`, `meta` |
| Measure | `name`, `sql` (aggregate expression) | `format`, `description`, `meta` |
| Join | target cube + `on` expression | `relationship` (inferred from primary keys), `type` (`left` by default) |

```yaml
cubes:
  orders:
    table: orders
    joins:
      customers: "{orders.customer_id} = {customers.id}"
    dimensions:
      region: region
      created_at: {sql: created_at, type: time}
    measures:
      revenue: sum(amount)
      orders: count(*)
      aov: "{revenue} / nullif({orders}, 0)"   # derived measures are SQL, not pandas
```

The DSL has no metric-kind enum. Share-of-total, rank, lag and running totals are generic **query operators** (see L1 below).

**Validation at load time:** reference resolution, dependency-cycle detection, type inference, and a schema fingerprint used for cache invalidation.

## 2. SQL engine: levels

The compiler builds a relational IR on sqlglot and emits SQL for each dialect. Every query records the level it uses, and the UI shows it.

| Level | Covers | Guarantee |
|---|---|---|
| **L0 Typed query** | measures, dimensions, filter tree, time dimension and granularity, order, limit | Fully validated; the LLM never writes SQL |
| **L1 Operators** | `share(m) over d`, `rank`, `lag`/`lead`, `running`, `compare(period)` | Typed; compiled to window functions |
| **L2 Stages** | query-over-query (CTE), UNION/INTERSECT/EXCEPT, aliased self-joins, subquery filters, grouping sets | Typed IR, composable |
| **L3 Raw SQL** | anything else | Must parse with sqlglot; read-only (SELECT only); row filters injected; labelled **unverified** |

**Query shape (L0 + L1):**

```json
{
  "measures": ["orders.revenue", {"op": "share", "of": "orders.revenue", "over": ["orders.region"]}],
  "dimensions": ["orders.region"],
  "time": {"dimension": "orders.created_at", "granularity": "month", "range": "last 6 months"},
  "filters": {"and": [{"member": "customers.segment", "op": "in", "values": ["smb"]}]},
  "order": [["orders.revenue", "desc"]],
  "limit": 100
}
```

**Context and security:** `context` is a free-form dictionary of variables available to cube SQL. `row_filter` is a single hook that injects the caller's row-level restrictions as filters, so there is no fixed list of JWT claims.

**Routing:** L0/L1 metric queries go through a result cache or pre-aggregations. Heavy L2/L3 or row-level queries go straight to the warehouse.

## 3. Workload profiling and classification

- **Profiling:** the script runs in an isolated sandbox on a sample dataset. We sample CPU, RSS memory, I/O bytes, wall time, number of queries and output size.
- **Training data:**
  - Google Borg cluster traces (2019)
  - Alibaba cluster-trace-v2018 and the microservices traces
  - Azure Public Dataset (VM traces, Functions traces)
  - Bitbrains GWA-T-12
- **Features:** resource levels and variance, burstiness, how often it is invoked, duration, and the memory/CPU ratio.
- **Model:** a gradient-boosted classifier that outputs a workload class and a confidence score. It is evaluated on held-out traces.
- **Classes:** batch/cron, bursty interactive, steady service, memory-heavy, I/O-bound, compute-heavy.

## 4. Deployment tiers

| Tier | For | Target |
|---|---|---|
| T0 Snapshot | reports that rarely change | precomputed result plus static page, refreshed by cron |
| T1 Serverless interactive | bursty dashboards and trackers | Cloud Run (scale to zero) |
| T2 Scheduled batch | daily jobs and scoring | Cloud Run jobs + Cloud Scheduler |
| T3 Always-on API | apps other systems call | Cloud Run (min instances) or GKE Autopilot |
| T4 Heavy compute | ML or GPU work | Cloud Run GPU or GKE Autopilot GPU |

Placement outputs a tier, resource requests, a monthly cost estimate and its reasoning. The user can always override it. T0–T2 run on Cloud Run services and jobs. T3 and T4 run on Cloud Run (minimum instances, GPU) first; GKE Autopilot is added only for workloads Cloud Run cannot serve (see Kubernetes below).

## 5. Continuous monitoring

Every deployed app gets:

- **Health:** p50/p95 latency, error rate, memory and CPU against the requested resources, cold starts.
- **Data:** freshness (time since source data last updated), row-count anomalies, schema changes in source tables.
- **Cost:** compute, warehouse queries, and LLM tokens per app and per customer set.
- **Output drift:** changes in output distributions between runs and across customer sets.
- **Alerts:** thresholds per app, sent to email or webhook.
- **Re-placement loop:** live telemetry is periodically fed back into the classifier. When an app no longer fits its tier (for example, a T1 dashboard that is now busy all the time), ShipCube suggests moving it, with a cost comparison.

## 6. Model providers

The provider layer has one interface and several backends. It is configured per workspace, and each app can override it.

| Mode | How it works | Notes |
|---|---|---|
| **Meta (default)** | Meta Model API (Muse Spark, Muse Code) | The platform's own pipeline always uses this |
| **BYOK, OpenAI-compatible** | user's base URL and key | Fireworks, Baseten, Together, OpenRouter, local Ollama/vLLM |
| **BYOK, OpenAI** | user's OpenAI API key | |
| **BYOK, Anthropic** | user's Anthropic API key | |
| **Sign in with ChatGPT** | user's ChatGPT plan allowance | Roadmap only: requires acceptance into OpenAI's partner program |

**Claude subscriptions are not supported.** Anthropic does not allow third-party products to offer claude.ai login or use subscription rate limits without prior approval. Claude is supported through API keys only.

**Key handling:** keys are encrypted at rest and scoped to a workspace. They are never logged or sent to the browser. Per-app token budgets and usage appear on the monitoring page.

## 7. UI principles

- AI is a step in the pipeline, not a chatbot. Every AI decision produces something reviewable: a diff, the compiled SQL, a placement decision with reasoning. Each can be overridden.
- Four core screens: Import → Review plan → Deploy & runs → Monitoring and gallery. No landing page.
- One neutral palette with one accent colour. Inter or Geist type, dense 13–14px layouts, tabular numbers. Real loading, empty and error states. Logs stream while jobs run.
- No gradients, glows, emoji or "AI-powered" badges.

## 8. Evidence (evals)

- **SQL compiler:** about 50 queries checked against hand-written SQL on DuckDB, measuring result equality per level.
- **Classifier:** accuracy and F1 on held-out traces, plus a confusion matrix.
- **End to end:** time from notebook to deployed app, and cost per deploy.
