// Server-side errors that escape a page or route handler end up here. They're written as
// structured JSON so Cloud Logging and Error Reporting pick them up with the request path.

// At boot, a production server with invalid configuration (for example no SHIPCUBE_MCP_URL)
// exits instead of serving. `next build` needs no environment, so the check skips the build.
export async function register() {
  if (process.env.NEXT_RUNTIME !== "nodejs" || process.env.NODE_ENV !== "production") return;
  if (process.env.NEXT_PHASE === "phase-production-build") return;
  const { config } = await import("./lib/server/config");
  try {
    config();
  } catch (e) {
    console.error(JSON.stringify({ severity: "CRITICAL", message: `Refusing to start: ${(e as Error).message}`, component: "console" }));
    process.exit(1);
  }
}

export async function onRequestError(
  error: unknown,
  request: { path: string; method: string; headers: Record<string, string | string[] | undefined> },
  context: { routePath: string; routeType: string },
) {
  const e = error as Error & { digest?: string; code?: string; requestId?: string };
  const trace = String(request.headers["x-cloud-trace-context"] ?? "").split("/")[0] || undefined;
  const project = process.env.GOOGLE_CLOUD_PROJECT;
  console.error(
    JSON.stringify({
      severity: "ERROR",
      message: `${e.name ?? "Error"}: ${e.message}`,
      component: "console",
      "@type": "type.googleapis.com/google.devtools.clouderrorreporting.v1beta1.ReportedErrorEvent",
      stack_trace: e.stack,
      digest: e.digest,
      code: e.code,
      requestId: e.requestId,
      httpRequest: { requestMethod: request.method, requestUrl: request.path },
      route: context.routePath,
      routeType: context.routeType,
      ...(trace && project ? { "logging.googleapis.com/trace": `projects/${project}/traces/${trace}` } : {}),
    }),
  );
}
