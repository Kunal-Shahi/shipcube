import "server-only";

import { config } from "../server/config";
import { clearSessionCookies, isOnboarded, markOnboarded, readSessionCookie, setSessionCookie } from "./cookies";
import type { AuthProvider, Session } from "./types";

// DEV_MODE only (config() refuses it otherwise, and always on Cloud Run). Stores an unsigned
// demo session in a cookie so the console can be clicked through without Google.

const DEMO_USER = {
  id: "usr_dev",
  email: "kunal@example.com",
  name: "Kunal Shahi",
  hostedDomain: "example.com",
};

export const mockAuth: AuthProvider = {
  id: "mock",

  async getSession() {
    const raw = await readSessionCookie();
    if (!raw) return null;
    try {
      const s = JSON.parse(Buffer.from(raw, "base64url").toString()) as Omit<Session, "onboarded">;
      if (new Date(s.expiresAt).getTime() < Date.now()) return null;
      return { ...s, onboarded: await isOnboarded() };
    } catch {
      return null;
    }
  },

  async createSession(_idToken, intent) {
    const expires = new Date(Date.now() + 7 * 86_400_000);
    const s = { user: DEMO_USER, expiresAt: expires.toISOString() };
    await setSessionCookie(Buffer.from(JSON.stringify(s)).toString("base64url"), expires);
    if (intent === "sign-in") await markOnboarded();
    return { next: intent === "sign-up" && !(await isOnboarded()) ? "onboarding" : "console" };
  },

  async completeOnboarding() {
    await markOnboarded();
  },

  async signOut() {
    await clearSessionCookies();
  },

  async apiCredential() {
    // A console session token (sc_ses_…) for a local control-plane API, if one is configured.
    return config().devApiSession;
  },
};
