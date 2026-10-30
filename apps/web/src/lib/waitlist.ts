// The early-access request form: its rules and the request itself. The browser posts straight to
// the API (POST /v1/waitlist), because the API rate-limits per visitor IP; it validates again.

export const LIMITS = { name: 100, useCase: 500, email: 254 } as const;

/** The honeypot's field name. People never see or fill it; form-filling bots usually do. */
export const HONEYPOT = "website";

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export type AccessRequestFields = { email: string; name: string; useCase: string };
export type FieldErrors = Partial<Record<keyof AccessRequestFields, string>>;

export function validateAccessRequest(f: AccessRequestFields): FieldErrors {
  const e: FieldErrors = {};
  const email = f.email.trim();
  if (!email) e.email = "Enter your work email.";
  else if (email.length > LIMITS.email || !EMAIL.test(email)) e.email = "That doesn't look like an email address.";
  if (!f.name.trim()) e.name = "Enter your name.";
  else if (f.name.trim().length > LIMITS.name) e.name = `Keep it under ${LIMITS.name} characters.`;
  if (!f.useCase.trim()) e.useCase = "Say a little about what you'd deploy.";
  else if (f.useCase.trim().length > LIMITS.useCase) e.useCase = `Keep it under ${LIMITS.useCase} characters.`;
  return e;
}

export type SubmitResult = { ok: true } | { ok: false; message: string };

/**
 * Sends the request. The API answers 202 for every valid submission (new, duplicate, already
 * decided, or a filled honeypot), so the person always sees the same "Thanks".
 */
export async function submitAccessRequest(
  apiBase: string,
  f: AccessRequestFields & { honeypot: string },
  fetchImpl: typeof fetch = fetch,
): Promise<SubmitResult> {
  let res: Response;
  try {
    res = await fetchImpl(`${apiBase}/v1/waitlist`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: f.email.trim(), name: f.name.trim(), use_case: f.useCase.trim(), [HONEYPOT]: f.honeypot }),
      credentials: "omit",
    });
  } catch {
    return { ok: false, message: "Couldn't reach ShipCube. Check your connection and try again." };
  }
  if (res.status === 202 || res.ok) return { ok: true };
  const body = (await res.json().catch(() => ({}))) as { error?: { code?: string; message?: string; retry_after?: number } };
  if (res.status === 429) {
    const s = Number(body.error?.retry_after ?? res.headers.get("Retry-After") ?? 0);
    const wait = s > 90 ? `${Math.ceil(s / 60)} minutes` : s > 0 ? `${s} seconds` : "a few minutes";
    return { ok: false, message: `Too many requests from here. Try again in ${wait}.` };
  }
  if (res.status === 400 && body.error?.message) return { ok: false, message: body.error.message };
  return { ok: false, message: "We couldn't save your request just now. Try again in a minute." };
}
