"use server";

import { redirect } from "next/navigation";
import { auth } from "@/lib/auth";

export async function signInWithGoogle(formData: FormData) {
  const intent = formData.get("intent") === "sign-up" ? "sign-up" : "sign-in";
  const returnTo = formData.get("returnTo");
  const safeReturn = typeof returnTo === "string" && returnTo.startsWith("/") && !returnTo.startsWith("//") ? returnTo : undefined;
  const { redirectTo } = await auth.signInWithGoogle({ intent, returnTo: safeReturn });
  redirect(redirectTo);
}

export async function signOut() {
  await auth.signOut();
  redirect("/sign-in");
}
