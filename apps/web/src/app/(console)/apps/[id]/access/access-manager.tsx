"use client";

import { Globe, User, Users } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { ActionErrorText } from "@/components/action-error";
import { Segmented } from "@/components/segmented";
import { Button, EmptyState, Input, Select } from "@/components/ui";
import { newIdempotencyKey, useAction } from "@/components/use-action";
import { cn } from "@/lib/cn";
import { date } from "@/lib/format";
import type { AccessGrant, GrantKind, Role } from "@/lib/types";
import { changeAccessAction } from "../../../actions";

const principalOf = (g: { kind: GrantKind; principal: string }) => `${g.kind}:${g.principal}`;

const KIND: Record<GrantKind, { label: string; icon: typeof User; placeholder: string; noun: string }> = {
  user: { label: "Person", icon: User, placeholder: "name@example.com", noun: "person" },
  group: { label: "Google group", icon: Users, placeholder: "team@example.com", noun: "group" },
  domain: { label: "Domain", icon: Globe, placeholder: "example.com", noun: "domain" },
};

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;
const DOMAIN = /^(?!-)[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}$/i;

function validate(kind: GrantKind, value: string, existing: AccessGrant[]): string | null {
  const v = value.trim().toLowerCase();
  if (!v) return null;
  if (kind === "domain") {
    if (v.includes("@")) return "Enter just the domain, such as example.com. To add one person, choose Person.";
    if (!DOMAIN.test(v)) return "That doesn't look like a domain. Use the part after the @, such as example.com.";
    if (["gmail.com", "googlemail.com", "outlook.com", "yahoo.com"].includes(v))
      return `Sharing with everyone on ${v} would make the app public to millions of accounts. Add people one by one instead.`;
  } else if (!EMAIL.test(v)) {
    return kind === "group" ? "Enter the group's email address, for example team@example.com." : "Enter an email address, for example maria@example.com.";
  }
  if (existing.some((g) => g.principal.toLowerCase() === v)) return `${v} already has access.`;
  return null;
}

export function AccessManager({ appId, initial, owner }: { appId: string; initial: AccessGrant[]; owner: string }) {
  const [grants, setGrants] = useState(initial);
  const [kind, setKind] = useState<GrantKind>("user");
  const [value, setValue] = useState("");
  const [role, setRole] = useState<Role>("viewer");
  const [touched, setTouched] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [approvalUrl, setApprovalUrl] = useState<string | null>(null);
  const change = useAction(changeAccessAction);
  const pending = change.pending;
  const [confirming, setConfirming] = useState<string | null>(null);

  const error = touched ? validate(kind, value, grants) : null;

  function flash(msg: string) {
    setToast(msg);
    setTimeout(() => setToast(null), 3000);
  }

  async function apply(
    body: { add?: { principal: string; role: "viewer" | "editor" }[]; remove?: string[] },
    done: string,
  ): Promise<boolean> {
    setApprovalUrl(null);
    const r = await change.run(appId, body, newIdempotencyKey("access"));
    if (!r) return false;
    setGrants(r.access);
    if (r.approval) {
      setApprovalUrl(r.approval.url);
      flash("Domain sharing waits for approval");
    } else {
      flash(done);
    }
    return true;
  }

  async function grant(e: React.FormEvent) {
    e.preventDefault();
    setTouched(true);
    if (!value.trim() || validate(kind, value, grants)) return;
    const principal = value.trim().toLowerCase();
    const r = role === "editor" ? "editor" : "viewer";
    if (await apply({ add: [{ principal: `${kind}:${principal}`, role: r }] }, `Access granted to ${principal}`)) {
      setValue("");
      setTouched(false);
    }
  }

  return (
    <div className="flex flex-col">
      <form onSubmit={grant} className="flex flex-col gap-3 border-b border-border p-4" noValidate>
        <div className="flex flex-wrap gap-2">
          <Segmented
            label="Grant to"
            value={kind}
            onChange={(k) => {
              setKind(k);
              setTouched(false);
            }}
            options={(Object.keys(KIND) as GrantKind[]).map((k) => {
              const Icon = KIND[k].icon;
              return { value: k, label: KIND[k].label, icon: <Icon className="size-3.5" aria-hidden /> };
            })}
          />
        </div>
        <div className="flex flex-wrap items-start gap-2">
          <div className="min-w-[220px] flex-1">
            <label htmlFor="grant-principal" className="sr-only">
              {KIND[kind].label}
            </label>
            <Input
              id="grant-principal"
              placeholder={KIND[kind].placeholder}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              onBlur={() => value && setTouched(true)}
              aria-invalid={!!error}
              aria-describedby="grant-help"
              className={cn(error && "border-critical")}
              autoComplete="off"
            />
          </div>
          <label htmlFor="grant-role" className="sr-only">Role</label>
          <Select id="grant-role" value={role} onChange={(e) => setRole(e.target.value as Role)} className="w-[120px]">
            <option value="viewer">Can view</option>
            <option value="editor">Can edit</option>
          </Select>
          <Button type="submit" variant="primary" pending={pending}>
            Grant access
          </Button>
        </div>
        <ActionErrorText error={change.error} message={change.message} />
        {approvalUrl && (
          <p role="status" className="text-[12px] text-warning">
            Sharing with a whole domain needs approval first.{" "}
            <Link href={approvalUrl} className="text-accent underline">Review the approval</Link>
          </p>
        )}
        <p id="grant-help" className={cn("text-[12px]", error ? "text-critical" : "text-fg-muted")}>
          {error ??
            (kind === "domain"
              ? "Everyone who signs in with a Google account on this domain can open the app."
              : kind === "group"
                ? "Membership is checked against Google Groups each time someone opens the app."
                : "They sign in with Google. Nobody else can open the app.")}
        </p>
      </form>

      {grants.length <= 1 ? (
        <EmptyState
          title="Only you can open this app"
          body="Add a person, a Google group or your company's domain above. People who aren't listed see a sign-in page and then an access-denied message."
        />
      ) : null}

      <ul className="divide-y divide-border">
        {grants.map((g) => {
          const Icon = KIND[g.kind].icon;
          const isOwner = g.role === "owner";
          return (
            <li key={g.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2.5">
              <span className="grid size-7 shrink-0 place-items-center rounded-full bg-surface-2 text-fg-muted">
                <Icon className="size-3.5" aria-label={KIND[g.kind].label} />
              </span>
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium">{g.principal}</div>
                <div className="text-[12px] text-fg-muted">
                  {isOwner
                    ? "Owner"
                    : [KIND[g.kind].label, g.addedBy && `added by ${g.addedBy === owner ? "you" : g.addedBy}`, g.addedAt && `on ${date(g.addedAt)}`]
                        .filter(Boolean)
                        .join(", ")}
                </div>
              </div>
              {isOwner ? (
                <span className="text-[12px] text-fg-muted">Owner</span>
              ) : confirming === g.id ? (
                <div className="flex items-center gap-2">
                  <span className="text-[12px] text-fg-muted">Revoke access now?</span>
                  <Button
                    size="sm"
                    variant="danger"
                    pending={pending}
                    onClick={async () => {
                      if (await apply({ remove: [principalOf(g)] }, `Access revoked for ${g.principal}`)) setConfirming(null);
                    }}
                  >
                    Revoke
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setConfirming(null)}>
                    Cancel
                  </Button>
                </div>
              ) : (
                <div className="flex items-center gap-2">
                  <label htmlFor={`role-${g.id}`} className="sr-only">Role for {g.principal}</label>
                  <Select
                    id={`role-${g.id}`}
                    value={g.role}
                    className="h-7 w-[110px] text-[12px]"
                    disabled={pending}
                    onChange={(e) => {
                      const r = e.target.value === "editor" ? "editor" : "viewer";
                      void apply({ add: [{ principal: principalOf(g), role: r }] }, `${g.principal} can now ${r === "editor" ? "edit" : "view"}`);
                    }}
                  >
                    <option value="viewer">Can view</option>
                    <option value="editor">Can edit</option>
                  </Select>
                  <Button size="sm" variant="ghost" onClick={() => setConfirming(g.id)}>
                    Revoke
                  </Button>
                </div>
              )}
            </li>
          );
        })}
      </ul>
      <div role="status" aria-live="polite" className="min-h-0">
        {toast && (
          <div className="fixed right-4 bottom-[max(16px,env(safe-area-inset-bottom))] z-50 rounded-md border border-border bg-surface px-3 py-2 text-[13px] shadow-pop">
            {toast}
          </div>
        )}
      </div>
    </div>
  );
}
