// ShipCube MCP tools. Each tool maps to one control-plane API call; schemas here
// are the agent-facing contract documented in docs/research/networking.md §2.

import { createHash } from 'node:crypto';
import type { McpServer } from '@modelcontextprotocol/server';
import * as z from 'zod/v4';
import { api, ApiError } from './api.ts';

// --- shared schemas -----------------------------------------------------------------

const AppName = z
  .string()
  .regex(/^[a-z][a-z0-9-]{1,38}[a-z0-9]$/)
  .describe('App name: lowercase letters, digits and hyphens. Becomes <name>.apps.shipcube.dev.');

const Source = z
  .discriminatedUnion('kind', [
    z.object({ kind: z.literal('upload'), upload_id: z.string().describe('From create_upload, after the tarball was PUT') }),
    z.object({ kind: z.literal('git'), url: z.string().url(), ref: z.string().optional(), subdir: z.string().optional() }),
    z.object({ kind: z.literal('image'), uri: z.string().describe('Container image, e.g. us-docker.pkg.dev/p/r/app@sha256:...') }),
    z.object({
      kind: z.literal('inline'),
      files: z.array(z.object({ path: z.string(), content: z.string() })).max(50).describe('Small apps only (< 256 KB total)'),
    }),
  ])
  .describe('Where the app code comes from. Prefer upload for local projects.');

const Tier = z.enum(['T0', 'T1', 'T2', 'T3', 'T4']).describe('T0 snapshot, T1 serverless, T2 batch, T3 always-on, T4 heavy');

const Caps = z
  .object({
    max_instances: z.number().int().min(0).max(10).optional(),
    concurrency: z.number().int().min(1).max(1000).optional(),
    requests_per_minute: z.number().int().min(1).optional(),
    monthly_spend_usd: z.number().min(0).optional(),
    cpu: z.enum(['0.5', '1', '2', '4']).optional(),
    memory: z.string().regex(/^\d+(Mi|Gi)$/).optional(),
    timeout_s: z.number().int().min(1).max(3600).optional(),
  })
  .describe('Per-app limits. Omitted fields keep their current value.');

const Wait = z
  .number()
  .int()
  .min(0)
  .max(55)
  .default(20)
  .describe('Seconds to wait for completion before returning the operation handle. Progress streams meanwhile.');

const Operation = z.object({
  id: z.string(),
  kind: z.enum(['estimate', 'deploy', 'delete']),
  app: z.string().nullable(),
  status: z.enum(['queued', 'running', 'succeeded', 'failed', 'cancelled']),
  done: z.boolean(),
  progress: z.object({ step: z.string(), completed_steps: z.number(), total_steps: z.number(), message: z.string() }),
  steps: z.array(z.object({ name: z.string(), status: z.string() })),
  result: z.unknown(),
  error: z.object({ code: z.string(), message: z.string() }).nullable(),
  poll_after_s: z.number(),
  created_at: z.string(),
  updated_at: z.string(),
});
type Operation = z.infer<typeof Operation>;

const Principal = z
  .string()
  .regex(/^(user|group|domain):.+$/)
  .describe('user:alice@acme.com, group:analysts@acme.com or domain:acme.com');

// --- helpers --------------------------------------------------------------------------

type Ctx = Parameters<Parameters<McpServer['registerTool']>[2]>[1];

function ok<T extends object>(data: T) {
  return { content: [{ type: 'text' as const, text: JSON.stringify(data, null, 2) }], structuredContent: data };
}

function fail(err: unknown) {
  const message = err instanceof ApiError ? `${err.code} (${err.status}): ${err.message}` : String(err);
  return { content: [{ type: 'text' as const, text: message }], isError: true };
}

function token(ctx: Ctx) {
  return ctx.http?.authInfo?.token;
}

// The 2026-07-28 transport drops a request whose response stream breaks, and the
// client re-issues it with a new id. Without a key from the agent, derive one from
// caller + arguments within a 10-minute window so the retry does not deploy twice.
function deriveKey(ctx: Ctx, tool: string, args: unknown, explicit?: string) {
  if (explicit) return explicit;
  const bucket = Math.floor(Date.now() / 600_000);
  return createHash('sha256')
    .update(JSON.stringify([ctx.http?.authInfo?.clientId ?? 'anon', tool, args, bucket]))
    .digest('hex')
    .slice(0, 32);
}

const sleep = (ms: number, signal: AbortSignal) =>
  new Promise<void>((resolve) => {
    const t = setTimeout(resolve, ms);
    signal.addEventListener('abort', () => (clearTimeout(t), resolve()), { once: true });
  });

// Poll the API until the operation finishes or `waitS` elapses, emitting
// notifications/progress on the request's SSE stream when the client asked for it.
async function waitFor(op: Operation, waitS: number, ctx: Ctx): Promise<Operation> {
  const progressToken = ctx.mcpReq._meta?.progressToken;
  const deadline = Date.now() + waitS * 1000;
  let last = -1;
  while (true) {
    if (progressToken !== undefined && op.progress.completed_steps > last) {
      last = op.progress.completed_steps;
      await ctx.mcpReq.notify({
        method: 'notifications/progress',
        params: { progressToken, progress: last, total: op.progress.total_steps, message: op.progress.message },
      });
    }
    if (op.done || Date.now() >= deadline || ctx.mcpReq.signal.aborted) return op;
    await sleep(Math.min(1000, deadline - Date.now()), ctx.mcpReq.signal);
    op = await api<Operation>(`/v1/operations/${op.id}`, { token: token(ctx), signal: ctx.mcpReq.signal });
  }
}

function opResult(op: Operation) {
  const next = op.done ? undefined : `Not finished yet. Call get_operation with id "${op.id}" (wait_s up to 55) to keep waiting.`;
  return ok({ operation: op, ...(next ? { next } : {}) });
}

// --- tools ----------------------------------------------------------------------------

export function registerTools(server: McpServer) {
  server.registerTool(
    'whoami',
    {
      title: 'Who am I',
      description: 'The signed-in user, workspace, GCP deploy targets (ShipCube-hosted or linked projects) and plan limits.',
      outputSchema: z.object({
        user_id: z.string(),
        email: z.string(),
        workspace: z.object({ id: z.string(), name: z.string() }),
        gcp_targets: z.array(z.object({ id: z.string(), project: z.string(), kind: z.enum(['shipcube', 'linked']), default: z.boolean() })),
        limits: z.object({ max_apps: z.number(), monthly_spend_usd: z.number() }),
      }),
      annotations: { readOnlyHint: true, openWorldHint: false },
    },
    async (ctx) => {
      try {
        return ok(await api('/v1/me', { token: token(ctx) }));
      } catch (e) {
        return fail(e);
      }
    },
  );

  server.registerTool(
    'create_upload',
    {
      title: 'Create source upload',
      description:
        'Get a short-lived signed URL to upload the app source as a .tar.gz. Then run e.g. ' +
        '`tar czf - . | curl -X PUT -H "Content-Type: application/gzip" --data-binary @- "<url>"` and pass ' +
        '{"kind":"upload","upload_id":...} as the source to estimate_load or deploy_app.',
      inputSchema: z.object({ content_type: z.string().default('application/gzip') }),
      outputSchema: z.object({
        upload_id: z.string(),
        method: z.literal('PUT'),
        url: z.string(),
        headers: z.record(z.string(), z.string()),
        max_bytes: z.number(),
        expires_at: z.string(),
      }),
      annotations: { readOnlyHint: false, destructiveHint: false, openWorldHint: false },
    },
    async (args, ctx) => {
      try {
        return ok(await api('/v1/uploads', { body: args, token: token(ctx) }));
      } catch (e) {
        return fail(e);
      }
    },
  );

  server.registerTool(
    'estimate_load',
    {
      title: 'Estimate load',
      description:
        'Profile the app in a sandbox on sample data, classify the workload and recommend a tier, resources, caps and a ' +
        'monthly cost, with reasoning from Muse Spark. Long-running: returns an operation; result.estimate_id can be passed to deploy_app.',
      inputSchema: z.object({
        source: Source,
        app: AppName.optional(),
        expected_users: z.number().int().min(1).optional().describe('Rough number of people who will open the app'),
        schedule: z.string().optional().describe('Cron expression if this is a scheduled job'),
        wait_s: Wait,
      }),
      outputSchema: z.object({ operation: Operation, next: z.string().optional() }),
      annotations: { readOnlyHint: true, openWorldHint: false },
    },
    async ({ wait_s, ...args }, ctx) => {
      try {
        const op = await api<Operation>('/v1/estimates', {
          body: args,
          token: token(ctx),
          idempotencyKey: deriveKey(ctx, 'estimate_load', args),
        });
        return opResult(await waitFor(op, wait_s, ctx));
      } catch (e) {
        return fail(e);
      }
    },
  );

  server.registerTool(
    'deploy_app',
    {
      title: 'Deploy app',
      description:
        'Build and deploy an app (Cloud Build -> Artifact Registry -> Cloud Run service/job). New apps are private: only the owner ' +
        'can open them until grant_access is called. Long-running: returns an operation. Safe to retry with the same idempotency_key.',
      inputSchema: z.object({
        app: AppName,
        source: Source,
        estimate_id: z.string().optional().describe('Use the tier and sizing from a finished estimate_load'),
        tier: Tier.optional().describe('Override the estimated tier'),
        target: z.string().optional().describe('GCP target id from whoami; defaults to the workspace default'),
        region: z.string().default('us-central1'),
        env: z.record(z.string(), z.string()).optional().describe('Non-secret environment variables'),
        secrets: z.record(z.string(), z.string()).optional().describe('ENV_NAME -> secret name in ShipCube secrets (never values)'),
        caps: Caps.optional(),
        idempotency_key: z.string().max(64).optional().describe('Reuse on retry to avoid a duplicate deploy'),
        wait_s: Wait,
      }),
      outputSchema: z.object({ operation: Operation, next: z.string().optional() }),
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: false },
    },
    async ({ wait_s, idempotency_key, app, ...args }, ctx) => {
      try {
        const op = await api<Operation>(`/v1/apps/${app}/deployments`, {
          body: args,
          token: token(ctx),
          idempotencyKey: deriveKey(ctx, 'deploy_app', { app, ...args }, idempotency_key),
        });
        return opResult(await waitFor(op, wait_s, ctx));
      } catch (e) {
        return fail(e);
      }
    },
  );

  server.registerTool(
    'get_operation',
    {
      title: 'Get operation',
      description: 'Status of a long-running estimate, deploy or delete. Waits up to wait_s seconds for it to finish, streaming progress.',
      inputSchema: z.object({ id: z.string(), wait_s: Wait }),
      outputSchema: z.object({ operation: Operation, next: z.string().optional() }),
      annotations: { readOnlyHint: true, openWorldHint: false },
    },
    async ({ id, wait_s }, ctx) => {
      try {
        const op = await api<Operation>(`/v1/operations/${id}`, { token: token(ctx) });
        return opResult(await waitFor(op, wait_s, ctx));
      } catch (e) {
        return fail(e);
      }
    },
  );

  server.registerTool(
    'cancel_operation',
    {
      title: 'Cancel operation',
      description: 'Cancel a running estimate or deploy. Steps already finished are not rolled back; the live revision keeps serving.',
      inputSchema: z.object({ id: z.string() }),
      outputSchema: z.object({ operation: Operation }),
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: false },
    },
    async ({ id }, ctx) => {
      try {
        return ok({ operation: await api<Operation>(`/v1/operations/${id}/cancel`, { method: 'POST', token: token(ctx) }) });
      } catch (e) {
        return fail(e);
      }
    },
  );

  server.registerTool(
    'list_apps',
    {
      title: 'List apps',
      description: 'Apps in the workspace with their URL, tier and status.',
      outputSchema: z.object({
        apps: z.array(z.object({ name: z.string(), url: z.string(), tier: z.string(), status: z.string(), updated_at: z.string() })),
      }),
      annotations: { readOnlyHint: true, openWorldHint: false },
    },
    async (ctx) => {
      try {
        return ok(await api('/v1/apps', { token: token(ctx) }));
      } catch (e) {
        return fail(e);
      }
    },
  );

  server.registerTool(
    'get_status',
    {
      title: 'Get app status',
      description: 'Full state of one app: URL, tier, target project, live revision, caps, access list and AI provider.',
      inputSchema: z.object({ app: AppName }),
      outputSchema: z.object({
        name: z.string(),
        url: z.string(),
        tier: z.string(),
        target: z.string(),
        region: z.string(),
        status: z.string(),
        revision: z.string(),
        image: z.string(),
        caps: z.record(z.string(), z.unknown()),
        access: z.array(z.object({ principal: z.string(), role: z.string(), granted_at: z.string() })),
        ai: z.object({ provider: z.string(), model: z.string(), key_secret: z.string().nullable() }),
        updated_at: z.string(),
      }),
      annotations: { readOnlyHint: true, openWorldHint: false },
    },
    async ({ app }, ctx) => {
      try {
        return ok(await api(`/v1/apps/${app}`, { token: token(ctx) }));
      } catch (e) {
        return fail(e);
      }
    },
  );

  server.registerTool(
    'get_logs',
    {
      title: 'Get logs',
      description: 'Recent log entries for an app, newest first.',
      inputSchema: z.object({
        app: AppName,
        severity: z.enum(['DEBUG', 'INFO', 'WARNING', 'ERROR']).optional(),
        limit: z.number().int().min(1).max(500).default(50),
        page_token: z.string().optional(),
      }),
      outputSchema: z.object({
        entries: z.array(z.object({ timestamp: z.string(), severity: z.string(), message: z.string(), revision: z.string().optional() })),
        next_page_token: z.string().nullable(),
      }),
      annotations: { readOnlyHint: true, openWorldHint: false },
    },
    async ({ app, ...q }, ctx) => {
      try {
        return ok(await api(`/v1/apps/${app}/logs`, { query: q, token: token(ctx) }));
      } catch (e) {
        return fail(e);
      }
    },
  );

  server.registerTool(
    'get_metrics',
    {
      title: 'Get metrics',
      description: 'Health, cost and cap usage for an app over a time window.',
      inputSchema: z.object({ app: AppName, window: z.enum(['15m', '1h', '24h', '7d', '30d']).default('1h') }),
      outputSchema: z.object({
        app: z.string(),
        window: z.string(),
        requests: z.number(),
        error_rate: z.number(),
        latency_ms: z.object({ p50: z.number(), p95: z.number() }),
        instances: z.object({ avg: z.number(), max: z.number() }),
        cold_starts: z.number(),
        cpu_utilization: z.number(),
        memory_utilization: z.number(),
        cost_usd: z.record(z.string(), z.number()),
        caps_hit: z.record(z.string(), z.number()),
        data_freshness_minutes: z.number().nullable(),
      }),
      annotations: { readOnlyHint: true, openWorldHint: false },
    },
    async ({ app, window }, ctx) => {
      try {
        return ok(await api(`/v1/apps/${app}/metrics`, { query: { window }, token: token(ctx) }));
      } catch (e) {
        return fail(e);
      }
    },
  );

  server.registerTool(
    'set_caps',
    {
      title: 'Set caps',
      description: 'Change spend, rate, concurrency and resource caps for an app. Returns where each cap is enforced.',
      inputSchema: z.object({ app: AppName, caps: Caps }),
      outputSchema: z.object({ caps: z.record(z.string(), z.unknown()), enforced_by: z.record(z.string(), z.string()) }),
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: false },
    },
    async ({ app, caps }, ctx) => {
      try {
        return ok(await api(`/v1/apps/${app}/caps`, { method: 'PATCH', body: caps, token: token(ctx) }));
      } catch (e) {
        return fail(e);
      }
    },
  );

  const AccessList = z.object({ access: z.array(z.object({ principal: z.string(), role: z.string(), granted_at: z.string() })) });

  server.registerTool(
    'grant_access',
    {
      title: 'Grant access',
      description: 'Let a user, Google group or whole domain open the app. They sign in with Google.',
      inputSchema: z.object({ app: AppName, principal: Principal, role: z.enum(['viewer', 'editor']).default('viewer') }),
      outputSchema: AccessList,
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: false },
    },
    async ({ app, ...body }, ctx) => {
      try {
        return ok(await api(`/v1/apps/${app}/access`, { body, token: token(ctx) }));
      } catch (e) {
        return fail(e);
      }
    },
  );

  server.registerTool(
    'revoke_access',
    {
      title: 'Revoke access',
      description: 'Remove a principal from the app. The owner cannot be removed.',
      inputSchema: z.object({ app: AppName, principal: Principal }),
      outputSchema: AccessList,
      annotations: { readOnlyHint: false, destructiveHint: true, idempotentHint: true, openWorldHint: false },
    },
    async ({ app, principal }, ctx) => {
      try {
        return ok(await api(`/v1/apps/${app}/access/${encodeURIComponent(principal)}`, { method: 'DELETE', token: token(ctx) }));
      } catch (e) {
        return fail(e);
      }
    },
  );

  server.registerTool(
    'connect_data',
    {
      title: 'Connect data source',
      description:
        'Register a data source for the semantic layer. Pass credentials as a ShipCube secret name, never inline. ' +
        'Returns the static egress IP to allowlist on the database.',
      inputSchema: z.object({
        name: z.string().regex(/^[a-z][a-z0-9_]{1,40}$/),
        kind: z.enum(['postgres', 'bigquery', 'duckdb', 'snowflake']),
        connection_secret: z.string().optional().describe('Secret name holding the DSN or service-account key'),
        model_yaml: z.string().optional().describe('Semantic DSL (cubes) for this source; inferred from the schema if omitted'),
      }),
      outputSchema: z.object({
        datasource_id: z.string(),
        name: z.string(),
        kind: z.string(),
        egress: z.string(),
        cubes_detected: z.array(z.string()),
        status: z.string(),
      }),
      annotations: { readOnlyHint: false, destructiveHint: false, openWorldHint: true },
    },
    async (args, ctx) => {
      try {
        return ok(await api('/v1/datasources', { body: args, token: token(ctx) }));
      } catch (e) {
        return fail(e);
      }
    },
  );

  server.registerTool(
    'query',
    {
      title: 'Query semantic layer',
      description: 'Run a typed semantic query (measures, dimensions, time, filters, operators). Returns rows, the compiled SQL and its level.',
      inputSchema: z.object({
        datasource: z.string().optional(),
        query: z.object({
          measures: z.array(z.unknown()).min(1),
          dimensions: z.array(z.string()).optional(),
          time: z.object({ dimension: z.string(), granularity: z.string().optional(), range: z.string().optional() }).optional(),
          filters: z.unknown().optional(),
          order: z.array(z.tuple([z.string(), z.enum(['asc', 'desc'])])).optional(),
          limit: z.number().int().max(10_000).optional(),
        }),
      }),
      outputSchema: z.object({
        level: z.enum(['L0', 'L1', 'L2', 'L3']),
        sql: z.string(),
        columns: z.array(z.object({ name: z.string(), type: z.string() })),
        rows: z.array(z.array(z.unknown())),
        cached: z.boolean(),
        verified: z.boolean(),
        datasource: z.string(),
      }),
      annotations: { readOnlyHint: true, openWorldHint: true },
    },
    async (args, ctx) => {
      try {
        return ok(await api('/v1/query', { body: args, token: token(ctx) }));
      } catch (e) {
        return fail(e);
      }
    },
  );

  server.registerTool(
    'ai_config',
    {
      title: 'Configure AI provider',
      description:
        "Choose the model provider the app reaches through ShipCube's AI gateway. Muse is the default; BYOK keys are referenced by secret name.",
      inputSchema: z.object({
        app: AppName,
        provider: z.enum(['meta', 'openai_compatible', 'openai', 'anthropic']).default('meta'),
        model: z.string().default('muse-spark'),
        key_secret: z.string().optional().describe('Secret name holding the BYOK API key'),
        base_url: z.string().url().optional().describe('For openai_compatible providers'),
      }),
      outputSchema: z.object({
        ai: z.object({ provider: z.string(), model: z.string(), key_secret: z.string().nullable() }),
        gateway_url: z.string(),
      }),
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: false },
    },
    async ({ app, ...body }, ctx) => {
      try {
        return ok(await api(`/v1/apps/${app}/ai`, { method: 'PUT', body, token: token(ctx) }));
      } catch (e) {
        return fail(e);
      }
    },
  );

  server.registerTool(
    'delete_app',
    {
      title: 'Delete app',
      description: 'Tear down an app: the Cloud Run service or job, its image and access bindings. Irreversible. Long-running.',
      inputSchema: z.object({
        app: AppName,
        confirm: z.literal(true).describe('Must be true; ask the user first'),
        wait_s: Wait,
      }),
      outputSchema: z.object({ operation: Operation, next: z.string().optional() }),
      annotations: { readOnlyHint: false, destructiveHint: true, idempotentHint: true, openWorldHint: false },
    },
    async ({ app, wait_s }, ctx) => {
      try {
        const op = await api<Operation>(`/v1/apps/${app}`, {
          method: 'DELETE',
          token: token(ctx),
          idempotencyKey: deriveKey(ctx, 'delete_app', { app }),
        });
        return opResult(await waitFor(op, wait_s, ctx));
      } catch (e) {
        return fail(e);
      }
    },
  );
}
