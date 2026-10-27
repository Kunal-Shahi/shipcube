import { NextResponse, type NextRequest } from "next/server";
import { SESSION_COOKIE } from "@/lib/auth/types";

// Runs on every page request:
// 1. A per-request nonce and Content-Security-Policy (Next applies the nonce to its own scripts).
// 2. A cheap gate: no session cookie means no console. Pages still verify the session itself.

const PUBLIC = ["/sign-in", "/sign-up", "/api/auth/session", "/api/healthz"];

export function csp(nonce: string, dev: boolean) {
  const authDomain = process.env.FIREBASE_AUTH_DOMAIN;
  const frames = ["https://*.firebaseapp.com", "https://*.web.app", "https://accounts.google.com", authDomain && `https://${authDomain}`]
    .filter(Boolean)
    .join(" ");
  return [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic' https://apis.google.com${dev ? " 'unsafe-eval'" : ""}`,
    // Inline style attributes (chart sizes, meters) need 'unsafe-inline'; scripts never do.
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: https://lh3.googleusercontent.com",
    "font-src 'self'",
    "connect-src 'self' https://identitytoolkit.googleapis.com https://securetoken.googleapis.com https://www.googleapis.com" + (dev ? " ws:" : ""),
    `frame-src ${frames}`,
    "frame-ancestors 'none'",
    "form-action 'self'",
    "base-uri 'none'",
    "object-src 'none'",
    ...(dev ? [] : ["upgrade-insecure-requests"]),
  ].join("; ");
}

export function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;

  if (!PUBLIC.some((p) => pathname === p || pathname.startsWith(p + "/")) && !req.cookies.has(SESSION_COOKIE)) {
    const url = req.nextUrl.clone();
    url.pathname = "/sign-in";
    url.search = pathname === "/" ? "" : `?returnTo=${encodeURIComponent(pathname + req.nextUrl.search)}`;
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
