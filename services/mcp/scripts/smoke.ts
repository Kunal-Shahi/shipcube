// Smoke test: list the tools and run the core flow against a running server.
//   SHIPCUBE_MCP_URL=http://localhost:8080/mcp SHIPCUBE_TOKEN=... npm run smoke

import { Client, StreamableHTTPClientTransport } from '@modelcontextprotocol/client';

const url = new URL(process.env.SHIPCUBE_MCP_URL ?? 'http://localhost:8080/mcp');
const headers: Record<string, string> = process.env.SHIPCUBE_TOKEN ? { Authorization: `Bearer ${process.env.SHIPCUBE_TOKEN}` } : {};

const client = new Client({ name: 'shipcube-smoke', version: '0.1.0' });
await client.connect(new StreamableHTTPClientTransport(url, { requestInit: { headers } }));

const { tools } = await client.listTools();
console.log(`tools (${tools.length}): ${tools.map((t) => t.name).join(', ')}\n`);

async function call(name: string, args: Record<string, unknown> = {}) {
  const res = await client.callTool(
    { name, arguments: args },
    { onprogress: (p) => console.log(`  progress ${p.progress}/${p.total ?? '?'}: ${p.message ?? ''}`) },
  );
  console.log(`> ${name} ${JSON.stringify(args)}`);
  console.log(res.isError ? `  ERROR ${JSON.stringify(res.content)}` : `  ${JSON.stringify(res.structuredContent).slice(0, 400)}`);
  return res.structuredContent as any;
}

await call('whoami');
const est = await call('estimate_load', { source: { kind: 'git', url: 'https://github.com/example/olist-tracker' }, wait_s: 15 });
const dep = await call('deploy_app', {
  app: 'olist-tracker',
  source: { kind: 'git', url: 'https://github.com/example/olist-tracker' },
  estimate_id: est.operation.result?.estimate_id,
  idempotency_key: 'smoke-1',
  wait_s: 3,
});
if (!dep.operation.done) await call('get_operation', { id: dep.operation.id, wait_s: 20 });
await call('grant_access', { app: 'olist-tracker', principal: 'user:teammate@example.com' });
await call('set_caps', { app: 'olist-tracker', caps: { max_instances: 1, requests_per_minute: 60 } });
await call('get_status', { app: 'olist-tracker' });
await client.close();
