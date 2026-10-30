"use client";

import { CheckCircle2 } from "lucide-react";
import { useActionState, useState } from "react";
import { requestAccessAction, type RequestAccessState } from "@/app/(public)/actions";
import { cn } from "@/lib/cn";
import { HONEYPOT, LIMITS, validateAccessRequest, type FieldErrors } from "@/lib/waitlist";
import { Button, Field, Input, inputClass } from "./ui";

/**
 * Early-access request. A plain form post, so it works before (or without) JavaScript; with it,
 * fields are checked as they're left. Signed in, the email is the account's and isn't editable.
 */
export function RequestAccessForm({ accountEmail }: { accountEmail?: string }) {
  const [state, action, pending] = useActionState<RequestAccessState, FormData>(requestAccessAction, { status: "idle" });
  const [errors, setErrors] = useState<FieldErrors>({});
  const shown: FieldErrors = { ...(state.status === "error" ? state.errors : {}), ...errors };
  const values = state.status === "error" ? state.values : undefined;

  if (state.status === "sent") {
    return (
      <div role="status" className="flex gap-2.5 rounded-lg border border-border bg-surface p-4">
        <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-good" aria-hidden />
        <div>
          <p className="font-medium">Thanks, you&apos;re on the list.</p>
          <p className="mt-0.5 text-fg-muted">
            Requests are reviewed by hand. Once yours is approved, sign in with the Google account for{" "}
            {accountEmail ?? "the email you gave"} and your workspace will be ready.
          </p>
        </div>
      </div>
    );
  }

  // Check one field when it's left, so errors appear where the person is looking.
  const check = (field: keyof FieldErrors) => (e: React.FocusEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    const form = e.currentTarget.form;
    if (!form || !e.currentTarget.value) return;
    const f = new FormData(form);
    const all = validateAccessRequest({
      email: accountEmail ?? String(f.get("email") ?? ""),
      name: String(f.get("name") ?? ""),
      useCase: String(f.get("useCase") ?? ""),
    });
    setErrors((prev) => ({ ...prev, [field]: all[field] }));
  };

  return (
    <form action={action} noValidate className="flex flex-col gap-4" onSubmit={() => setErrors({})}>
      {accountEmail ? (
        <p className="text-fg-muted">
          Requesting access for <span className="font-medium text-fg">{accountEmail}</span>.
        </p>
      ) : (
        <Field label="Work email" htmlFor="ra-email" error={shown.email}>
          <Input id="ra-email" name="email" type="email" autoComplete="email" required maxLength={LIMITS.email} defaultValue={values?.email} onBlur={check("email")} />
        </Field>
      )}
      <Field label="Name" htmlFor="ra-name" error={shown.name}>
        <Input id="ra-name" name="name" autoComplete="name" required maxLength={LIMITS.name} defaultValue={values?.name} onBlur={check("name")} />
      </Field>
      <Field label="What will you build?" htmlFor="ra-use" error={shown.useCase} help="A sentence or two: the app, who uses it, and the data it needs.">
        <textarea
          id="ra-use"
          name="useCase"
          required
          rows={3}
          maxLength={LIMITS.useCase}
          defaultValue={values?.useCase}
          onBlur={check("useCase")}
          className={cn(inputClass, "h-auto min-h-[76px] resize-y py-2")}
        />
      </Field>
      {/* Honeypot: hidden from people and assistive tech; bots that fill every field reveal themselves. */}
      <div aria-hidden="true" className="absolute -left-[9999px] h-px w-px overflow-hidden">
        <label htmlFor="ra-hp">Website</label>
        <input id="ra-hp" name={HONEYPOT} type="text" tabIndex={-1} autoComplete="off" />
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" variant="primary" pending={pending}>
          {pending ? "Sending…" : "Request access"}
        </Button>
        {state.status === "error" && state.message && (
          <p role="alert" className="text-[12px] text-critical">
            {state.message}
          </p>
        )}
      </div>
    </form>
  );
}
