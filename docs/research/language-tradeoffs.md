# Language trade-offs: Go vs Python (vs TypeScript)

Status: recommendation, Oct 27 2026. Owner: language trade-offs workstream. Benchmarks: [`research/bench/`](../../research/bench/README.md).

## TL;DR

- **Python 3.12 for almost everything**: MCP server, control-plane API, deployer, semantic compiler, profiler orchestration, classifier, monitoring workers. These share code (the MCP tools call the compiler and the estimator in-process), and two of them, the compiler and the classifier, only have mature libraries in Python.
- **Go for one component: the access proxy / AI gateway.** It is the only component in the request path of *every* user-app request. In our measurements a Go reverse proxy handles **~40–49x** the throughput of a Python one at **a quarter of the memory**, and it is about 300 lines of stdlib code.
- **Not TypeScript.** Its MCP SDK is good and it starts faster than Python, but it has no sqlglot, no ML stack, and it adds a third runtime next to the Next.js console.
- **Split rule:** add a second language for a component only when all three hold: (1) it sits in the hot path of user traffic or must hold many concurrent connections, (2) it shares no domain code with the Python core, and (3) it can be built on its language's standard library in one day or less. Today only the gateway passes.

## Benchmarks (method in [`research/bench/README.md`](../../research/bench/README.md))

Apple M5 laptop, 10 cores, macOS, native processes (Docker was not installed). Go 1.27.3, Python 3.12.16, Node 22.23, oha 1.16. The load generator shares the machine, so treat ratios as the result, not absolute numbers.

### Cold start and memory (20 runs, median; p95 in brackets)

| Service | Process start → first response | Idle RSS |
|---|---:|---:|
| Go `net/http` | **15 ms** [18] | **11 MB** |
| Python FastAPI + uvicorn | 472 ms [1,232] | 59 MB |
| MCP, official Go SDK v1.8.0 | **13 ms** [17] | **14 MB** |
| MCP, official Python SDK `mcp` 2.3.0 | 634 ms [650] | 76 MB |
| MCP, official TS SDK 1.32.2 (Node 22) | 147 ms [152] | 72 MB |

Python pays ~0.5–0.6 s more per cold start. On Cloud Run this is added on top of the platform's own instance start, which is the same for every language. Google's guidance is to cut dependencies, initialise lazily, enable startup CPU boost, or keep one minimum instance ([Cloud Run general tips](https://docs.cloud.google.com/run/docs/tips/general)). Startup CPU boost cut startup by up to 30% for Node.js; Google published no Go or Python figures ([announcement](https://cloud.google.com/blog/products/serverless/announcing-startup-cpu-boost-for-cloud-run--cloud-functions)). I found no published Go vs Python cold-start benchmark for Cloud Run. On AWS Lambda, Go cold starts are about 2x faster than Python ([tecRacer 2023](https://www.tecracer.com/blog/2023/07/custom-runtime-on-amazon-linux-2-go-outperforms-node-3x-and-python-2x-with-aws-lambda-cold-start-time.html)), which is consistent with our numbers once platform overhead is included.

### Throughput, single instance (oha, 64 connections, 5 s after a 5 s warm-up)

| Service | req/s | p50 | p99 | RSS after load |
|---|---:|---:|---:|---:|
| Go `net/http` `/echo` | 172,566 | 0.26 ms | 2.1 ms | 23 MB |
| Go `net/http`, GOMAXPROCS=1 | 123,767 | 0.51 ms | 1.0 ms | 17 MB |
| FastAPI + uvicorn `/echo` (1 worker) | 29,813 | 2.1 ms | 4.1 ms | 60 MB |
| MCP `tools/call`, Go SDK | 11,646 | 4.9 ms | 15.9 ms | 32 MB |
| MCP `tools/call`, Go SDK, GOMAXPROCS=1 | 10,456 | 6.2 ms | 8.7 ms | 20 MB |
| MCP `tools/call`, Python SDK | 3,588 | 16.3 ms | 28.6 ms | 81 MB |
| MCP `tools/call`, TS SDK | 12,760 | 5.0 ms | 9.4 ms | 178 MB |

The Python MCP server does 3,588 tool calls per second on one core. Coding agents make a few calls per minute, so this is three to four orders of magnitude more than the MCP server needs. **Throughput does not decide the MCP server.**

### Proxy hop (Go upstream serving `/echo`)

| Path | req/s | p50 | p99 | Proxy RSS |
|---|---:|---:|---:|---:|
| Direct to upstream | 177,440 | 0.24 ms | 2.3 ms | — |
| Go `httputil.ReverseProxy` | **46,579** | 1.2 ms | 3.7 ms | 28 MB |
| Go `httputil.ReverseProxy`, GOMAXPROCS=1 | 38,136 | 1.6 ms | 2.5 ms | 18 MB |
| Python FastAPI + `httpx.AsyncClient` (1 worker) | 957 | 50.5 ms | 270.8 ms | 70 MB |

The Python proxy is particularly slow because `httpx` adds a lot of per-request overhead. `aiohttp` would probably narrow the gap to around 5–10x, but I did not measure it. Even at 10x, every user-app request would cost more CPU and more latency in Python. Vendor numbers agree with ours: Bifrost (Go) reports 0.99 ms of gateway overhead against LiteLLM's (Python) 40 ms, and 100% vs 88.78% success at 500 RPS on a t3.medium ([Bifrost benchmarks](https://www.getmaxim.ai/bifrost/resources/benchmarks); vendor-published, not independently reproduced).

### sqlglot import cost (Python 3.12, sqlglot 30.25.0, 20 fresh interpreters, median)

| Fresh process | Wall time |
|---|---:|
| Bare interpreter | 10 ms |
| `import sqlglot` | 258 ms |
| `import sqlglot` + optimizer | 259 ms |
| `import fastapi` | 372 ms |
| `import fastapi, sqlglot, sqlglot.optimizer` (the service) | 506 ms |

Per-query cost, warm, on an L1-shaped query (CTE, `date_trunc`, `share` window function and `lag`): parse 0.41 ms, BigQuery generation 0.33 ms, full `optimize()` with schema 5.1 ms, and 33 ms for the first parse and transpile in a process. Compile cost is negligible next to warehouse latency. The ~0.25 s import is a cold-start cost, paid once per instance. `sqlglot[c]` (mypyc-compiled) parses about 3–5x faster ([Fivetran](https://www.fivetran.com/blog/how-we-accelerated-transpilation-by-compiling-sqlglot-with-mypyc)), but it may break custom dialect subclasses, so stay on pure Python unless profiling says otherwise.

### Image size (linux/amd64, compressed)

| Image | Base | Deps / binary | Total |
|---|---:|---:|---:|
| Go service on `distroless/static` | 0.7 MB | 3.0–3.6 MB (gzipped static binary) | **~4 MB** |
| Python service on `python:3.12-slim` | 43.5 MB | 3.9–8.2 MB (site-packages: sqlglot / FastAPI / mcp) | **~50–55 MB** |
| Python service on `distroless/python3` | 19.7 MB | same | ~25–30 MB |
| Node service on `node:22-slim` | 79.9 MB | 4.1 MB | ~84 MB |

Cloud Run streams images, so image size barely affects startup ([tips](https://docs.cloud.google.com/run/docs/tips/general)). It only matters for Artifact Registry storage: 0.5 GB is free, then $0.10/GB-month ([free tier](https://docs.cloud.google.com/free/docs/free-cloud-features), [pricing](https://cloud.google.com/artifact-registry/pricing)). Keep about five Python images and prune old tags.

## Library maturity (checked Oct 27 2026)

| Area | Go | Python | TypeScript |
|---|---|---|---|
| **MCP SDK** | Official, Tier 1, `go-sdk` v1.8.0 (Sep 2026). Spec 2026-07-28 over HTTP requires `Stateless: true`; stateful servers negotiate down to 2025-11-25. OAuth bearer validation and scope filtering ([releases](https://github.com/modelcontextprotocol/go-sdk/releases)) | Official, Tier 1, `mcp` 2.3.0 (Oct 2026). v2 renamed FastMCP to `MCPServer` and breaks v1 code; most online examples are still v1 ([releases](https://github.com/modelcontextprotocol/python-sdk/releases)). Streamable HTTP, OAuth (RFC 9207, client credentials) | Official, Tier 1, v2.4.0, split into `@modelcontextprotocol/server` and other packages; 1.x is still maintained ([releases](https://github.com/modelcontextprotocol/typescript-sdk/releases)) |
| **Cloud Run API** | `cloud.google.com/go/run/apiv2` stable v1.x; Services, Jobs, Executions ([pkg.go.dev](https://pkg.go.dev/cloud.google.com/go/run/apiv2)) | `google-cloud-run` 0.16.2, marked **Beta** but generated from the same v2 API ([PyPI](https://pypi.org/project/google-cloud-run/)) | `@google-cloud/run` 4.2.0 |
| **GKE / Cloud Build** | `container/apiv1` stable; client-go for workloads | `google-cloud-container` and `google-cloud-build` Production/Stable ([PyPI](https://pypi.org/project/google-cloud-build/)); `kubernetes` client | Available |
| **SQL compiler** | No equivalent. `sqlglot-go` covers only base, MySQL and Postgres; cross-dialect output is unverified and most of the optimizer is missing ([pkg](https://pkg.go.dev/github.com/ridi-oss/sqlglot-go)). `polyglot` has a Go SDK through PureGo FFI and a native library, with no optimizer ([repo](https://github.com/tobilg/polyglot)). `pg_query_go` is Postgres-only and needs cgo. The CockroachDB parser was archived in Oct 2026 | **sqlglot** 30.25.0: 32 dialects including BigQuery and DuckDB, optimizer, qualify, lineage ([PyPI](https://pypi.org/project/sqlglot/)) | `node-sql-parser`: no DuckDB, no transpile ([repo](https://github.com/taozhi8833998/node-sql-parser)); `@polyglot-sql/sdk` (WASM) |
| **ML** | Inference only. `leaves` reads LightGBM and XGBoost models but was last pushed in Jul 2024 ([repo](https://github.com/dmitryikh/leaves)); `onnxruntime_go` needs cgo | scikit-learn, LightGBM, XGBoost, pandas: first class | Weak |
| **Proxy** | `httputil.ReverseProxy` in stdlib, with `Rewrite` (use it instead of the deprecated `Director`) and streaming flush ([docs](https://pkg.go.dev/net/http/httputil#ReverseProxy)) | httpx/aiohttp; LiteLLM as prior art | Portkey as prior art |

Python 3.14's free-threaded build is officially supported but is not the default and is about 5–10% slower single-threaded ([What's new](https://docs.python.org/3.14/whatsnew/3.14.html)). It doesn't change the analysis for an 8-day build, so stay on 3.12 or 3.13.

## Per-component scores

Scale 1–5, higher is better. Weights: ship-in-8-days ×3, libraries ×2, runtime ×1, cost ×1, ops simplicity ×1, for a maximum of 40. "Ops" assumes the rest of the backend is Python.

| Component | Go | Python | TS | Pick | Why |
|---|---:|---:|---:|---|---|
| **MCP server** | 27 | **35** | 27 | Python | The tools call the compiler (sqlglot) and the estimator in-process; a Go server would need an RPC hop to Python for both. All three SDKs are Tier 1. Fix the 0.6 s cold start with `min-instances=1` (see Cost) |
| **Control-plane API** | 30 | **36** | 27 | Python | CRUD plus calls into the compiler, placement and deployer. FastAPI + Pydantic share one schema with the MCP tools. It can live in the same service as the MCP server |
| **Deployer + GCP client** | 33 | **34** | 26 | Python | Go's client is labelled stable and Python's beta, but both are generated from the same v2 API. Deploy time is dominated by Cloud Build and revision rollout (tens of seconds), not by the language. Python keeps one codebase |
| **Access proxy / AI gateway** | **35** | 24 | 28 | **Go** | It is on every user-app request, and token streaming means holding long-lived connections. Go: ~40–49x the throughput, a quarter of the RSS and 13 ms cold start, in stdlib only. It doesn't need the compiler, sqlglot or ML |
| **Semantic compiler** | 13 | **39** | 18 | Python | sqlglot has no real Go or TS equivalent (see above). Writing a Go dialect layer would take the whole 8 days |
| **Profiler + sandbox** | 28 | **34** | 22 | Python | Isolation comes from the platform (a Cloud Run job on gen2 microVMs, or gVisor), not from the language. In-process Python sandboxes are bypassable ([overview](https://www.morphllm.com/python-sandbox)). The profiled code is usually Python, so a `psutil` sampler inside the job is the simplest option |
| **Classifier training + serving** | 12 | **39** | 14 | Python | LightGBM/sklearn for training. Serve in-process from the API, since one prediction is under 1 ms; Go inference libraries are stale |
| **Monitoring workers** | 31 | **35** | 26 | Python | Cloud Run jobs on Cloud Scheduler: cold start doesn't matter in batch, and drift checks use pandas and the classifier |

## Cost on the $300 credit

Cloud Run Tier 1 instance-based billing is $0.000018 per vCPU-s and $0.000002 per GiB-s, with 240k vCPU-s and 450k GiB-s free per month ([pricing](https://cloud.google.com/run/pricing); spot-check, since the page is JS-rendered). The free trial is $300 over 90 days ([terms](https://docs.cloud.google.com/free/docs/free-cloud-features)).

- **One always-warm Python MCP + API instance** (1 vCPU, 512 MiB, `min-instances=1`) costs about $1.60/day before the free tier, so about **$13 for the 8 days**, and the free tier covers most of it. This removes Python's cold-start penalty for agents.
- **Go gateway** on 0.5 vCPU / 256 MiB, scaling to zero, costs cents. A Python gateway would need roughly 10x the instances under the same load, but at demo traffic levels that is still under $5.
- **The language changes cost by less than $20 on a $300 budget.** It does not decide anything. Warehouse queries, Muse/LLM tokens and GKE Autopilot (keep T3/T4 as placement decisions only, as planned) are what move cost.

## Recommendation

| Component | Language | Deploy shape |
|---|---|---|
| MCP server + control-plane API (one service at first) | Python 3.12, `mcp` 2.x `MCPServer` + FastAPI | Cloud Run, `min-instances=1`, startup CPU boost on, bytecode precompiled |
| Deployer, placement, classifier serving | Python, imported as packages into the API | — |
| Semantic compiler | Python, sqlglot (pure, not `[c]`) | Library |
| Profiler | Python sampler in a sandboxed Cloud Run job | Cloud Run job (gen2) |
| Classifier training | Python, LightGBM / scikit-learn | Offline; ship the model file in the image |
| Monitoring workers | Python | Cloud Run jobs + Cloud Scheduler |
| **Access proxy / AI gateway** | **Go**, stdlib `httputil.ReverseProxy` (`Rewrite`, `FlushInterval: -1` for streaming) | Cloud Run, distroless/static, ~4 MB image |
| Web console | TypeScript (owned by frontend) | — |

**When to split vs stay in one language.** Default to Python. Move a component to Go only if:

1. **It is in the hot path.** It handles user-app traffic on every request or holds many concurrent or streaming connections, so that per-request overhead or cold start shows up in user-facing latency (in our benchmark the proxy hop is 1.2 ms in Go vs 50 ms in Python).
2. **It is self-contained.** It imports nothing from the Python core (compiler, classifier, placement). It talks to the core over HTTP or by reading shared Postgres tables.
3. **It is small.** It can be built on Go's standard library plus at most one dependency in one day or less.

If any of these fails, stay in Python and make it faster first: `min-instances`, lazy imports, more instances. Re-check the MCP server only if it has to stream long-lived sessions for many agents at once; the Go SDK is ready if that happens.

**Fallback if the schedule slips:** ship the gateway in Python (FastAPI + `httpx`, or LiteLLM for the AI part) and accept the latency for the demo. The Go gateway adds value but the demo doesn't depend on it.

## Proposed change for shared docs (not edited here)

`docs/ARCHITECTURE.md`: add one line under the repo layout: "Backend is Python 3.12 except `services/gateway/` (Go). Rationale: docs/research/language-tradeoffs.md." Also note that the Python MCP SDK v2 renamed FastMCP to `MCPServer`, so code examples should use `from mcp.server.mcpserver import MCPServer`.
