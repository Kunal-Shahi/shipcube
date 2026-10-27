import "server-only";

import { ApiError, codeForStatus } from "@/lib/errors";
import { config } from "./config";
import { log, traceFrom } from "./log";

// The one way the console talks to the control-plane API (services/api/openapi.yaml).
// Typed errors, a timeout on every call, and retries with backoff for idempotent requests.

export type Method = "GET" | "POST" | "PATCH" | "PUT" | "DELETE";

export interface RequestOptions {
  method?: Method;
  body?: unknown;
  query?: Record<string, string | number | undefined>;
  /** Makes a POST/PATCH/DELETE safe to retry; the API returns the same result for a repeated key. */
  idempotencyKey?: string;
  timeoutMs?: number;
  /** Total attempts for retryable failures. */
  attempts?: number;
}

/** Supplies the caller's credential for the API, or null when signed out. Set by the auth module. */
let credential: () => Promise<string | null> = async () => null;
export function setApiCredential(fn: () => Promise<string | null>) {
  credential = fn;
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export function backoffMs(attempt: number, retryAfterS?: number) {
  if (retryAfterS !== undefined && Number.isFinite(retryAfterS)) return Math.min(retryAfterS * 1000, 10_000);
  const cap = Math.min(250 * 2 ** attempt, 4_000);
  return Math.round(cap / 2 + Math.random() * (cap / 2));
}

export function isIdempotent(method: Method, idempotencyKey?: string) {
  return method === "GET" || method === "PUT" || method === "DELETE" || Boolean(idempotencyKey);
}

async function incoming(): Promise<Headers | null> {
  try {
    const { headers } = await import("next/headers");
    return await headers();
  } catch {
    return null; // outside a request (tests, build)
  }
}

async function parseError(res: Response, requestId: string): Promise<ApiError> {
  let code: string = codeForStatus(res.status);
  let message = `${res.status} ${res.statusText}`;
  try {
    const body = await res.json();
    if (body?.error?.message) {
      code = body.error.code ?? code;
      message = body.error.message;
    } else if (typeof body?.detail === "string") {
      message = body.detail;
    }
  } catch {
    // Non-JSON error body (a proxy, a load balancer). Keep the status line.
  }
  return new ApiError(code, message, res.status, res.headers.get("x-request-id") ?? requestId);
}

export async function api<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const cfg = config();
  if (!cfg.apiUrl) throw new ApiError("internal", "SHIPCUBE_API_URL is not set.", 500, "");
  const method = opts.method ?? "GET";
  const retryable = isIdempotent(method, opts.idempotencyKey);
  const attempts = retryable ? (opts.attempts ?? 3) : 1;

  const url = new URL(cfg.apiUrl + path);
  for (const [k, v] of Object.entries(opts.query ?? {})) if (v !== undefined) url.searchParams.set(k, String(v));

  const h = await incoming();
  const { trace, traceparent } = h ? traceFrom(h) : {};
  const requestId = crypto.randomUUID();
  const token = await credential();

  const headers: Record<string, string> = { Accept: "application/json", "X-Request-Id": requestId };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (traceparent) headers.traceparent = traceparent;
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (opts.idempotencyKey) headers["Idempotency-Key"] = opts.idempotencyKey;

  let last: ApiError | null = null;
  for (let attempt = 0; attempt < attempts; attempt++) {
    const started = performance.now();
    let retryAfter: number | undefined;
    try {
      const res = await fetch(url, {
        method,
        headers,
        body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
        signal: AbortSignal.timeout(opts.timeoutMs ?? cfg.apiTimeoutMs),
        cache: "no-store",
      });
      const ms = Math.round(performance.now() - started);
      if (res.ok) {
        log("INFO", "api call", { requestId, trace, method, path, status: res.status, ms, attempt });
        if (res.status === 204) return undefined as T;
        return (await res.json()) as T;
      }
      last = await parseError(res, requestId);
      const ra = res.headers.get("retry-after");
      retryAfter = ra ? Number(ra) : undefined;
      // 5xx and exhausted retries are errors; 4xx are the caller's problem and only a warning.
      log(res.status >= 500 && attempt + 1 >= attempts ? "ERROR" : "WARNING", "api call failed", {
        requestId, trace, method, path, status: res.status, code: last.code, ms, attempt,
      });
    } catch (e) {
      const timedOut = e instanceof DOMException && (e.name === "TimeoutError" || e.name === "AbortError");
      last = new ApiError(
        timedOut ? "timeout" : "network",
        timedOut ? `The API didn't answer within ${opts.timeoutMs ?? cfg.apiTimeoutMs} ms.` : "Couldn't reach the API.",
        0,
        requestId,
      );
      log(attempt + 1 < attempts ? "WARNING" : "ERROR", "api call failed", {
        requestId, trace, method, path, code: last.code, error: String(e), attempt,
      });
    }
    if (!last.retryable || attempt + 1 >= attempts) break;
    await sleep(backoffMs(attempt, retryAfter));
  }
  throw last!;
}
