# services/mcp

ShipCube's MCP server: the interface coding agents (Claude Code, Cursor, Muse Code) use. It is a thin, stateless adapter over the control-plane API (`services/api`). Each tool is one API call, plus polling for long-running operations.

- Transport: Streamable HTTP on `/mcp` (MCP 2026-07-28). The SDK also serves clients on the 2025 protocol versions statelessly.
- SDK: [`@modelcontextprotocol/server`](https://www.npmjs.com/package/@modelcontextprotocol/server) v2 (TypeScript). See `docs/research/networking.md` §1.6 for why, and for how to swap it out.
- Auth (phase 1): `Authorization: Bearer <personal access token>`. Locally this is a single dev token set in `SHIPCUBE_DEV_TOKEN`.
- Tools and schemas: `src/tools.ts`, documented in `docs/research/networking.md` §2.

## Run locally

```bash
# terminal 1: the mock control-plane API on :8081
cd services/api && npm install && npm start
```

```bash
# terminal 2: the MCP server on :8080
cd services/mcp && npm install && SHIPCUBE_DEV_TOKEN=dev-local-token npm start
```

Connect Claude Code:

```bash
claude mcp add --transport http shipcube http://localhost:8080/mcp --header "Authorization: Bearer dev-local-token"
```

Cursor (`.cursor/mcp.json`):

```json
{ "mcpServers": { "shipcube": { "url": "http://localhost:8080/mcp", "headers": { "Authorization": "Bearer ${env:SHIPCUBE_TOKEN}" } } } }
```

Smoke test (lists the tools, then runs estimate → deploy → grant → caps → status):

```bash
SHIPCUBE_TOKEN=dev-local-token npm run smoke
```

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `PORT` / `HOST` | `8080` / `127.0.0.1` | Listen address. The server refuses to bind a non-loopback host without a token. |
| `SHIPCUBE_API_URL` | `http://127.0.0.1:8081` | Control-plane API base URL |
| `SHIPCUBE_DEV_TOKEN` | unset | Bearer token required on `/mcp`. Leaving it unset disables auth (localhost only). |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1,[::1]` | Allowed `Host` and `Origin` hostnames (DNS-rebinding protection) |
