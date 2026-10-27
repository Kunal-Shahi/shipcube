# Language benchmarks

Small, reproducible benchmarks behind [docs/research/language-tradeoffs.md](../../docs/research/language-tradeoffs.md). They answer three questions: how fast a minimal service starts and how much memory it uses, how much a proxy hop costs, and how long a Python service that loads sqlglot takes to import.

## What is measured

| Directory | Service |
|---|---|
| `go-http/` | Go stdlib `net/http`. With `UPSTREAM` set it becomes an `httputil.ReverseProxy` |
| `py-http/` | FastAPI on uvicorn (uvloop, httptools). With `UPSTREAM` set it proxies with `httpx.AsyncClient` |
| `go-mcp/` | MCP server on the official Go SDK, streamable HTTP, stateless, one tool |
| `py-mcp/` | MCP server on the official Python SDK (`mcp` 2.x `MCPServer`), streamable HTTP, stateless, one tool |
| `ts-mcp/` | MCP server on the official TypeScript SDK, streamable HTTP, stateless, one tool |
| `sqlglot/` | Interpreter import time of sqlglot and FastAPI, and per-query parse/transpile/optimize cost |

`bench.py` (stdlib only) drives the servers:

- **Cold start:** start the process, poll `GET /healthz` every 1 ms until it returns 200 ("ready"), then send one real request (`/echo`, or an MCP `tools/call`) ("first response"). RSS is read with `ps` 200 ms later. 20 runs per target, median and p95.
- **Throughput:** `oha` for 5 s at 64 connections after a 5 s warm-up (`DURATION=5s`), against `/echo` or MCP `tools/call`. Go is also run with `GOMAXPROCS=1` to approximate a 1 vCPU Cloud Run instance; Python runs as one uvicorn worker, which uses about one core anyway.
- **Proxy hop:** a Go upstream serving `/echo`; `oha` hits it directly, through the Go reverse proxy, and through the Python proxy.

## Caveats

- Run on a laptop (Apple M5, 10 cores, macOS), natively, not in Docker: Docker was not installed. Absolute numbers will differ on Cloud Run. Ratios between languages are the useful output.
- The load generator runs on the same machine and competes for CPU, so throughput is a lower bound for everything, and more so for Go.
- Process start-up on a warm laptop excludes Cloud Run's image pull and sandbox start, which add the same fixed cost to every language. Image size matters there, so it is reported separately.
- Python `.pyc` files were already compiled (warm runs). A fresh container without precompiled bytecode starts slower; compile at build time (`python -m compileall` or `UV_COMPILE_BYTECODE=1`).

## Reproduce

```bash
brew install go uv oha
(cd go-http && go build -trimpath -ldflags='-s -w' -o bin/gohttp .)
(cd go-mcp && go build -trimpath -ldflags='-s -w' -o bin/gomcp .)
(cd py-http && uv sync --python 3.12)
(cd py-mcp && uv sync --python 3.12)
(cd sqlglot && uv sync --python 3.12)
(cd ts-mcp && npm install)
DURATION=5s python3 bench.py all
(cd sqlglot && .venv/bin/python bench_import.py)
```

Raw results are in `results/*.json`.
