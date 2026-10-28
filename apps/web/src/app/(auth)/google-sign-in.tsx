"use client";

import { Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import type { Intent } from "@/lib/auth/types";

// Sign-in opens Google in a popup. Where popups can't work (a popup blocker, an embedded browser,
// or a browser that opens the window in this same tab, where it has no opener to report back to),
// it continues in this tab with signInWithRedirect, and this page finishes it with
// getRedirectResult when Google sends the browser back. Both paths end in the same exchange:
// the ID token is posted to /api/auth/session, which sets the httpOnly session cookie.

export type WebAuthConfig = { apiKey: string; authDomain: string; projectId: string; tenantId?: string; emulatorUrl?: string } | null;

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

/** What a sign-in started in this tab needs after it leaves for Google and comes back. */
const PENDING_KEY = "sc_sign_in";
type Pending = { flow: "popup" | "redirect"; intent: Intent; returnTo?: string; at: number };
const PENDING_TTL_MS = 10 * 60_000;

function readPending(): Pending | null {
  try {
    const p = JSON.parse(sessionStorage.getItem(PENDING_KEY) ?? "null") as Pending | null;
    return p && Date.now() - p.at < PENDING_TTL_MS ? p : null;
  } catch {
    return null;
  }
}
function writePending(p: Pending | null) {
  try {
    if (p) sessionStorage.setItem(PENDING_KEY, JSON.stringify(p));
    else sessionStorage.removeItem(PENDING_KEY);
  } catch {
    // Storage blocked: the redirect path can't resume, but the popup path still works.
  }
}

/** Popups don't come back from in-app browsers (Facebook, Instagram, LINE, Android WebViews). */
function embeddedBrowser() {
  return /FBAN|FBAV|Instagram|Line\/|; wv\)/.test(navigator.userAgent);
}

/** Errors after which only the redirect flow can work. */
const USE_REDIRECT = new Set(["auth/popup-blocked", "auth/operation-not-supported-in-environment"]);

async function firebaseAuth(webConfig: NonNullable<WebAuthConfig>) {
  // Loaded on demand: the Firebase SDK is only needed on this page.
  const [{ initializeApp, getApps }, fa] = await Promise.all([import("firebase/app"), import("firebase/auth")]);
  const app = getApps()[0] ?? initializeApp(webConfig);
  const a = fa.getAuth(app);
  // Local development: the Firebase Auth emulator stands in for Google (AUTH_PROVIDER=firebase-emulator).
  if (webConfig.emulatorUrl && !a.emulatorConfig) fa.connectAuthEmulator(a, webConfig.emulatorUrl, { disableWarnings: true });
  if (webConfig.tenantId) a.tenantId = webConfig.tenantId;
  const provider = new fa.GoogleAuthProvider();
  provider.setCustomParameters({ prompt: "select_account" });
  return { a, fa, provider };
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
  /** Offer "continue in this tab" once a popup has failed. */
  const [offerRedirect, setOfferRedirect] = useState(false);
  /** A popup opened in this tab and never reported back: popups can't work in this browser. */
  const [popupBroken, setPopupBroken] = useState(false);
  const router = useRouter();

  /** Both flows end here: trade the ID token for the server session, then go on. */
  const finish = useCallback(
    async (idToken: string, i: Intent, to?: string) => {
      const res = await fetch("/api/auth/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ idToken, intent: i }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(messageFor(body?.error?.code ?? "", body?.error?.message ?? "") ?? body?.error?.message ?? "Sign-in failed.");
        return;
      }
      const safe = to && to.startsWith("/") && !to.startsWith("//") ? to : "/apps";
      router.replace(body.next === "onboarding" ? "/onboarding" : safe);
      router.refresh();
    },
    [router],
  );

  const fail = useCallback((e: unknown) => {
    const err = e as { code?: string; message?: string };
    setError(messageFor(err.code ?? "", err.message ?? ""));
  }, []);

  // Back from Google after a redirect, or back on this page after a popup that opened in this
  // tab and so could never report back.
  useEffect(() => {
    if (!webConfig) return;
    const resume = async () => {
      const p = readPending();
      if (!p) return;
      writePending(null);
      if (p.flow === "popup") {
        setPending(false);
        setPopupBroken(true);
        setOfferRedirect(true);
        return;
      }
      setPending(true);
      try {
        const { a, fa } = await firebaseAuth(webConfig);
        await fa.setPersistence(a, fa.browserSessionPersistence);
        const cred = await fa.getRedirectResult(a);
        if (!cred) return;
        const idToken = await cred.user.getIdToken();
        await fa.signOut(a);
        await finish(idToken, p.intent, p.returnTo);
      } catch (e) {
        fail(e);
      } finally {
        setPending(false);
      }
    };
    void resume();
    // A page restored from the back-forward cache doesn't re-run effects.
    const onShow = (e: PageTransitionEvent) => e.persisted && void resume();
    window.addEventListener("pageshow", onShow);
    return () => window.removeEventListener("pageshow", onShow);
  }, [webConfig, finish, fail]);

  async function redirect() {
    if (!webConfig) return;
    setPending(true);
    setError(null);
    try {
      const { a, fa, provider } = await firebaseAuth(webConfig);
      // The redirect result has to survive leaving the page; signOut clears it once used.
      await fa.setPersistence(a, fa.browserSessionPersistence);
      writePending({ flow: "redirect", intent, returnTo, at: Date.now() });
      await fa.signInWithRedirect(a, provider);
    } catch (e) {
      writePending(null);
      fail(e);
      setPending(false);
    }
  }

  async function signIn() {
    if (webConfig && (popupBroken || embeddedBrowser())) return redirect();
    setPending(true);
    setError(null);
    try {
      let idToken = "";
      if (webConfig) {
        const { a, fa, provider } = await firebaseAuth(webConfig);
        // The server session is the source of truth; don't keep a second session in the browser.
        await fa.setPersistence(a, fa.inMemoryPersistence);
        // If the popup opens in this tab instead, this page unloads; the marker lets it offer
        // the redirect flow when the person comes back.
        writePending({ flow: "popup", intent, returnTo, at: Date.now() });
        let cred;
        try {
          cred = await fa.signInWithPopup(a, provider);
        } finally {
          writePending(null);
        }
        idToken = await cred.user.getIdToken();
        await fa.signOut(a);
      }
      await finish(idToken, intent, returnTo);
    } catch (e) {
      const code = (e as { code?: string }).code ?? "";
      if (USE_REDIRECT.has(code)) return redirect();
      if (code === "auth/popup-closed-by-user" || code === "auth/cancelled-popup-request") setOfferRedirect(true);
      fail(e);
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
      {offerRedirect && !pending && (
        <p className="text-[12px] text-fg-muted">
          Sign-in window didn&apos;t work?{" "}
          <button type="button" onClick={redirect} className="text-accent hover:underline">
            Continue in this tab
          </button>
        </p>
      )}
      {error && (
        <p role="alert" className="text-[12px] text-critical">
          {error}
        </p>
      )}
    </div>
  );
}
