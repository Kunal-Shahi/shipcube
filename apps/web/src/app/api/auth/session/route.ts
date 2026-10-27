import { NextResponse, type NextRequest } from "next/server";
import { auth } from "@/lib/auth";
import { AuthError } from "@/lib/auth/types";
import { log } from "@/lib/server/log";

// POST { idToken, intent } → sets the httpOnly session cookie. Called only by the sign-in
// page on this origin, so cross-site requests are refused (CSRF).

export async function POST(req: NextRequest) {
  const origin = req.headers.get("origin");
  const host = req.headers.get("x-forwarded-host") ?? req.headers.get("host");
  if (!origin || !host || new URL(origin).host !== host) {
    return NextResponse.json({ error: { code: "forbidden", message: "Cross-site request refused." } }, { status: 403 });
  }

  let body: { idToken?: unknown; intent?: unknown };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: { code: "invalid_argument", message: "Expected JSON." } }, { status: 400 });
  }
  const idToken = typeof body.idToken === "string" ? body.idToken : "";
  const intent = body.intent === "sign-up" ? "sign-up" : "sign-in";
  if (auth.id !== "mock" && (!idToken || idToken.length > 8192)) {
    return NextResponse.json({ error: { code: "invalid_argument", message: "Missing ID token." } }, { status: 400 });
  }

  try {
    const { next } = await auth.createSession(idToken, intent);
    return NextResponse.json({ next });
  } catch (e) {
    if (e instanceof AuthError) {
      return NextResponse.json({ error: { code: e.code, message: e.message } }, { status: e.code === "not_invited" ? 403 : 401 });
    }
    log("ERROR", "session creation failed", { error: String(e) });
    return NextResponse.json({ error: { code: "internal", message: "Sign-in failed on our side. Try again." } }, { status: 500 });
  }
}
