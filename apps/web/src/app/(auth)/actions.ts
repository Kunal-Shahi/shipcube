"use server";

import { redirect } from "next/navigation";
import { auth } from "@/lib/auth";
import { ApiError } from "@/lib/errors";
import { data } from "@/lib/server/data";
import { TERMS_VERSION } from "@/lib/legal";
import { log } from "@/lib/server/log";
import { validateAccessRequest, type SubmitResult } from "@/lib/waitlist";

export async function signOut() {
  await auth.signOut();
  redirect("/sign-in");
}

/**
 * The one-click request on /waitlist, for someone signed in without a workspace. Server-side with
 * their session, so the API uses the verified email (and no cross-origin call is needed).
 */
export async function requestAccessSignedInAction(input: { name: string; useCase: string; consent: boolean }): Promise<SubmitResult> {
  const session = await auth.getSession();
  if (!session) return { ok: false, message: "Your session ended. Sign in again." };
  const errors = validateAccessRequest({
    email: session.user.email,
    name: input.name ?? "",
    useCase: input.useCase ?? "",
    consent: input.consent === true,
  });
  const first = errors.name ?? errors.useCase ?? errors.consent;
  if (first) return { ok: false, message: first };
  try {
    await (await data()).requestAccessSignedIn({ name: input.name.trim(), useCase: input.useCase.trim(), termsVersion: TERMS_VERSION });
    return { ok: true };
  } catch (e) {
    log("WARNING", "signed-in waitlist request failed", { code: e instanceof ApiError ? e.code : undefined, error: String(e) });
    if (e instanceof ApiError && e.code === "invalid_argument") return { ok: false, message: e.message };
    // Signed-in requests have their own limits: per account, and in total.
    if (e instanceof ApiError && e.code === "rate_limited") return { ok: false, message: "Too many requests right now. Try again in a little while." };
    return { ok: false, message: "We couldn't save your request just now. Try again in a minute." };
  }
}
