"use server";

import { redirect } from "next/navigation";
import { auth } from "@/lib/auth";

export async function finishOnboarding() {
  await auth.completeOnboarding();
  redirect("/setup?welcome=1");
}
