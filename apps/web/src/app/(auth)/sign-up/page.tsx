import type { Metadata } from "next";
import { AuthCard } from "../auth-card";

export const metadata: Metadata = { title: "Create account" };

export default function SignUpPage() {
  return <AuthCard intent="sign-up" />;
}
