// ShipCube control-plane API: skeleton with in-memory mock state.
//
// The HTTP contract in openapi.yaml is the stable part. This implementation is a
// placeholder that can be rewritten in Go or Python once the language trade-off
// research lands; the MCP server and web console only depend on the contract.

import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';
import { randomUUID } from 'node:crypto';
import { store, type Operation } from './store.ts';

const PORT = Number(process.env.PORT ?? 8081);
const HOST = process.env.HOST ?? '127.0.0.1';
// Dev-only shared token. In production the API trusts identity asserted by the
// MCP server / console (service-to-service ID token) plus the end user's PAT.
const DEV_TOKEN = process.env.SHIPCUBE_DEV_TOKEN;

type Handler = (ctx: Ctx) => Promise<Reply> | Reply;
type Ctx = { req: IncomingMessage; params: Record<string, string>; query: URLSearchParams; body: any };
type Reply = { status?: number; body?: unknown; headers?: Record<string, string> };

const routes: Array<{ method: string; pattern: RegExp; keys: string[]; handler: Handler }> = [];
function route(method: string, path: string, handler: Handler) {
  const keys: string[] = [];
  const pattern = new RegExp(
    '^' + path.replace(/:([a-z_]+)/g, (_, k) => (keys.push(k), '([^/]+)')).replace(/\./g, '\\.') + '$',
  );
  routes.push({ method, pattern, keys, handler });
}

const notFound = (what: string): Reply => ({ status: 404, body: { error: { code: 'not_found', message: `${what} not found` } } });
const bad = (message: string): Reply => ({ status: 400, body: { error: { code: 'invalid_argument', message } } });

// --- identity ---------------------------------------------------------------
route('GET', '/v1/me', () => ({ body: store.me }));

// --- uploads: signed URL for the agent to PUT a source tarball -------------------
route('POST', '/v1/uploads', ({ body }) => {
  const id = `upl_${randomUUID().slice(0, 8)}`;
  return {
    status: 201,
    body: {
      upload_id: id,
      method: 'PUT',
      // Production: a V4 signed URL on a GCS bucket with a 15-minute expiry.
      url: `https://storage.googleapis.com/shipcube-uploads/${id}.tgz?X-Goog-Signature=mock`,
      headers: { 'Content-Type': body?.content_type ?? 'application/gzip' },
      max_bytes: 100 * 1024 * 1024,
      expires_at: new Date(Date.now() + 15 * 60_000).toISOString(),
    },
  };
});

// --- long-running operations ----------------------------------------------------
route('POST', '/v1/estimates', ({ req, body }) => {
  if (!body?.source) return bad('source is required');
  return accepted(store.startOperation('estimate', body.app ?? null, body, idemKey(req)));
});

route('POST', '/v1/apps/:app/deployments', ({ req, params, body }) => {
  if (!body?.source) return bad('source is required');
  return accepted(store.startOperation('deploy', params.app, body, idemKey(req)));
});

route('GET', '/v1/operations/:id', ({ params }) => {
  const op = store.getOperation(params.id);
  return op ? { body: op } : notFound('operation');
});

route('POST', '/v1/operations/:id/cancel',({ params }) => {
  const op = store.cancelOperation(params.id);
  return op ? { body: op } : notFound('operation');
});

// --- apps -----------------------------------------------------------------------
route('GET', '/v1/apps', () => ({ body: { apps: [...store.apps.values()].map(summary) } }));
route('GET', '/v1/apps/:app', ({ params }) => {
  const app = store.apps.get(params.app);
  return app ? { body: app } : notFound('app');
});
route('DELETE', '/v1/apps/:app', ({ req, params }) => {
  if (!store.apps.has(params.app)) return notFound('app');
  return accepted(store.startOperation('delete', params.app, {}, idemKey(req)));
});

route('GET', '/v1/apps/:app/logs', ({ params, query }) => {
  if (!store.apps.has(params.app)) return notFound('app');
  const limit = Math.min(Number(query.get('limit') ?? 50), 500);
  return { body: { entries: store.logs(params.app, limit, query.get('severity') ?? undefined), next_page_token: null } };
});

route('GET', '/v1/apps/:app/metrics', ({ params, query }) => {
  if (!store.apps.has(params.app)) return notFound('app');
  return { body: store.metrics(params.app, query.get('window') ?? '1h') };
});

route('PATCH', '/v1/apps/:app/caps', ({ params, body }) => {
  const app = store.apps.get(params.app);
  if (!app) return notFound('app');
  app.caps = { ...app.caps, ...body };
  return { body: { caps: app.caps, enforced_by: store.enforcement(app.caps) } };
});

route('POST', '/v1/apps/:app/access', ({ params, body }) => {
  const app = store.apps.get(params.app);
  if (!app) return notFound('app');
  if (!body?.principal) return bad('principal is required');
  const binding = { principal: body.principal, role: body.role ?? 'viewer', granted_at: new Date().toISOString() };
  app.access = app.access.filter((b) => b.principal !== binding.principal).concat(binding);
  return { status: 201, body: { access: app.access } };
});

route('DELETE', '/v1/apps/:app/access/:principal', ({ params }) => {
  const app = store.apps.get(params.app);
  if (!app) return notFound('app');
  const principal = decodeURIComponent(params.principal);
  if (principal === `user:${store.me.email}`) return bad('cannot revoke the owner');
  app.access = app.access.filter((b) => b.principal !== principal);
  return { body: { access: app.access } };
});

route('PUT', '/v1/apps/:app/ai', ({ params, body }) => {
  const app = store.apps.get(params.app);
  if (!app) return notFound('app');
  app.ai = { provider: body?.provider ?? 'meta', model: body?.model ?? 'muse-spark', key_secret: body?.key_secret ?? null };
  return { body: { ai: app.ai, gateway_url: `https://ai.shipcube.dev/v1/apps/${app.name}` } };
});

// --- data -------------------------------------------------------------------------
route('POST', '/v1/datasources', ({ body }) => {
  if (!body?.name || !body?.kind) return bad('name and kind are required');
  return { status: 201, body: store.connectData(body) };
});

route('POST', '/v1/query', ({ body }) => {
  if (!body?.query) return bad('query is required');
  return { body: store.query(body) };
});

// --- plumbing ---------------------------------------------------------------------
function idemKey(req: IncomingMessage) {
  const v = req.headers['idempotency-key'];
  return typeof v === 'string' ? v : undefined;
}
function accepted(op: Operation): Reply {
  return { status: 202, body: op, headers: { Location: `/v1/operations/${op.id}`, 'Retry-After': String(op.poll_after_s) } };
}
function summary(a: any) {
  return { name: a.name, url: a.url, tier: a.tier, status: a.status, updated_at: a.updated_at };
}

async function readBody(req: IncomingMessage) {
  const chunks: Buffer[] = [];
  for await (const c of req) chunks.push(c as Buffer);
  if (!chunks.length) return undefined;
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}

function send(res: ServerResponse, r: Reply) {
  res.writeHead(r.status ?? 200, { 'Content-Type': 'application/json', ...r.headers });
  res.end(r.body === undefined ? '' : JSON.stringify(r.body));
}

createServer(async (req, res) => {
  const url = new URL(req.url ?? '/', 'http://localhost');
  if (url.pathname === '/healthz') return send(res, { body: { ok: true } });
  if (DEV_TOKEN && req.headers.authorization !== `Bearer ${DEV_TOKEN}`) {
    return send(res, { status: 401, body: { error: { code: 'unauthenticated', message: 'missing or invalid token' } } });
  }
  for (const r of routes) {
    if (r.method !== req.method) continue;
    const m = r.pattern.exec(url.pathname);
    if (!m) continue;
    try {
      const params = Object.fromEntries(r.keys.map((k, i) => [k, m[i + 1]]));
      return send(res, await r.handler({ req, params, query: url.searchParams, body: await readBody(req) }));
    } catch (err: any) {
      const status = err?.status ?? 500;
      return send(res, { status, body: { error: { code: status === 409 ? 'conflict' : 'internal', message: String(err?.message ?? err) } } });
    }
  }
  send(res, notFound('route'));
}).listen(PORT, HOST, () => console.log(`shipcube api listening on http://${HOST}:${PORT}`));
