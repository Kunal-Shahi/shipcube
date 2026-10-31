import { Compass, LogOut } from "lucide-react";
import { Suspense } from "react";
import Link from "next/link";
import { redirect } from "next/navigation";
import { Wordmark } from "@/components/logo";
import { Nav } from "@/components/nav";
import { ThemeToggle } from "@/components/theme-toggle";
import { Tour } from "@/components/tour";
import { accountStatus, pendingApprovalCount } from "@/lib/api";
import { requireSession } from "@/lib/auth";
import { signOut } from "../(auth)/actions";

export default async function ConsoleLayout({ children }: { children: React.ReactNode }) {
  const session = await requireSession();
  // Signed in but not in a workspace yet: the waitlist page, not a console full of errors. If the
  // API itself is failing, carry on: each page shows its own error state with a retry, and the
  // API still enforces membership and admin rights.
  const account = await accountStatus().catch(() => ({ member: true, platformAdmin: false }));
  if (!account.member) redirect("/waitlist");
  if (!session.onboarded) redirect("/onboarding");
  const { user } = session;
  const initials = user.name
    .split(" ")
    .map((p) => p[0])
    .join("")
    .slice(0, 2);

  return (
    <div className="md:grid md:min-h-dvh md:grid-cols-[224px_1fr]">
      <a href="#main" className="sr-only-focusable rounded-md bg-accent px-3 py-1.5 font-medium text-accent-fg">
        Skip to content
      </a>
      {/* The sidebar is blueprint paper, as on the landing page, in both themes. */}
      <aside className="console-rail blueprint sticky top-0 z-20 flex flex-col gap-3 px-4 py-3 md:h-dvh md:gap-6 md:px-3 md:py-4">
        <div className="flex items-center justify-between md:px-2">
          <Link href="/overview" aria-label="ShipCube home">
            <Wordmark />
          </Link>
          <div className="md:hidden">
            <ThemeToggle />
          </div>
        </div>
        <div className="md:flex-1">
          <Nav pendingApprovals={(await pendingApprovalCount()) ?? 0} platformAdmin={account.platformAdmin} />
        </div>
        <div className="hidden flex-col gap-3 md:flex">
          <Link
            href="/overview?tour=1"
            className="flex h-8 items-center gap-2 rounded-md px-2 text-[13px] text-fg-muted hover:bg-surface-2 hover:text-fg"
          >
            <Compass className="size-4 text-fg-subtle" aria-hidden />
            Take the tour
          </Link>
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
      <main id="main" tabIndex={-1} className="min-w-0 px-4 py-6 focus:outline-none sm:px-6 md:py-8">
        <div className="mx-auto max-w-[1120px]">{children}</div>
      </main>
      {/* The first-run tour: reads the URL, so it sits in its own Suspense boundary. */}
      <Suspense fallback={null}>
        <Tour />
      </Suspense>
    </div>
  );
}
