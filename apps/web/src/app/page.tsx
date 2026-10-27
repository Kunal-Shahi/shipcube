import { redirect } from "next/navigation";
import { auth } from "@/lib/auth";

export default async function Home() {
  const session = await auth.getSession();
  if (!session) redirect("/sign-in");
  redirect(session.onboarded ? "/apps" : "/onboarding");
}
