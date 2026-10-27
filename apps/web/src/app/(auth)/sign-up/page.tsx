import type { Metadata } from "next";
import { AuthCard } from "../auth-card";

export const metadata: Metadata = { title: "Create account" };

export default function SignUpPage() {
  return <AuthCard intent="sign-up" />;
}

// Reads auth settings from the environment at request time, not at build time.
export const dynamic = "force-dynamic";
