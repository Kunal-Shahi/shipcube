import { LogOut } from "lucide-react";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Wordmark } from "@/components/logo";
import { ThemeToggle } from "@/components/theme-toggle";
import { accountStatus } from "@/lib/api";
import { requireSession } from "@/lib/auth";
import { signOut } from "../(auth)/actions";

// Platform pages, outside the console layout: a platform admin may have no workspace of their own,
// and the console layout sends non-members to the waitlist. Everyone who isn't an admin gets a 404.
export default async function AdminLayout({ children }: { children: React.ReactNode }) {
  const session = await requireSession();
  const account = await accountStatus();
  if (!account.platformAdmin) notFound();
  return (
    <div className="min-h-dvh">
      <header className="border-b border-border">
        <div className="mx-auto flex h-14 max-w-[1120px] items-center justify-between gap-4 px-4 sm:px-6">
          <div className="flex items-center gap-4">
            <Link href={account.member ? "/overview" : "/admin/waitlist"} aria-label="ShipCube home">
              <Wordmark />
            </Link>
            <span className="text-fg-muted">Platform</span>
          </div>
          <div className="flex items-center gap-3">
            {account.member && (
              <Link href="/apps" className="text-fg-muted hover:text-fg">
                Back to console
              </Link>
            )}
            <ThemeToggle />
            <span className="hidden text-[12px] text-fg-muted sm:inline">{session.user.email}</span>
            <form action={signOut}>
              <button type="submit" className="grid size-7 place-items-center rounded-md text-fg-subtle hover:bg-surface-2 hover:text-fg" aria-label="Sign out" title="Sign out">
                <LogOut className="size-3.5" aria-hidden />
              </button>
            </form>
          </div>
        </div>
      </header>
      <main id="main" className="mx-auto max-w-[1120px] px-4 py-6 sm:px-6">
        {children}
      </main>
    </div>
  );
}
