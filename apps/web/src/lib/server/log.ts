import "server-only";

// Structured JSON logs for Cloud Logging: one object per line on stdout/stderr. Cloud Run
// parses `severity`, `message` and `logging.googleapis.com/trace` into the log entry.

type Severity = "DEBUG" | "INFO" | "WARNING" | "ERROR";

export interface LogContext {
  requestId?: string;
  trace?: string;
  [key: string]: unknown;
}

const REDACT = /(authorization|cookie|token|secret|password|key)$/i;

function scrub(fields: Record<string, unknown>) {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(fields)) out[k] = REDACT.test(k) ? "[redacted]" : v;
  return out;
}

export function log(severity: Severity, message: string, ctx: LogContext = {}) {
  const { trace, ...rest } = ctx;
  const project = process.env.GOOGLE_CLOUD_PROJECT;
  const entry = {
    severity,
    message,
    component: "console",
    time: new Date().toISOString(),
    ...(trace && project ? { "logging.googleapis.com/trace": `projects/${project}/traces/${trace}` } : {}),
    ...scrub(rest),
  };
  const line = JSON.stringify(entry);
  if (severity === "ERROR" || severity === "WARNING") console.error(line);
  else console.log(line);
}

/** Trace id from the incoming request (Cloud Run sets X-Cloud-Trace-Context; W3C traceparent otherwise). */
export function traceFrom(h: Headers): { trace?: string; traceparent?: string } {
  const traceparent = h.get("traceparent") ?? undefined;
  const cloud = h.get("x-cloud-trace-context");
  const trace = cloud?.split("/")[0] ?? traceparent?.split("-")[1];
  return { trace, traceparent };
}
