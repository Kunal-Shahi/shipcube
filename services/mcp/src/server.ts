// ShipCube MCP server: Streamable HTTP on /mcp, stateless (MCP 2026-07-28, with
// 2025-era clients served statelessly by the SDK's legacy mode).

import { createServer } from 'node:http';
import { createMcpHandler, McpServer, verifyBearerToken, bearerAuthChallengeResponse } from '@modelcontextprotocol/server';
import { toNodeHandler, hostHeaderValidation, originValidation } from '@modelcontextprotocol/node';
import { registerTools } from './tools.ts';

const PORT = Number(process.env.PORT ?? 8080);
const HOST = process.env.HOST ?? '127.0.0.1';
const ALLOWED_HOSTS = (process.env.ALLOWED_HOSTS ?? 'localhost,127.0.0.1,[::1]').split(',');
// Phase 1 auth: a personal access token minted in the web console. Locally, one
// dev token from the environment; unset means no auth (localhost only).
const DEV_TOKEN = process.env.SHIPCUBE_DEV_TOKEN;

if (!DEV_TOKEN && HOST !== '127.0.0.1' && HOST !== 'localhost') {
  throw new Error('Refusing to listen on a non-loopback host without SHIPCUBE_DEV_TOKEN');
}

const mcp = createMcpHandler(() => {
  const server = new McpServer(
    { name: 'shipcube', version: '0.1.0' },
    {
      instructions:
        'ShipCube deploys and runs internal apps on GCP. Typical flow: create_upload (or a git source) -> estimate_load -> ' +
        'deploy_app with the estimate_id -> grant_access. Long-running tools return an operation; call get_operation to keep waiting.',
    },
  );
  registerTools(server);
  return server;
});
const handle = toNodeHandler(mcp);

const verifier = {
  // Production: introspect sc_pat_* tokens against the API (hashed lookup, scopes,
  // expiry), or validate an OAuth access token's signature and audience.
  async verifyAccessToken(token: string) {
    if (token !== DEV_TOKEN) throw new Error('unknown token');
    return { token, clientId: 'dev', scopes: ['apps:write'], expiresAt: Math.floor(Date.now() / 1000) + 3600 };
  },
};

const validHost = hostHeaderValidation(ALLOWED_HOSTS);
const validOrigin = originValidation(ALLOWED_HOSTS);

createServer(async (req, res) => {
  if (req.url === '/healthz') return void res.writeHead(200).end('ok');
  if (!req.url?.startsWith('/mcp')) return void res.writeHead(404).end();
  if (!validHost(req, res) || !validOrigin(req, res)) return;
  if (DEV_TOKEN) {
    try {
      (req as any).auth = await verifyBearerToken(req.headers.authorization, { verifier });
    } catch (err) {
      const challenge = bearerAuthChallengeResponse(err);
      res.writeHead(challenge.status, Object.fromEntries(challenge.headers));
      return void res.end(await challenge.text());
    }
  }
  void handle(req, res);
}).listen(PORT, HOST, () => console.log(`shipcube mcp listening on http://${HOST}:${PORT}/mcp`));
