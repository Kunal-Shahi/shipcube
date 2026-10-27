# services/mcp

ShipCube's MCP server: the interface coding agents (Claude Code, Cursor, Muse Code) use. It is a thin, stateless adapter over the control-plane API (`services/api`). Each tool is one API call, plus polling for long-running operations.

- **Transport:** Streamable HTTP on `/mcp` (MCP 2026-07-28, `stateless_http=True`). Clients on the 2025 protocol versions connect too.
- **SDK:** official Python `mcp` 2.3.0 (`MCPServer`), Python 3.12. See `docs/research/networking.md` §1.6.
- **One service:** by default `/mcp` and the API's `/v1/*` run in one process, and tools reach the API in-process (same FastAPI routes, auth and validation). Set `SHIPCUBE_API_URL` to call a separate API over HTTP.
- **Auth (phase 1):** `Authorization: Bearer sc_pat_<id>_<secret>`. The MCP server checks that the API recognises the token and forwards it; the API checks scopes.
- **Tools (16):** 11 app tools in `shipcube_mcp/tools.py` (schemas from `services/api/shipcube_api/models.py`), plus 5 `data_*` tools from shipcube-data mounted in-process by `shipcube_mcp/data.py` (`apply_migration` is never exposed). Documented in `docs/research/networking.md` §2.
- **Approvals:** the first deploy to a linked GCP project, cap raises above the workspace default, `domain:` grants and `delete_app` return an operation with `status: awaiting_approval` and an `approval_url`. Only a console session can approve (`POST /v1/operations/{id}/approve`).
- **Data layer:** `uv sync --extra data` installs shipcube-data (pinned commit) and serves its Olist demo warehouse. Without it, a stub with the same tool names answers. The library's example migrations aren't packaged yet; point `SHIPCUBE_DATA_MIGRATIONS` at a checkout's `examples/olist/migrations`.

## Run locally

Needs [uv](https://docs.astral.sh/uv/). It installs Python 3.12 itself.

```bash
cd services/mcp && SHIPCUBE_DEV_TOKEN=sc_pat_devlocal_devonlyNotASecret0123456789abcdefXYZ SHIPCUBE_DEV_CONSOLE_TOKEN=sc_ses_devhuman_consoleOnlyNotASecret9876543210zyxw uv run python -m shipcube_mcp.server
```

Connect Claude Code:

```bash
claude mcp add --transport http shipcube http://localhost:8080/mcp --header "Authorization: Bearer sc_pat_devlocal_devonlyNotASecret0123456789abcdefXYZ"
```

Cursor (`.cursor/mcp.json`):

```json
{ "mcpServers": { "shipcube": { "url": "http://localhost:8080/mcp", "headers": { "Authorization": "Bearer ${env:SHIPCUBE_TOKEN}" } } } }
```

Smoke test. It lists the tools, then runs estimate → deploy → set_access (with a console approval) → caps → linked deploy (approval) → inspect → data tools → delete (awaiting approval). The console token stands in for a human approving:

```bash
SHIPCUBE_TOKEN=sc_pat_devlocal_devonlyNotASecret0123456789abcdefXYZ SHIPCUBE_CONSOLE_TOKEN=sc_ses_devhuman_consoleOnlyNotASecret9876543210zyxw uv run python scripts/smoke.py
```

Tests (the 16-tool contract, data tools in-process with a verified caller, merged tools and approvals):

```bash
uv run pytest
```

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `PORT` / `HOST` | `8080` / `127.0.0.1` | Listen address. A non-loopback host requires `ALLOWED_HOSTS`. |
| `ALLOWED_HOSTS` | `localhost:*,127.0.0.1:*` | Allowed `Host` values; `Origin` must match one of them (DNS-rebinding protection) |
| `SHIPCUBE_API_URL` | unset (in-process) | Call a separate control-plane API instead |
| `SHIPCUBE_DEV_TOKEN` | unset | Registers one local PAT with every scope. It must look like `sc_pat_<8 alphanumerics>_<32+ chars>`. |
| `SHIPCUBE_DEV_CONSOLE_TOKEN` | unset | Registers one local console session (`sc_ses_…`, with a different secret), which can approve |
| `SHIPCUBE_DATA_MIGRATIONS` | the library's `examples/olist/migrations` | Migrations for the demo warehouse; the stub is used if missing |
| `SHIPCUBE_DATA_DEMO_TENANTS` | `example.com=acme` | Google domain → demo tenant schema |
| `SHIPCUBE_MUSE_UNAVAILABLE` | unset | Forces the rule-based explainer for `estimate_load` |
| `SHIPCUBE_TOKEN_PEPPER` | dev value | HMAC pepper for token digests (Secret Manager in production) |
