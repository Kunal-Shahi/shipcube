"use server";

// The landing page's "Request access" form. Public: no session needed. It also works without
// JavaScript (a plain form post), returning the next state of the form.

import { headers } from "next/headers";
import { auth } from "@/lib/auth";
import { ApiError } from "@/lib/errors";
import { data } from "@/lib/server/data";
import { log } from "@/lib/server/log";
import { HONEYPOT, validateAccessRequest, type AccessRequestFields, type FieldErrors } from "@/lib/waitlist";

export type RequestAccessState =
  | { status: "idle" }
  | { status: "sent" }
  | { status: "error"; errors: FieldErrors; message?: string; values: AccessRequestFields };

const str = (v: FormDataEntryValue | null) => (typeof v === "string" ? v : "");

export async function requestAccessAction(_prev: RequestAccessState, form: FormData): Promise<RequestAccessState> {
  const session = await auth.getSession().catch(() => null);
  const values: AccessRequestFields = {
    // Signed in, the request is for the account's own email (the API uses the verified one too).
    email: session?.user.email ?? str(form.get("email")).trim(),
    name: str(form.get("name")).trim(),
    useCase: str(form.get("useCase")).trim(),
  };

  // Bots fill the hidden field. Answer exactly as for a real request, and send nothing.
  if (str(form.get(HONEYPOT))) {
    log("INFO", "waitlist honeypot tripped");
    return { status: "sent" };
  }

  const errors = validateAccessRequest(values);
  if (Object.keys(errors).length) return { status: "error", errors, values };

  const ip = (await headers()).get("x-forwarded-for")?.split(",")[0]?.trim() || undefined;
  try {
    await (await data()).requestAccess(values, ip);
    return { status: "sent" };
  } catch (e) {
    const err = e instanceof ApiError ? e : null;
    log(err && err.status < 500 ? "WARNING" : "ERROR", "waitlist request failed", { code: err?.code, requestId: err?.requestId, error: String(e) });
    const message =
      err?.code === "rate_limited"
        ? "Too many requests from here. Try again in a few minutes."
        : err?.code === "invalid_argument"
          ? err.message
          : "We couldn't save your request just now. Try again in a minute.";
    return { status: "error", errors: {}, message, values };
  }
}
