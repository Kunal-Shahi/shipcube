import "server-only";

import { cookies } from "next/headers";
import { ONBOARDED_COOKIE, SESSION_COOKIE } from "./types";

const secure = process.env.NODE_ENV === "production";

export async function setSessionCookie(value: string, expires: Date) {
  (await cookies()).set(SESSION_COOKIE, value, { httpOnly: true, secure, sameSite: "lax", path: "/", expires });
}

export async function readSessionCookie() {
  return (await cookies()).get(SESSION_COOKIE)?.value ?? null;
}

export async function clearSessionCookies() {
  const c = await cookies();
  c.delete(SESSION_COOKIE);
}

/** A UX preference only (skip onboarding next time); it grants nothing. */
export async function markOnboarded() {
  (await cookies()).set(ONBOARDED_COOKIE, "1", { httpOnly: true, secure, sameSite: "lax", path: "/", maxAge: 365 * 86_400 });
}

export async function isOnboarded() {
  return (await cookies()).get(ONBOARDED_COOKIE)?.value === "1";
}
