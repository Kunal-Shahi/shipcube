import "server-only";

// Every environment-specific setting, read once from the environment. One build artifact
// is promoted across environments, so nothing here is baked in at build time except the
// NEXT_PUBLIC_ values the browser needs (see README "Configuration").

/** identity-platform in production; firebase-emulator (the Firebase Auth emulator) and mock are local only. */
export type AuthProviderId = "identity-platform" | "firebase-emulator" | "mock";

export interface Config {
  /** Local development only: serve typed mock data and allow the mock sign-in. */
  devMode: boolean;
  /** Base URL of the control-plane API (the shipcube-mcp Cloud Run service in production). */
  apiUrl: string | null;
  /** The MCP endpoint users paste into their agent, e.g. https://shipcube-mcp-<n>.<region>.run.app/mcp. */
  mcpUrl: string;
  /** Per-request timeout for API calls. */
  apiTimeoutMs: number;
  auth: AuthProviderId;
  /** Google Cloud project that hosts Identity Platform. */
  identityProject: string | null;
  /** host:port of the Firebase Auth emulator as the server reaches it, only with AUTH_PROVIDER=firebase-emulator. */
  authEmulatorHost: string | null;
  /** The emulator as the browser reaches it (differs inside Docker Compose). */
  authEmulatorPublicUrl: string | null;
  /** Session cookie lifetime. Identity Platform allows 5 minutes to 14 days. */
  sessionDays: number;
  /** A console session token for a local API (sc_ses_…), used only with the mock sign-in. */
  devApiSession: string | null;
  onCloudRun: boolean;
}

let cached: Config | null = null;

/** The local MCP server (services/mcp listens on 8080). Used only when DEV_MODE=1 and SHIPCUBE_MCP_URL is unset. */
export const DEV_MCP_URL = "http://localhost:8080/mcp";
/** The emulator's default address, and a demo- project, which the emulator never connects to Google for. */
export const DEV_AUTH_EMULATOR_HOST = "127.0.0.1:9099";
export const EMULATOR_PROJECT = "demo-shipcube";
/** Shared with the middleware's CSP, which can't import this server-only module's config(). */
export function emulatorPublicUrl(env: Record<string, string | undefined>) {
  return (env.FIREBASE_AUTH_EMULATOR_PUBLIC_URL || `http://${env.FIREBASE_AUTH_EMULATOR_HOST || DEV_AUTH_EMULATOR_HOST}`).replace(/\/+$/, "");
}
const PROVIDERS: AuthProviderId[] = ["identity-platform", "firebase-emulator", "mock"];

/** Set by Cloud Run on every service and job: their presence means production (#29). */
export const CLOUD_RUN_VARS = ["K_SERVICE", "K_REVISION", "CLOUD_RUN_JOB"] as const;
const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]", "host.docker.internal"]);

/** A URL the browser or server would call that is public and encrypted. */
function isPublicHttps(raw: string): boolean {
  try {
    const u = new URL(raw);
    const h = u.hostname.toLowerCase();
    return u.protocol === "https:" && !LOCAL_HOSTS.has(h) && !h.endsWith(".localhost") && !h.endsWith(".local");
  } catch {
    return false;
  }
}

export function config(): Config {
  if (cached) return cached;
  const env = process.env;
  // SHIPCUBE_DEV_MODE is the one switch name (#29); DEV_MODE still works, with a warning.
  const devMode = [env.SHIPCUBE_DEV_MODE, env.DEV_MODE].some((v) => v === "1" || v === "true");
  if (devMode && !(env.SHIPCUBE_DEV_MODE === "1" || env.SHIPCUBE_DEV_MODE === "true")) {
    console.warn("DEV_MODE=1 is deprecated; use SHIPCUBE_DEV_MODE=1");
  }
  const onCloudRun = CLOUD_RUN_VARS.some((k) => Boolean(env[k]));
  const auth = (env.AUTH_PROVIDER || (devMode ? "mock" : "identity-platform")) as AuthProviderId;
  if (!PROVIDERS.includes(auth)) throw new Error(`Unknown AUTH_PROVIDER "${auth}".`);
  const emulator = auth === "firebase-emulator";

  const c: Config = {
    devMode,
    apiUrl: env.SHIPCUBE_API_URL?.replace(/\/+$/, "") || null,
    mcpUrl: env.SHIPCUBE_MCP_URL?.trim() || (devMode ? DEV_MCP_URL : ""),
    apiTimeoutMs: Number(env.SHIPCUBE_API_TIMEOUT_MS ?? 10_000),
    auth,
    identityProject: env.IDENTITY_PLATFORM_PROJECT || env.NEXT_PUBLIC_FIREBASE_PROJECT_ID || (emulator ? EMULATOR_PROJECT : null),
    authEmulatorHost: emulator ? env.FIREBASE_AUTH_EMULATOR_HOST || DEV_AUTH_EMULATOR_HOST : null,
    authEmulatorPublicUrl: emulator ? emulatorPublicUrl(env) : null,
    sessionDays: Number(env.SESSION_DAYS ?? 5),
    devApiSession: env.SHIPCUBE_DEV_API_SESSION || null,
    onCloudRun,
  };

  // Mock data, the mock sign-in and the auth emulator must never reach a deployed service.
  if (onCloudRun && (c.devMode || c.auth !== "identity-platform")) {
    throw new Error(`DEV_MODE and AUTH_PROVIDER=${c.auth} are refused on Cloud Run (K_SERVICE is set).`);
  }
  if (onCloudRun) {
    if (c.devApiSession) throw new Error("SHIPCUBE_DEV_API_SESSION (a local console session) is refused on Cloud Run.");
    for (const [name, value] of [["SHIPCUBE_API_URL", c.apiUrl], ["SHIPCUBE_MCP_URL", c.mcpUrl]] as const) {
      if (value && !isPublicHttps(value)) throw new Error(`${name} "${value}" must be a public https URL on Cloud Run.`);
    }
  }
  if (c.auth !== "identity-platform" && !c.devMode) {
    throw new Error(`AUTH_PROVIDER=${c.auth} requires DEV_MODE=1.`);
  }
  // With this variable set, the Admin SDK accepts unsigned emulator tokens, whatever the provider.
  if (env.FIREBASE_AUTH_EMULATOR_HOST && !c.devMode) {
    throw new Error("FIREBASE_AUTH_EMULATOR_HOST is refused outside DEV_MODE: the Admin SDK would accept unsigned tokens.");
  }
  if (!c.devMode && !c.apiUrl) {
    throw new Error("SHIPCUBE_API_URL is required unless DEV_MODE=1.");
  }
  // Users paste their PAT next to this URL, so it must be ours and encrypted. There is no
  // public default: a wrong or unowned domain would collect those tokens.
  if (!c.mcpUrl) throw new Error("SHIPCUBE_MCP_URL is required unless DEV_MODE=1.");
  let mcp: URL;
  try {
    mcp = new URL(c.mcpUrl);
  } catch {
    throw new Error(`SHIPCUBE_MCP_URL "${c.mcpUrl}" isn't a URL.`);
  }
  if (!c.devMode && env.NODE_ENV === "production" && mcp.protocol !== "https:") {
    throw new Error("SHIPCUBE_MCP_URL must use https in production.");
  }
  if (c.auth !== "mock" && !c.identityProject) {
    throw new Error("IDENTITY_PLATFORM_PROJECT (or NEXT_PUBLIC_FIREBASE_PROJECT_ID) is required for Identity Platform sign-in.");
  }
  if (!(c.sessionDays > 0 && c.sessionDays <= 14)) throw new Error("SESSION_DAYS must be between 1 and 14.");

  cached = c;
  return c;
}

/** Mock data only in DEV_MODE, and only when no API URL is configured (so local dev can use a local API). */
export function mockDataEnabled() {
  const c = config();
  return c.devMode && !c.apiUrl;
}

/** For tests. */
export function resetConfig() {
  cached = null;
}
