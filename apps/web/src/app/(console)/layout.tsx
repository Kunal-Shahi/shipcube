import { LogOut } from "lucide-react";
import Link from "next/link";
import { redirect } from "next/navigation";
import { Wordmark } from "@/components/logo";
import { Nav } from "@/components/nav";
import { ThemeToggle } from "@/components/theme-toggle";
import { pendingApprovalCount } from "@/lib/api";
import { requireSession } from "@/lib/auth";
import { signOut } from "../(auth)/actions";

export default async function ConsoleLayout({ children }: { children: React.ReactNode }) {
  const session = await requireSession();
  if (!session.onboarded) redirect("/onboarding");
  const { user } = session;
  const initials = user.name
    .split(" ")
    .map((p) => p[0])
    .join("")
    .slice(0, 2);

  return (
    <div className="md:grid md:min-h-dvh md:grid-cols-[224px_1fr]">
      <aside className="sticky top-0 z-20 flex flex-col gap-3 border-b border-border bg-bg px-4 py-3 md:h-dvh md:gap-6 md:border-r md:border-b-0 md:px-3 md:py-4">
        <div className="flex items-center justify-between md:px-2">
          <Link href="/apps" aria-label="ShipCube home">
            <Wordmark />
          </Link>
          <div className="md:hidden">
            <ThemeToggle />
          </div>
        </div>
        <div className="md:flex-1">
          <Nav pendingApprovals={(await pendingApprovalCount()) ?? 0} />
        </div>
        <div className="hidden flex-col gap-3 md:flex">
          <div className="px-2">
            <ThemeToggle />
          </div>
          <div className="flex items-center gap-2 border-t border-border px-2 pt-3">
            <span className="grid size-7 shrink-0 place-items-center rounded-full bg-surface-2 text-[11px] font-semibold text-fg-muted">
              {initials}
            </span>
            <div className="min-w-0 flex-1">
              <div className="truncate text-[12px] font-medium">{user.name}</div>
              <div className="truncate text-[12px] text-fg-muted">{user.email}</div>
            </div>
            <form action={signOut}>
              <button
                type="submit"
                className="grid size-7 place-items-center rounded-md text-fg-subtle hover:bg-surface-2 hover:text-fg"
                aria-label="Sign out"
                title="Sign out"
              >
                <LogOut className="size-3.5" aria-hidden />
              </button>
            </form>
          </div>
        </div>
      </aside>
      <main className="min-w-0 px-4 py-6 sm:px-6 md:py-8">
        <div className="mx-auto max-w-[1120px]">{children}</div>
      </main>
    </div>
  );
}
