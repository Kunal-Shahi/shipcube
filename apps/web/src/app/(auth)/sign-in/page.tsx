import type { Metadata } from "next";
import { AuthCard } from "../auth-card";

export const metadata: Metadata = { title: "Sign in" };

export default async function SignInPage({ searchParams }: { searchParams: Promise<{ returnTo?: string }> }) {
  const { returnTo } = await searchParams;
  return <AuthCard intent="sign-in" returnTo={returnTo} />;
}

// Reads auth settings from the environment at request time, not at build time.
export const dynamic = "force-dynamic";
