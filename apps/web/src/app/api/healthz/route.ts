import { NextResponse } from "next/server";
import { config } from "@/lib/server/config";

// Liveness and startup probe for Cloud Run. Fails when configuration is invalid, so a bad
// revision never receives traffic.
export const dynamic = "force-dynamic";

export function GET() {
  try {
    const c = config();
    return NextResponse.json({ status: "ok", auth: c.auth, api: c.apiUrl ? "configured" : "mock" });
  } catch (e) {
    return NextResponse.json({ status: "misconfigured", error: (e as Error).message }, { status: 500 });
  }
}
