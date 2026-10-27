import { cookies } from "next/headers";
import { SESSION_COOKIE, type AuthProvider, type Session } from "./types";

// Development-only provider. It stores a fake session in a cookie so the console
// can be clicked through without Google credentials. Never enable it in production.

const DEMO_USER = {
  id: "usr_01JB8K",
  email: "kunal@acme-analytics.com",
  name: "Kunal Shahi",
  hostedDomain: "acme-analytics.com",
};

function encode(s: Session) {
  return Buffer.from(JSON.stringify(s)).toString("base64url");
}

function decode(v: string): Session | null {
  try {
    return JSON.parse(Buffer.from(v, "base64url").toString()) as Session;
  } catch {
    return null;
  }
}

async function write(session: Session) {
  (await cookies()).set(SESSION_COOKIE, encode(session), {
    httpOnly: true,
    sameSite: "lax",
    path: "/",
    secure: process.env.NODE_ENV === "production",
    expires: new Date(session.expiresAt),
  });
}

export const mockAuth: AuthProvider = {
  id: "mock",

  async getSession() {
    const raw = (await cookies()).get(SESSION_COOKIE)?.value;
    return raw ? decode(raw) : null;
  },

  async signInWithGoogle({ intent, returnTo }) {
    const existing = await this.getSession();
    const onboarded = intent === "sign-in" ? true : (existing?.onboarded ?? false);
    await write({
      user: DEMO_USER,
      onboarded,
      expiresAt: new Date(Date.now() + 7 * 24 * 3600_000).toISOString(),
    });
    return { redirectTo: onboarded ? (returnTo ?? "/apps") : "/onboarding" };
  },

  async completeOnboarding() {
    const s = await this.getSession();
    if (s) await write({ ...s, onboarded: true });
  },

  async signOut() {
    (await cookies()).delete(SESSION_COOKIE);
  },
};
