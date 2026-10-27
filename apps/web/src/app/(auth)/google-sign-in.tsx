"use client";

import { Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import type { Intent } from "@/lib/auth/types";

export type WebAuthConfig = { apiKey: string; authDomain: string; projectId: string; tenantId?: string } | null;

function GoogleG() {
  return (
    <svg viewBox="0 0 18 18" className="size-4" aria-hidden>
      <path fill="#4285F4" d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.92c1.7-1.57 2.68-3.88 2.68-6.62Z" />
      <path fill="#34A853" d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.92-2.26c-.8.54-1.84.86-3.04.86-2.34 0-4.32-1.58-5.03-3.7H.96v2.33A9 9 0 0 0 9 18Z" />
      <path fill="#FBBC05" d="M3.97 10.72A5.41 5.41 0 0 1 3.68 9c0-.6.1-1.18.29-1.72V4.95H.96A9 9 0 0 0 0 9c0 1.45.35 2.83.96 4.05l3.01-2.33Z" />
      <path fill="#EA4335" d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.58A9 9 0 0 0 .96 4.95l3.01 2.33C4.68 5.16 6.66 3.58 9 3.58Z" />
    </svg>
  );
}

// Messages for the Firebase Auth errors a person can actually hit.
function messageFor(code: string, raw: string): string | null {
  if (code === "auth/popup-closed-by-user" || code === "auth/cancelled-popup-request") return null;
  if (code === "auth/popup-blocked") return "Your browser blocked the Google sign-in window. Allow pop-ups for this site and try again.";
  if (code === "auth/network-request-failed") return "Couldn't reach Google. Check your connection and try again.";
  if (raw.includes("BLOCKING_FUNCTION") || code === "auth/admin-restricted-operation" || code === "not_invited") {
    return "ShipCube is invite-only for now, and this Google account isn't on the list. Use the account your invite was sent to, or ask the person who invited you.";
  }
  if (code === "auth/unauthorized-domain") return "Sign-in isn't set up for this address yet. An operator needs to add it to Identity Platform's authorized domains.";
  return "Google sign-in didn't complete. Try again.";
}

export function GoogleSignIn({
  label,
  intent,
  returnTo,
  webConfig,
}: {
  label: string;
  intent: Intent;
  returnTo?: string;
  webConfig: WebAuthConfig;
}) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const router = useRouter();

  async function signIn() {
    setPending(true);
    setError(null);
    try {
      let idToken = "";
      if (webConfig) {
        // Loaded on demand: the Firebase SDK is only needed on this page.
        const [{ initializeApp, getApps }, fa] = await Promise.all([import("firebase/app"), import("firebase/auth")]);
        const app = getApps()[0] ?? initializeApp(webConfig);
        const a = fa.getAuth(app);
        if (webConfig.tenantId) a.tenantId = webConfig.tenantId;
        // The server session is the source of truth; don't keep a second session in the browser.
        await fa.setPersistence(a, fa.inMemoryPersistence);
        const provider = new fa.GoogleAuthProvider();
        provider.setCustomParameters({ prompt: "select_account" });
        const cred = await fa.signInWithPopup(a, provider);
        idToken = await cred.user.getIdToken();
        await fa.signOut(a);
      }
      const res = await fetch("/api/auth/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ idToken, intent }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(messageFor(body?.error?.code ?? "", body?.error?.message ?? "") ?? body?.error?.message ?? "Sign-in failed.");
        return;
      }
      const safe = returnTo && returnTo.startsWith("/") && !returnTo.startsWith("//") ? returnTo : "/apps";
      router.replace(body.next === "onboarding" ? "/onboarding" : safe);
      router.refresh();
    } catch (e) {
      const err = e as { code?: string; message?: string };
      setError(messageFor(err.code ?? "", err.message ?? ""));
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <button
        type="button"
        onClick={signIn}
        disabled={pending}
        className="flex h-9 w-full items-center justify-center gap-2 rounded-md border border-border-strong bg-surface text-[13px] font-medium hover:bg-surface-2 disabled:opacity-60"
      >
        {pending ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <GoogleG />}
        {pending ? "Waiting for Google…" : label}
      </button>
      {error && (
        <p role="alert" className="text-[12px] text-critical">
          {error}
        </p>
      )}
    </div>
  );
}
