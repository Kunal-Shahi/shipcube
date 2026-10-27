// Thin client for the control-plane API (services/api/openapi.yaml).
// The MCP server holds no state of its own: every tool is one or two API calls.

const API_URL = process.env.SHIPCUBE_API_URL ?? 'http://127.0.0.1:8081';

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

export interface CallOptions {
  method?: string;
  body?: unknown;
  query?: Record<string, string | number | undefined>;
  idempotencyKey?: string;
  // The caller's bearer token, forwarded so the API authorizes the end user.
  token?: string;
  signal?: AbortSignal;
}

export async function api<T = any>(path: string, opts: CallOptions = {}): Promise<T> {
  const url = new URL(path, API_URL);
  for (const [k, v] of Object.entries(opts.query ?? {})) if (v !== undefined) url.searchParams.set(k, String(v));
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (opts.body !== undefined) headers['Content-Type'] = 'application/json';
  if (opts.idempotencyKey) headers['Idempotency-Key'] = opts.idempotencyKey;
  if (opts.token) headers.Authorization = `Bearer ${opts.token}`;
  const res = await fetch(url, {
    method: opts.method ?? (opts.body === undefined ? 'GET' : 'POST'),
    headers,
    body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
    signal: opts.signal,
  });
  const text = await res.text();
  const json = text ? JSON.parse(text) : undefined;
  if (!res.ok) throw new ApiError(res.status, json?.error?.code ?? 'unknown', json?.error?.message ?? res.statusText);
  return json as T;
}
