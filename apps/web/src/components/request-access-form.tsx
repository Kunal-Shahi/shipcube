"use client";

import { CheckCircle2 } from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/cn";
import { HONEYPOT, LIMITS, submitAccessRequest, validateAccessRequest, type FieldErrors } from "@/lib/waitlist";
import { Button, Field, Input, inputClass } from "./ui";

/**
 * Early-access request, posted from the browser straight to the API (its rate limit is per
 * visitor IP). Signed in, the email is the account's and isn't editable. With no API configured
 * (DEV_MODE mock data), the request is only pretended.
 */
export function RequestAccessForm({ apiBase, accountEmail }: { apiBase: string | null; accountEmail?: string }) {
  const [errors, setErrors] = useState<FieldErrors>({});
  const [message, setMessage] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [sent, setSent] = useState(false);

  if (sent) {
    return (
      <div role="status" className="flex gap-2.5 rounded-lg border border-border bg-surface p-4">
        <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-good" aria-hidden />
        <div>
          <p className="font-medium">Thanks, you&apos;re on the list.</p>
          <p className="mt-0.5 text-fg-muted">
            A person reviews every request. Once yours is approved, sign in with the Google account for{" "}
            {accountEmail ?? "the email you gave"}, and your workspace will be there.
          </p>
        </div>
      </div>
    );
  }

  const read = (form: HTMLFormElement) => {
    const f = new FormData(form);
    return {
      email: accountEmail ?? String(f.get("email") ?? ""),
      name: String(f.get("name") ?? ""),
      useCase: String(f.get("useCase") ?? ""),
      honeypot: String(f.get(HONEYPOT) ?? ""),
    };
  };

  // Check a field when it's left, so the error appears where the person is looking.
  const check = (field: keyof FieldErrors) => (e: React.FocusEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    const form = e.currentTarget.form;
    if (!form || !e.currentTarget.value) return;
    // Read now: React clears currentTarget before a state updater runs.
    const error = validateAccessRequest(read(form))[field];
    setErrors((prev) => ({ ...prev, [field]: error }));
  };

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const fields = read(e.currentTarget);
    const found = validateAccessRequest(fields);
    setErrors(found);
    setMessage(null);
    if (Object.keys(found).length) {
      e.currentTarget.querySelector<HTMLElement>("[aria-invalid=true]")?.focus();
      return;
    }
    setPending(true);
    const r = apiBase ? await submitAccessRequest(apiBase, fields) : await new Promise<{ ok: true }>((ok) => setTimeout(() => ok({ ok: true }), 400));
    setPending(false);
    if (r.ok) setSent(true);
    else setMessage(r.message);
  }

  return (
    <form onSubmit={onSubmit} noValidate className="flex flex-col gap-4">
      {accountEmail ? (
        <p className="text-fg-muted">
          Requesting access for <span className="font-medium text-fg">{accountEmail}</span>.
        </p>
      ) : (
        <Field label="Work email" htmlFor="ra-email" error={errors.email}>
          <Input id="ra-email" name="email" type="email" autoComplete="email" required maxLength={LIMITS.email} onBlur={check("email")} />
        </Field>
      )}
      <Field label="Name" htmlFor="ra-name" error={errors.name}>
        <Input id="ra-name" name="name" autoComplete="name" required maxLength={LIMITS.name} onBlur={check("name")} />
      </Field>
      <Field label="What will you build?" htmlFor="ra-use" error={errors.useCase} help="A sentence or two: the app, who uses it, and the data it needs.">
        <textarea
          id="ra-use"
          name="useCase"
          required
          rows={3}
          maxLength={LIMITS.useCase}
          onBlur={check("useCase")}
          className={cn(inputClass, "h-auto min-h-[76px] resize-y py-2")}
        />
      </Field>
      {/* Honeypot: hidden from people and assistive tech. The API quietly drops requests that fill it. */}
      <div aria-hidden="true" className="absolute -left-[9999px] h-px w-px overflow-hidden">
        <label htmlFor="ra-hp">Website</label>
        <input id="ra-hp" name={HONEYPOT} type="text" tabIndex={-1} autoComplete="off" defaultValue="" />
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" variant="primary" pending={pending}>
          {pending ? "Sending…" : "Request access"}
        </Button>
        {message && (
          <p role="alert" className="text-[12px] text-critical">
            {message}
          </p>
        )}
      </div>
    </form>
  );
}
