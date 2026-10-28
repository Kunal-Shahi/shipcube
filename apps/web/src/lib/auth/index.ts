import "server-only";

import { redirect } from "next/navigation";
import { config } from "../server/config";
import { setApiCredential } from "../server/http";
import { identityPlatformAuth } from "./identity-platform";
import { mockAuth } from "./mock";
import type { AuthProvider, Session } from "./types";

function pick(): AuthProvider {
  return config().auth === "mock" ? mockAuth : identityPlatformAuth;
}

/** Resolved lazily so a missing setting fails the request with a clear log, not the build. */
export const auth: AuthProvider = {
  get id() {
    return pick().id;
  },
  getSession: () => pick().getSession(),
  createSession: (t, i) => pick().createSession(t, i),
  completeOnboarding: () => pick().completeOnboarding(),
  signOut: () => pick().signOut(),
  apiCredential: () => pick().apiCredential(),
};

setApiCredential(() => auth.apiCredential());

export async function requireSession(): Promise<Session> {
  const session = await auth.getSession();
  if (!session) redirect("/sign-in");
  return session;
}

/** Browser config for the Firebase JS SDK, read at request time so one build serves every environment. */
export function webAuthConfig() {
  const c = config();
  if (c.auth === "mock") return null;
  if (c.authEmulatorHost) {
    // The emulator accepts any API key; a demo- project keeps the SDK away from Google.
    return { apiKey: "demo-key", authDomain: `${c.identityProject}.firebaseapp.com`, projectId: c.identityProject ?? "", emulatorUrl: c.authEmulatorPublicUrl ?? undefined };
  }
  return {
    apiKey: process.env.FIREBASE_WEB_API_KEY ?? "",
    authDomain: process.env.FIREBASE_AUTH_DOMAIN ?? `${c.identityProject}.firebaseapp.com`,
    projectId: c.identityProject ?? "",
    tenantId: process.env.IDENTITY_PLATFORM_TENANT_ID || undefined,
  };
}

export type { AuthProvider, Session, User } from "./types";
