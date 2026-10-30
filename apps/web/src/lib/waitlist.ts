// The early-access request form's rules, shared by the form (instant feedback) and the server
// action (the check that counts). The API validates again.

export const LIMITS = { name: 100, useCase: 1000, email: 254 } as const;

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
