import { redirect } from "next/navigation";
import { mockAuth } from "./mock";
import type { AuthProvider, Session } from "./types";

// Pick the adapter from AUTH_PROVIDER. Only "mock" exists until the security
// workstream lands; adding Auth.js or Firebase means adding a file next to mock.ts.
const adapters: Record<string, AuthProvider> = { mock: mockAuth };

export const auth: AuthProvider = adapters[process.env.AUTH_PROVIDER ?? "mock"] ?? mockAuth;

export async function requireSession(): Promise<Session> {
  const session = await auth.getSession();
  if (!session) redirect("/sign-in");
  return session;
}

export type { AuthProvider, Session, User } from "./types";
