import "server-only";

import { cookies } from "next/headers";
import { ONBOARDED_COOKIE, SESSION_COOKIE } from "./types";

const secure = process.env.NODE_ENV === "production";

// Firebase Hosting (shipcube.web.app) forwards only the `__session` cookie to Cloud Run, so the
// "finished onboarding" hint rides on it as a suffix the server strips before verifying the session.
// It is a UX preference only (skip the onboarding screen); it grants nothing.
export const ONBOARDED_SUFFIX = "~onboarded";

export function splitSessionValue(raw: string | undefined | null): { session: string | null; onboarded: boolean } {
  if (!raw) return { session: null, onboarded: false };
  if (raw.endsWith(ONBOARDED_SUFFIX)) return { session: raw.slice(0, -ONBOARDED_SUFFIX.length) || null, onboarded: true };
  return { session: raw, onboarded: false };
}

export async function setSessionCookie(value: string, expires: Date, onboarded = false) {
  (await cookies()).set(SESSION_COOKIE, onboarded ? value + ONBOARDED_SUFFIX : value, {
    httpOnly: true, secure, sameSite: "lax", path: "/", expires,
  });
}

export async function readSessionCookie() {
  return splitSessionValue((await cookies()).get(SESSION_COOKIE)?.value).session;
}

export async function clearSessionCookies() {
  const c = await cookies();
  c.delete(SESSION_COOKIE);
}

/** Remember that onboarding is done: on the session cookie (works behind Firebase Hosting) when its
 * expiry is known, and in the legacy cookie for the run.app address. */
export async function markOnboarded(sessionExpires?: Date) {
  const c = await cookies();
  c.set(ONBOARDED_COOKIE, "1", { httpOnly: true, secure, sameSite: "lax", path: "/", maxAge: 365 * 86_400 });
  const { session, onboarded } = splitSessionValue(c.get(SESSION_COOKIE)?.value);
  if (session && !onboarded && sessionExpires) await setSessionCookie(session, sessionExpires, true);
}

export async function isOnboarded() {
  const c = await cookies();
  return splitSessionValue(c.get(SESSION_COOKIE)?.value).onboarded || c.get(ONBOARDED_COOKIE)?.value === "1";
}
