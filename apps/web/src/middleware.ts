import { NextResponse, type NextRequest } from "next/server";
import { SESSION_COOKIE } from "@/lib/auth/types";

// Cheap gate: no session cookie means no console. Pages still call requireSession(),
// which is where a real adapter verifies the session.
const PUBLIC = ["/sign-in", "/sign-up"];

export function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;
  if (PUBLIC.some((p) => pathname.startsWith(p))) return NextResponse.next();
  if (!req.cookies.has(SESSION_COOKIE)) {
    const url = req.nextUrl.clone();
    url.pathname = "/sign-in";
    url.search = pathname === "/" ? "" : `?returnTo=${encodeURIComponent(pathname)}`;
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|ico)$).*)"],
};
