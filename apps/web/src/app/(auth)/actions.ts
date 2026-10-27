"use server";

import { redirect } from "next/navigation";
import { auth } from "@/lib/auth";

export async function signOut() {
  await auth.signOut();
  redirect("/sign-in");
}
