import type { Metadata } from "next";
import { Clock, XCircle } from "lucide-react";
import { redirect } from "next/navigation";
import { RequestAccessForm } from "@/components/request-access-form";
import { accountStatus } from "@/lib/api";
import { requireSession } from "@/lib/auth";
import { mockDataEnabled } from "@/lib/server/config";
import { date } from "@/lib/format";
import type { AccountStatus, WaitlistState } from "@/lib/types";
import { signOut } from "../actions";

export const metadata: Metadata = { title: "Early access" };
export const dynamic = "force-dynamic";

const PREVIEW: WaitlistState[] = ["none", "pending", "approved", "rejected"];

// Where a signed-in person lands when they aren't in a workspace yet: the console layout and
// onboarding send them here instead of showing errors.
export default async function WaitlistPage({ searchParams }: { searchParams: Promise<{ preview?: string }> }) {
  const session = await requireSession();
  const { preview } = await searchParams;
  // DEV_MODE's mock user is a member; ?preview=<state> shows each state of this page.
  const forced = mockDataEnabled() && PREVIEW.includes(preview as WaitlistState) ? (preview as WaitlistState) : null;
  const account: AccountStatus = forced ? { member: false, platformAdmin: false, waitlist: forced } : await accountStatus();
  if (account.member) redirect("/apps");
  const state = account.waitlist ?? "none";

  return (
    <div className="w-full max-w-[440px]">
      {state === "pending" && (
        <>
          <Clock className="size-5 text-fg-muted" aria-hidden />
          <h1 className="mt-3 text-[20px] leading-7 font-semibold tracking-[-0.01em]">You&apos;re on the waitlist</h1>
          <p className="mt-1.5 text-fg-muted">
            Your request for <span className="font-medium text-fg">{session.user.email}</span> is waiting for review
            {account.requestedAt && <> (sent {date(account.requestedAt)})</>}. Requests are reviewed by hand. Once yours is
            approved, come back and sign in; your workspace will be ready.
          </p>
        </>
      )}
      {state === "approved" && (
        <>
          <h1 className="text-[20px] leading-7 font-semibold tracking-[-0.01em]">Your access is approved</h1>
          <p className="mt-1.5 text-fg-muted">
            Your workspace is being set up. Sign out and sign in again with {session.user.email} to open it.
          </p>
        </>
      )}
      {state === "rejected" && (
        <>
          <XCircle className="size-5 text-fg-muted" aria-hidden />
          <h1 className="mt-3 text-[20px] leading-7 font-semibold tracking-[-0.01em]">Your request wasn&apos;t approved</h1>
          <p className="mt-1.5 text-fg-muted">
            Early access is limited for now, and we couldn&apos;t include {session.user.email} this round.
          </p>
        </>
      )}
      {state === "none" && (
        <>
          <h1 className="text-[20px] leading-7 font-semibold tracking-[-0.01em]">Request access</h1>
          <p className="mt-1.5 text-fg-muted">
            You&apos;re signed in, but {session.user.email} doesn&apos;t have a ShipCube workspace yet. ShipCube is in early
            access; tell us what you&apos;ll build and we&apos;ll review your request.
          </p>
          <div className="mt-6">
            <RequestAccessForm accountEmail={session.user.email} />
          </div>
        </>
      )}

      <form action={signOut} className="mt-8 border-t border-border pt-4 text-[12px] text-fg-muted">
        Signed in as {session.user.email}.{" "}
        <button type="submit" className="text-accent hover:underline">
          Sign out
        </button>
      </form>
    </div>
  );
}
