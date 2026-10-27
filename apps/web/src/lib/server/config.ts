import "server-only";

// Every environment-specific setting, read once from the environment. One build artifact
// is promoted across environments, so nothing here is baked in at build time except the
// NEXT_PUBLIC_ values the browser needs (see README "Configuration").

export type AuthProviderId = "identity-platform" | "mock";

export interface Config {
  /** Local development only: serve typed mock data and allow the mock sign-in. */
  devMode: boolean;
  /** Base URL of the control-plane API, e.g. https://api.shipcube.dev. */
  apiUrl: string | null;
  /** Per-request timeout for API calls. */
  apiTimeoutMs: number;
  auth: AuthProviderId;
  /** Google Cloud project that hosts Identity Platform. */
  identityProject: string | null;
  /** Session cookie lifetime. Identity Platform allows 5 minutes to 14 days. */
  sessionDays: number;
  /** A console session token for a local API (sc_ses_…), used only with the mock sign-in. */
  devApiSession: string | null;
  onCloudRun: boolean;
}

let cached: Config | null = null;

export function config(): Config {
  if (cached) return cached;
  const env = process.env;
  const devMode = env.DEV_MODE === "1" || env.DEV_MODE === "true";
  const onCloudRun = Boolean(env.K_SERVICE);
  const auth = (env.AUTH_PROVIDER || (devMode ? "mock" : "identity-platform")) as AuthProviderId;
  if (auth !== "mock" && auth !== "identity-platform") throw new Error(`Unknown AUTH_PROVIDER "${auth}".`);

  const c: Config = {
    devMode,
    apiUrl: env.SHIPCUBE_API_URL?.replace(/\/+$/, "") || null,
    apiTimeoutMs: Number(env.SHIPCUBE_API_TIMEOUT_MS ?? 10_000),
    auth,
    identityProject: env.IDENTITY_PLATFORM_PROJECT || env.NEXT_PUBLIC_FIREBASE_PROJECT_ID || null,
    sessionDays: Number(env.SESSION_DAYS ?? 5),
    devApiSession: env.SHIPCUBE_DEV_API_SESSION || null,
    onCloudRun,
  };

  // Mock data and the mock sign-in must never reach a deployed service.
  if (onCloudRun && (c.devMode || c.auth === "mock")) {
    throw new Error("DEV_MODE and AUTH_PROVIDER=mock are refused on Cloud Run (K_SERVICE is set).");
  }
  if (c.auth === "mock" && !c.devMode) {
    throw new Error("AUTH_PROVIDER=mock requires DEV_MODE=1.");
  }
  if (!c.devMode && !c.apiUrl) {
    throw new Error("SHIPCUBE_API_URL is required unless DEV_MODE=1.");
  }
  if (c.auth === "identity-platform" && !c.identityProject) {
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
