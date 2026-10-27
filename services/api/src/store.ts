// In-memory mock state. Operations advance with wall-clock time so that polling
// and progress notifications behave like a real deploy without touching GCP.

import { createHash, randomUUID } from 'node:crypto';

export type OpKind = 'estimate' | 'deploy' | 'delete';
export type OpStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';

export interface Operation {
  id: string;
  kind: OpKind;
  app: string | null;
  status: OpStatus;
  done: boolean;
  progress: { step: string; completed_steps: number; total_steps: number; message: string };
  steps: Array<{ name: string; status: OpStatus | 'pending' }>;
  result: unknown;
  error: { code: string; message: string } | null;
  poll_after_s: number;
  created_at: string;
  updated_at: string;
}

const STEPS: Record<OpKind, Array<[string, string, number]>> = {
  // [step, message, seconds]
  estimate: [
    ['fetch_source', 'Fetching source', 1],
    ['sandbox_profile', 'Running in sandbox on sample data', 3],
    ['classify', 'Classifying workload', 1],
    ['reason', 'Muse Spark reviewing code and profile', 2],
    ['price', 'Pricing candidate tiers', 1],
  ],
  deploy: [
    ['validate', 'Validating source and caps', 1],
    ['build', 'Cloud Build: building container', 4],
    ['push', 'Pushing image to Artifact Registry', 1],
    ['deploy', 'Creating Cloud Run revision', 2],
    ['configure_access', 'Applying private-by-default access policy', 1],
    ['health_check', 'Waiting for the revision to become ready', 1],
  ],
  delete: [['teardown', 'Deleting service, image and access bindings', 2]],
};

interface OpRecord {
  op: Operation;
  startedAt: number;
  cancelledAt?: number;
  input: any;
}

const ops = new Map<string, OpRecord>();
const idempotency = new Map<string, { opId: string; bodyHash: string }>();

const now = () => new Date().toISOString();

const me = {
  user_id: 'usr_demo',
  email: 'owner@example.com',
  workspace: { id: 'ws_demo', name: 'Demo workspace' },
  gcp_targets: [
    { id: 'shipcube', project: 'shipcube-hosted', kind: 'shipcube', default: true },
    { id: 'linked-1', project: 'acme-analytics-prod', kind: 'linked', default: false },
  ],
  limits: { max_apps: 5, monthly_spend_usd: 20 },
};

function seedApp(name: string, tier: string) {
  return {
    name,
    url: `https://${name}.apps.shipcube.dev`,
    tier,
    target: 'shipcube',
    region: 'us-central1',
    status: 'serving',
    revision: `${name}-00003-abc`,
    image: `us-central1-docker.pkg.dev/shipcube-hosted/apps/${name}@sha256:mock`,
    caps: { max_instances: 2, concurrency: 40, requests_per_minute: 120, monthly_spend_usd: 5, cpu: '1', memory: '512Mi', timeout_s: 60 },
    access: [{ principal: `user:${me.email}`, role: 'owner', granted_at: '2026-10-26T12:00:00Z' }] as Array<{
      principal: string;
      role: string;
      granted_at: string;
    }>,
    ai: { provider: 'meta', model: 'muse-spark', key_secret: null as string | null },
    updated_at: '2026-10-26T12:00:00Z',
  };
}
type App = ReturnType<typeof seedApp>;

const apps = new Map<string, App>([['regional-revenue', seedApp('regional-revenue', 'T1')]]);

function materialize(rec: OpRecord): Operation {
  const { op } = rec;
  if (op.done) return op;
  const steps = STEPS[op.kind];
  const until = rec.cancelledAt ?? Date.now();
  let elapsed = (until - rec.startedAt) / 1000;
  let completed = 0;
  for (const [, , secs] of steps) {
    if (elapsed < secs) break;
    elapsed -= secs;
    completed++;
  }
  const finished = completed >= steps.length;
  op.steps = steps.map(([name], i) => ({
    name,
    status: i < completed ? 'succeeded' : i === completed && !finished ? (rec.cancelledAt ? 'cancelled' : 'running') : 'pending',
  }));
  const current = steps[Math.min(completed, steps.length - 1)];
  op.progress = { step: current[0], completed_steps: completed, total_steps: steps.length, message: finished ? 'Done' : current[1] };
  op.status = rec.cancelledAt ? 'cancelled' : finished ? 'succeeded' : 'running';
  op.done = rec.cancelledAt !== undefined || finished;
  op.updated_at = now();
  if (finished && !rec.cancelledAt) op.result = complete(op, rec.input);
  return op;
}

function complete(op: Operation, input: any): unknown {
  if (op.kind === 'estimate') {
    return {
      estimate_id: `est_${op.id.slice(3)}`,
      workload_class: 'bursty_interactive',
      confidence: 0.87,
      tier: 'T1',
      target: 'cloud_run_service',
      resources: { cpu: '1', memory: '512Mi', concurrency: 40, min_instances: 0, max_instances: 2 },
      monthly_cost_usd: { low: 0.0, expected: 1.8, high: 6.5 },
      profile: { peak_rss_mb: 210, cpu_seconds: 3.4, wall_seconds: 5.1, queries: 12, io_mb: 18 },
      recommended_caps: { requests_per_minute: 120, monthly_spend_usd: 5 },
      reasoning:
        'Short CPU bursts per request with long idle gaps and modest memory. Scale-to-zero Cloud Run fits; ' +
        'two instances cover the expected 5 concurrent viewers.',
      alternatives: [
        { tier: 'T0', why_not: 'Data refreshes hourly and viewers filter interactively, so a static snapshot is too stale.' },
        { tier: 'T3', why_not: 'Always-on min instances would cost ~$15/month for no latency benefit at this load.' },
      ],
    };
  }
  if (op.kind === 'deploy') {
    const name = op.app!;
    const existing = apps.get(name);
    const app = existing ?? seedApp(name, input?.tier ?? 'T1');
    app.status = 'serving';
    app.tier = input?.tier ?? app.tier;
    app.target = input?.target ?? app.target;
    if (input?.caps) app.caps = { ...app.caps, ...input.caps };
    app.revision = `${name}-0000${existing ? 4 : 1}-xyz`;
    app.updated_at = now();
    apps.set(name, app);
    return { app: name, url: app.url, revision: app.revision, tier: app.tier, target: app.target, access: 'private (owner only)' };
  }
  apps.delete(op.app!);
  return { app: op.app, deleted: true };
}

export const store = {
  me,
  apps,

  startOperation(kind: OpKind, app: string | null, input: any, idemKey?: string): Operation {
    const bodyHash = createHash('sha256').update(JSON.stringify({ kind, app, input })).digest('hex');
    if (idemKey) {
      const prior = idempotency.get(idemKey);
      if (prior) {
        if (prior.bodyHash !== bodyHash) {
          throw Object.assign(new Error('idempotency key reused with a different request'), { status: 409 });
        }
        return materialize(ops.get(prior.opId)!);
      }
    }
    const op: Operation = {
      id: `op_${randomUUID().slice(0, 12)}`,
      kind,
      app,
      status: 'queued',
      done: false,
      progress: { step: STEPS[kind][0][0], completed_steps: 0, total_steps: STEPS[kind].length, message: 'Queued' },
      steps: STEPS[kind].map(([name]) => ({ name, status: 'pending' })),
      result: null,
      error: null,
      poll_after_s: kind === 'deploy' ? 3 : 2,
      created_at: now(),
      updated_at: now(),
    };
    ops.set(op.id, { op, startedAt: Date.now(), input });
    if (idemKey) idempotency.set(idemKey, { opId: op.id, bodyHash });
    return op;
  },

  getOperation(id: string) {
    const rec = ops.get(id);
    return rec ? materialize(rec) : undefined;
  },

  cancelOperation(id: string) {
    const rec = ops.get(id);
    if (!rec) return undefined;
    if (!rec.op.done) rec.cancelledAt = Date.now();
    return materialize(rec);
  },

  logs(app: string, limit: number, severity?: string) {
    const base = Date.now();
    const lines = [
      ['INFO', 'GET /api/revenue?region=SP 200 84ms'],
      ['INFO', 'GET / 200 12ms'],
      ['WARNING', 'semantic layer: query fell back to L3 raw SQL (unverified)'],
      ['INFO', 'Cold start: 1.9s'],
      ['ERROR', 'upstream warehouse timeout after 30s'],
    ];
    return Array.from({ length: Math.min(limit, 20) }, (_, i) => {
      const [sev, msg] = lines[i % lines.length];
      return { timestamp: new Date(base - i * 37_000).toISOString(), severity: sev, message: msg, revision: apps.get(app)?.revision };
    }).filter((e) => !severity || e.severity === severity);
  },

  metrics(app: string, window: string) {
    return {
      app,
      window,
      requests: 412,
      error_rate: 0.012,
      latency_ms: { p50: 84, p95: 410 },
      instances: { avg: 0.4, max: 2 },
      cold_starts: 6,
      cpu_utilization: 0.21,
      memory_utilization: 0.43,
      cost_usd: { compute: 0.03, ai_tokens: 0.01, warehouse: 0.0, month_to_date: 0.62 },
      caps_hit: { rate_limited_requests: 3, max_instances_reached: 0 },
      data_freshness_minutes: 42,
    };
  },

  enforcement(caps: Record<string, unknown>) {
    const where: Record<string, string> = {
      max_instances: 'Cloud Run --max-instances',
      concurrency: 'Cloud Run --concurrency',
      requests_per_minute: 'ShipCube gateway token bucket (per app, per principal)',
      monthly_spend_usd: 'Billing budget alert -> scale to zero (max-instances=0)',
      cpu: 'Cloud Run resource limits',
      memory: 'Cloud Run resource limits',
      timeout_s: 'Cloud Run request timeout',
    };
    return Object.fromEntries(Object.keys(caps).map((k) => [k, where[k] ?? 'not enforced (unknown cap)']));
  },

  connectData(body: any) {
    return {
      datasource_id: `ds_${randomUUID().slice(0, 8)}`,
      name: body.name,
      kind: body.kind,
      egress: body.kind === 'duckdb' ? 'none (bundled)' : 'static egress IP 34.0.0.1 via Cloud NAT (allowlist this)',
      cubes_detected: ['orders', 'customers'],
      status: 'connected',
    };
  },

  query(body: any) {
    return {
      level: 'L0',
      sql: 'SELECT region, SUM(amount) AS revenue FROM orders GROUP BY 1 ORDER BY 2 DESC LIMIT 100',
      columns: [
        { name: 'orders.region', type: 'string' },
        { name: 'orders.revenue', type: 'number' },
      ],
      rows: [
        ['SP', 1_284_220.5],
        ['RJ', 512_904.1],
        ['MG', 471_330.0],
      ],
      cached: false,
      verified: true,
      datasource: body.datasource ?? 'default',
    };
  },
};
