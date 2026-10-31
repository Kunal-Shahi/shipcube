import { NextResponse, type NextRequest } from "next/server";
import { SESSION_COOKIE } from "@/lib/auth/types";
import { emulatorPublicUrl, publicApiUrl } from "@/lib/server/config";

// Runs on every page request:
// 1. A per-request nonce and Content-Security-Policy (Next applies the nonce to its own scripts).
// 2. A cheap gate: no session cookie means no console. Pages still verify the session itself.

// Pages anyone may open, matched exactly: the landing page and its link-preview image. (Its
// "Request access" form posts from the browser straight to the API.) Everything else needs a session.
const PUBLIC_EXACT = ["/", "/opengraph-image", "/terms", "/privacy"];
const PUBLIC = ["/sign-in", "/sign-up", "/api/auth/session", "/api/healthz"];

export function isPublic(pathname: string) {
  return PUBLIC_EXACT.includes(pathname) || PUBLIC.some((p) => pathname === p || pathname.startsWith(p + "/"));
}

export function csp(nonce: string, dev: boolean) {
  const authDomain = process.env.FIREBASE_AUTH_DOMAIN;
  // Local only (config() refuses it elsewhere): the Firebase Auth emulator serves the sign-in popup and token API.
  const emulator = process.env.AUTH_PROVIDER === "firebase-emulator" ? emulatorPublicUrl(process.env) : null;
  // The landing page's waitlist form posts from the browser straight to the API (its rate limit is per visitor IP).
  const apiOrigin = (() => {
    try {
      const u = publicApiUrl(process.env);
      return u ? new URL(u).origin : null;
    } catch {
      return null;
    }
  })();
  // Sign-in frames, by exact host only (no *.web.app or *.firebaseapp.com, which any Firebase customer can
  // serve from): the auth domain (shipcube.web.app, FIREBASE_AUTH_DOMAIN), the project's own
  // firebaseapp.com (Firebase's default auth host), Google's account chooser, and the local emulator.
  const project = process.env.IDENTITY_PLATFORM_PROJECT || process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID;
  const hostOk = (h: string | undefined) => !!h && /^[a-z0-9.-]+$/i.test(h);
  const frames = [
    hostOk(authDomain) && `https://${authDomain}`,
    hostOk(project) && `https://${project}.firebaseapp.com`,
    "https://accounts.google.com",
    emulator,
  ]
    .filter(Boolean)
    .join(" ");
  return [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic' https://apis.google.com${dev ? " 'unsafe-eval'" : ""}`,
    // Inline style attributes (chart sizes, meters) need 'unsafe-inline'; scripts never do.
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: https://lh3.googleusercontent.com",
    "font-src 'self'",
    "connect-src 'self' https://identitytoolkit.googleapis.com https://securetoken.googleapis.com https://www.googleapis.com" + (dev ? " ws:" : "") + (emulator ? ` ${emulator}` : "") + (apiOrigin ? ` ${apiOrigin}` : ""),
    `frame-src ${frames}`,
    "frame-ancestors 'none'",
    "form-action 'self'",
    "base-uri 'none'",
    "object-src 'none'",
    ...(dev || emulator ? [] : ["upgrade-insecure-requests"]),
  ].join("; ");
}

export function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;

  if (!isPublic(pathname) && !req.cookies.has(SESSION_COOKIE)) {
    const url = req.nextUrl.clone();
    url.pathname = "/sign-in";
    url.search = `?returnTo=${encodeURIComponent(pathname + req.nextUrl.search)}`;
    return NextResponse.redirect(url);
  }

  const nonce = btoa(crypto.randomUUID());
  const policy = csp(nonce, process.env.NODE_ENV === "development");
  const headers = new Headers(req.headers);
  headers.set("x-nonce", nonce);
  headers.set("content-security-policy", policy);
  const res = NextResponse.next({ request: { headers } });
  res.headers.set("Content-Security-Policy", policy);
  return res;
}

export const config = {
  matcher: [
    {
      source: "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|ico|woff2)$).*)",
      missing: [{ type: "header", key: "next-router-prefetch" }],
    },
  ],
};
