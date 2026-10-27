# services/mcp

ShipCube's MCP server: the interface coding agents (Claude Code, Cursor, Muse Code) use. It is a thin, stateless adapter over the control-plane API (`services/api`). Each tool is one API call, plus polling for long-running operations.

- **Transport:** Streamable HTTP on `/mcp` (MCP 2026-07-28, `stateless_http=True`). Clients on the 2025 protocol versions connect too.
- **SDK:** official Python `mcp` 2.3.0 (`MCPServer`), Python 3.12. See `docs/research/networking.md` §1.6.
- **One service:** by default `/mcp` and the API's `/v1/*` run in one process, and tools reach the API in-process (same FastAPI routes, auth and validation). Set `SHIPCUBE_API_URL` to call a separate API over HTTP.
- **Auth (phase 1):** `Authorization: Bearer sc_pat_<id>_<secret>`. The MCP server checks that the API recognises the token and forwards it; the API checks scopes.
- **Tools and schemas:** `shipcube_mcp/tools.py`, using the Pydantic models in `services/api/shipcube_api/models.py`. Documented in `docs/research/networking.md` §2.
- **Extra tool packages** (the data layer): `shipcube_mcp/plugins.py`. A package exposes `register(host)` through the `shipcube.mcp_tools` entry point or `SHIPCUBE_MCP_PLUGINS=module:function`, and registers tools with `host.tool(...)`. A plugin tool replaces a built-in tool with the same name. Inside a tool, `await host.caller("data:read")` returns the verified workspace.

## Run locally

Needs [uv](https://docs.astral.sh/uv/). It installs Python 3.12 itself.

```bash
cd services/mcp && SHIPCUBE_DEV_TOKEN=sc_pat_devlocal_devonlyNotASecret0123456789abcdefXYZ uv run python -m shipcube_mcp.server
```

Connect Claude Code:

```bash
claude mcp add --transport http shipcube http://localhost:8080/mcp --header "Authorization: Bearer sc_pat_devlocal_devonlyNotASecret0123456789abcdefXYZ"
```

Cursor (`.cursor/mcp.json`):

```json
{ "mcpServers": { "shipcube": { "url": "http://localhost:8080/mcp", "headers": { "Authorization": "Bearer ${env:SHIPCUBE_TOKEN}" } } } }
```

Smoke test. It lists the tools, then runs estimate → deploy → grant → caps → status → query:

```bash
SHIPCUBE_TOKEN=sc_pat_devlocal_devonlyNotASecret0123456789abcdefXYZ uv run python scripts/smoke.py
```

Tests (the tool contract, plus mounting a data-layer-style plugin in-process):

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
| `SHIPCUBE_TOKEN_PEPPER` | dev value | HMAC pepper for token digests (Secret Manager in production) |
| `SHIPCUBE_MCP_PLUGINS` | unset | Extra tool packages, `module:function`, comma-separated |
