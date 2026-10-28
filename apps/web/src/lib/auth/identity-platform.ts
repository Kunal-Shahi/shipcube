import "server-only";

import { getApps, initializeApp, type App } from "firebase-admin/app";
import { getAuth } from "firebase-admin/auth";
import { config } from "../server/config";
import { log } from "../server/log";
import { clearSessionCookies, isOnboarded, markOnboarded, readSessionCookie, setSessionCookie } from "./cookies";
import { AuthError, type AuthProvider } from "./types";

// Identity Platform (Google provider). The browser signs in with the Firebase JS SDK and
// posts the ID token to /api/auth/session, which turns it into an httpOnly session cookie
// with the Admin SDK. The Admin SDK uses Application Default Credentials: on Cloud Run,
// the service's own account (it needs roles/firebaseauth.admin on the Identity Platform project).
//
// Locally, the same adapter runs against the Firebase Auth emulator (AUTH_PROVIDER=firebase-emulator):
// the same popup, ID token and session cookie flow, with the emulator in place of Google.

/** Sign-ins older than this can't mint a session, so a stolen ID token has a short life. */
const MAX_SIGN_IN_AGE_S = 5 * 60;

let app: App | null = null;
function admin() {
  const c = config();
  // The Admin SDK switches to the emulator when this is set. config() refuses it outside DEV_MODE.
  if (c.authEmulatorHost) process.env.FIREBASE_AUTH_EMULATOR_HOST = c.authEmulatorHost;
  app ??= getApps()[0] ?? initializeApp({ projectId: c.identityProject ?? undefined });
  return getAuth(app);
}

async function verified() {
  const cookie = await readSessionCookie();
  if (!cookie) return null;
  try {
    // checkRevoked: a sign-out anywhere (revokeRefreshTokens) ends this session too.
    return { cookie, claims: await admin().verifySessionCookie(cookie, true) };
  } catch (e) {
    log("INFO", "session cookie rejected", { reason: (e as { code?: string }).code ?? String(e) });
    return null;
  }
}

export const identityPlatformAuth: AuthProvider = {
  get id() {
    return config().auth === "firebase-emulator" ? "firebase-emulator" : "identity-platform";
  },

  async getSession() {
    const v = await verified();
    if (!v) return null;
    const c = v.claims;
    const email = c.email ?? "";
    return {
      user: {
        id: c.uid,
        email,
        name: (c.name as string | undefined) ?? email,
        avatarUrl: c.picture,
        hostedDomain: email.includes("@") ? email.split("@")[1] : undefined,
      },
      expiresAt: new Date(c.exp * 1000).toISOString(),
      onboarded: await isOnboarded(),
    };
  },

  async createSession(idToken, intent) {
    let claims;
    try {
      claims = await admin().verifyIdToken(idToken, true);
    } catch (e) {
      log("WARNING", "id token rejected", { reason: (e as { code?: string }).code ?? String(e) });
      throw new AuthError("invalid_token", "Google sign-in didn't complete. Try again.");
    }
    if (Date.now() / 1000 - claims.auth_time > MAX_SIGN_IN_AGE_S) {
      throw new AuthError("stale_sign_in", "That sign-in is too old. Sign in again.");
    }
    if (!claims.email || claims.email_verified === false) {
      throw new AuthError("email_unverified", "Your Google account's email address isn't verified.");
    }
    const days = config().sessionDays;
    const cookie = await admin().createSessionCookie(idToken, { expiresIn: days * 86_400_000 });
    await setSessionCookie(cookie, new Date(Date.now() + days * 86_400_000));
    log("INFO", "session created", { uid: claims.uid });
    if (intent === "sign-in") await markOnboarded();
    return { next: intent === "sign-up" && !(await isOnboarded()) ? "onboarding" : "console" };
  },

  async completeOnboarding() {
    await markOnboarded();
  },

  async signOut() {
    const v = await verified();
    await clearSessionCookies();
    if (v) {
      try {
        await admin().revokeRefreshTokens(v.claims.uid);
      } catch (e) {
        log("WARNING", "revokeRefreshTokens failed", { error: String(e) });
      }
    }
  },

  async apiCredential() {
    const v = await verified();
    if (!v) return null;
    // The API can't verify the emulator's unsigned tokens, so locally the signed-in user calls it
    // with the dev console session (SHIPCUBE_DEV_API_SESSION) when one is configured.
    const c = config();
    if (c.auth === "firebase-emulator" && c.devApiSession) return c.devApiSession;
    // The API verifies this Identity Platform session cookie (a Google-signed JWT).
    return v.cookie;
  },
};
